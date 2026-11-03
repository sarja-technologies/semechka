"""The Work Mode runtime layer: the in-container helper (run here by the host python in a temp directory through the test
double), and the exact Docker command lines DockerRuntime builds (against a recording stand-in for the docker CLI)."""
import subprocess
import threading
import time

import pytest

from semechka import sandbox_svc, work_runtime
from semechka.work_runtime import ContainerSpec, DockerRuntime, LocalDirRuntime, PathRefused, RuntimeError_

SID = "ws_0123456789abcdef0123"


@pytest.fixture
def rt(tmp_path):
    r = LocalDirRuntime(tmp_path / "rt")
    r.create(ContainerSpec(SID))
    r.name = work_runtime.container_name(SID)
    return r


# ---- the helper: paths ---------------------------------------------------------------------------------------------
def test_files_round_trip_atomically_and_listing_is_ordered(rt):
    head = rt.put_file(rt.name, "a/b/c.txt", b"hello")
    assert head["size"] == 5 and len(head["sha256"]) == 64
    assert rt.get_file(rt.name, "a/b/c.txt")[0] == b"hello"
    assert rt.get_file(rt.name, "a/b/c.txt", offset=1, limit=3)[0] == b"ell"
    rt.put_file(rt.name, "a/b/c.txt", b"over")           # replaced, never appended
    assert rt.get_file(rt.name, "a/b/c.txt")[0] == b"over"
    rt.put_file(rt.name, "z.txt", b"1")
    assert [(e.name, e.type) for e in rt.list(rt.name, "")] == [(".home", "dir"), ("a", "dir"), ("z.txt", "file")]
    assert [f.name for f in rt.list(rt.name, "a/b")] == ["c.txt"]
    assert not [n for n in __import__("os").listdir(rt._dir(rt.name) / "a" / "b") if n.startswith(".wtmp")]  # no temp file left
    with pytest.raises(PathRefused):
        rt.put_file(rt.name, "z.txt", b"2", create_only=True)
    assert rt.stat(rt.name, "nope")["exists"] is False
    assert rt.mkdir(rt.name, "m/n") is None and rt.stat(rt.name, "m/n")["type"] == "dir"


@pytest.mark.parametrize("path", ["../x", "a/../../x", "a/../..", "\x00"])
def test_lexical_escapes_are_refused(rt, path):
    for op in (lambda: rt.get_file(rt.name, path), lambda: rt.put_file(rt.name, path, b"x"), lambda: rt.list(rt.name, path),
               lambda: rt.delete(rt.name, path)):
        with pytest.raises(PathRefused):
            op()


def test_a_symlink_that_leaves_the_workspace_is_refused_for_every_operation(rt, tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "f").write_text("SECRET")
    (rt._dir(rt.name) / "dirlink").symlink_to(out)
    (rt._dir(rt.name) / "filelink").symlink_to(out / "f")
    (rt._dir(rt.name) / "dangling").symlink_to(out / "new")
    for p in ("dirlink/f", "filelink", "dirlink"):
        with pytest.raises(PathRefused, match="symlink escape"):
            rt.get_file(rt.name, p)
    for p in ("dirlink/new", "filelink", "dangling"):
        with pytest.raises(PathRefused, match="symlink escape"):
            rt.put_file(rt.name, p, b"x")
    with pytest.raises(PathRefused):
        rt.list(rt.name, "dirlink")
    assert rt.search(rt.name, "SECRET")["matches"] == []          # searches do not follow links either
    assert (out / "f").read_text() == "SECRET" and not (out / "new").exists()
    # a link whose target stays inside is just a path
    (rt._dir(rt.name) / "real.txt").write_text("ok")
    (rt._dir(rt.name) / "okl").symlink_to("real.txt")
    assert rt.get_file(rt.name, "okl")[0] == b"ok"
    # deleting a link removes the link
    assert rt.delete(rt.name, "dirlink") == 1 and out.exists() and (out / "f").exists()


def test_the_workspace_root_cannot_be_deleted_and_directories_cannot_be_overwritten(rt):
    rt.put_file(rt.name, "d/f", b"x")
    with pytest.raises(PathRefused, match="root"):
        rt.delete(rt.name, "")
    with pytest.raises(PathRefused, match="directory"):
        rt.put_file(rt.name, "d", b"x")
    with pytest.raises(PathRefused, match="directory"):
        rt.get_file(rt.name, "d")
    assert rt.delete(rt.name, "d") == 2
    assert rt.delete(rt.name, "", allow_root=True) >= 1 and [e.name for e in rt.list(rt.name, "")] == []


def test_search_skips_vcs_binaries_and_reports_lines(rt):
    rt.put_file(rt.name, "src/a.py", b"def main():\n    pass\n")
    rt.put_file(rt.name, ".git/config", b"def main")
    rt.put_file(rt.name, "node_modules/x.js", b"def main")
    rt.put_file(rt.name, "bin.dat", b"\x00\x01def main")
    r = rt.search(rt.name, r"def\s+main", "")
    assert r["matches"] == [{"path": "src/a.py", "line": 1, "text": "def main():"}]
    assert [m["path"] for m in rt.search(rt.name, "", "", names_only=True, glob="*.py")["matches"]] == ["src/a.py"]
    with pytest.raises(PathRefused, match="bad pattern"):
        rt.search(rt.name, "(")


def test_usage_counts_the_workspace(rt):
    base = rt.usage_bytes(rt.name)
    rt.put_file(rt.name, "big", b"0" * 100000)
    assert rt.usage_bytes(rt.name) - base >= 100000


# ---- the helper: commands -------------------------------------------------------------------------------------------
def test_exec_reports_exit_code_duration_output_and_signals(rt):
    r = rt.exec(rt.name, "echo out; echo err >&2; exit 7")
    assert (r.exit, r.stdout, r.stderr, r.timed_out) == (7, "out\n", "err\n", False) and 0 <= r.duration_s < 5
    rt.mkdir(rt.name, "sub")
    r = rt.exec(rt.name, "pwd", cwd="sub")
    assert r.stdout.strip().endswith("/sub") and r.exit == 0
    with pytest.raises(PathRefused):
        rt.exec(rt.name, "pwd", cwd="../..")
    r = rt.exec(rt.name, "kill -9 $$")
    assert r.exit == -9 and r.signal == "SIGKILL"
    r = rt.exec(rt.name, argv=["printf", "%s", "no shell: $HOME"])
    assert r.stdout == "no shell: $HOME"


def test_exec_timeout_kills_the_whole_process_group(rt):
    t0 = time.time()
    r = rt.exec(rt.name, "(sleep 41.3 &); sleep 41.3", timeout_s=1)
    assert r.timed_out and time.time() - t0 < 10 and r.exit < 0
    assert rt.exec(rt.name, "pgrep -fc '[s]leep 41.3' || true").stdout.strip() == "0"


def test_exec_output_keeps_head_and_tail_and_counts_what_it_dropped(rt):
    r = rt.exec(rt.name, "python3 -c \"print('H' * 50 + 'x' * 1000000 + 'T' * 50)\"", max_output=1000)
    assert r.stdout.startswith("HHHH") and r.stdout.rstrip().endswith("TTTT") and len(r.stdout) <= 1001
    assert r.truncated_stdout > 900000 and r.truncated_stderr == 0


def test_exec_environment_is_built_not_inherited(rt, monkeypatch):
    monkeypatch.setenv("SEMECHKA_TOKEN", "hunter2")
    out = rt.exec(rt.name, "env", env={"GOOD_ONE": "1", "bad-name": "x", "lower": "y"}).stdout
    names = {ln.split("=", 1)[0] for ln in out.splitlines()}
    assert "SEMECHKA_TOKEN" not in names and "hunter2" not in out and "GOOD_ONE" in names and "bad-name" not in names and "lower" not in names
    assert {"HOME", "PATH", "LANG", "GIT_TERMINAL_PROMPT"} <= names


def test_exec_file_size_limit_and_quota_watchdog(rt):
    r = rt.exec(rt.name, "head -c 3000000 /dev/zero > big; echo $?", fsize_mb=1)
    assert (rt._dir(rt.name) / "big").stat().st_size == 1048576 and r.exit == 0   # RLIMIT_FSIZE stops a single file
    r = rt.exec(rt.name, "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do head -c 1000000 /dev/zero >> grow; sleep 0.5; done", quota_bytes=3_000_000, timeout_s=30)
    assert r.quota_killed and not r.timed_out and r.duration_s < 12


def test_interrupt_ends_a_running_command_and_says_so(rt):
    out = {}
    th = threading.Thread(target=lambda: out.update(r=rt.exec(rt.name, "sleep 50", timeout_s=60)))
    th.start()
    time.sleep(0.8)
    rt.interrupt(rt.name)
    th.join(15)
    assert not th.is_alive() and out["r"].interrupted is True and out["r"].exit < 0


def test_a_stopped_container_refuses_work_until_started(rt):
    rt.stop(rt.name)
    with pytest.raises(RuntimeError_, match="not running"):
        rt.exec(rt.name, "true")
    rt.start(rt.name)
    assert rt.exec(rt.name, "true").exit == 0


def test_reconcile_removes_only_orphans_and_reports_what_is_missing(tmp_path):
    r = LocalDirRuntime(tmp_path / "rt")
    mine, orphan = ContainerSpec("ws_aaaaaaaaaaaaaaaaaaaa"), ContainerSpec("ws_bbbbbbbbbbbbbbbbbbbb")
    r.create(mine)
    r.create(orphan)
    (r.base / "not-a-work-thing").mkdir()
    rep = r.reconcile("inst", {"ws_aaaaaaaaaaaaaaaaaaaa": mine.name, "ws_gone": "work-ffffffffffff"})
    assert rep["removed"] == [orphan.name] and rep["absent"] == ["ws_gone"] and rep["states"] == {"ws_aaaaaaaaaaaaaaaaaaaa": "running"}
    assert r.state(mine.name) == "running" and r.state(orphan.name) == "absent" and (r.base / "not-a-work-thing").exists()


# ---- DockerRuntime: the exact command lines ------------------------------------------------------------------------------
def flags(cmd):
    return {(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1)}


def test_the_container_is_created_with_exactly_the_designed_isolation():
    spec = ContainerSpec(SID, image="semechka-work:1", memory_mb=4096, cpus=2, pids=256, network="none", instance="inst1")
    cmd = DockerRuntime("unix:///x.sock").run_command(spec)
    f = flags(cmd)
    for pair in [("--network", "none"), ("--user", "65534:65534"), ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges:true"),
                 ("--pids-limit", "256"), ("--memory", "4096m"), ("--memory-swap", "4096m"), ("--cpus", "2.0"), ("--log-driver", "none"),
                 ("--pull", "never"), ("--label", f"semechka.work={SID}"), ("--label", "semechka.work.instance=inst1"),
                 ("-v", f"{spec.volume}:/work"), ("-e", "HOME=/work/.home"), ("--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777")]:
        assert pair in f, pair
    assert {"--read-only", "--init", "-d"} <= set(cmd) and cmd[-1] == "semechka-work:1" and cmd[0] == "run"
    assert spec.name == spec.volume and spec.name.startswith("work-")
    joined = " ".join(cmd)
    for bad in ("--privileged", "docker.sock", "--network host", "--cap-add", "--pid ", "--ipc", "--device", "-v /", "--mount", "/home"):
        assert bad not in joined, bad
    assert sum(1 for x in cmd if x == "-v") == 1   # the one named volume; no host mounts
    net = DockerRuntime("unix:///x.sock").run_command(ContainerSpec(SID, network="internet", network_name="mynet"))
    assert ("--network", "mynet") in flags(net)


class FakeDocker:
    """Stands in for `docker`: records every call, answers from a tiny state."""

    def __init__(self):
        self.calls, self.containers, self.volumes = [], {}, set()

    def __call__(self, host, *args, timeout=20, input=None):
        self.calls.append(args)
        out, rc = b"", 0
        if args[0] == "inspect":
            assert args[1:3] == ("--type", "container")   # the volume has the same name: inspect must say which it means
            name = args[-1]
            if name not in self.containers:
                return subprocess.CompletedProcess(args, 1, b"", b"Error: No such object: " + name.encode())
            out = (self.containers[name] + "\n").encode()
        elif args[0] == "volume" and args[1] == "create":
            self.volumes.add(args[-1])
        elif args[0] == "run":
            self.containers[args[args.index("--name") + 1]] = "running"
        elif args[0] == "rm":
            self.containers.pop(args[-1], None)
        elif args[0] == "volume" and args[1] == "rm":
            self.volumes.discard(args[-1])
        elif args[0] == "stop":
            self.containers[args[-1]] = "exited"
        elif args[0] == "start":
            self.containers[args[-1]] = "running"
        elif args[0] == "ps":
            out = b"work-0123456789abcdef0123|ws_x|running\nsmx-sandbox|1|running\nwork-zz|bad|exited\n"
        elif args[0] == "volume" and args[1] == "ls":
            out = b"work-0123456789abcdef0123|ws_x\nwork-aaaaaaaaaaaaaaaa|ws_y\n"
        return subprocess.CompletedProcess(args, rc, out, b"")


def test_docker_runtime_sequences_and_safety(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(sandbox_svc, "_docker", fake)
    d = DockerRuntime("unix:///x.sock")
    spec = ContainerSpec(SID, instance="inst1")
    assert d.state(spec.name) == "absent"
    d.create(spec)
    assert [c[0] if c[0] != "volume" else "volume " + c[1] for c in fake.calls][-3:] == ["inspect", "volume create", "run"]
    assert d.state(spec.name) == "running"
    d.create(spec)                                  # adopting: nothing new is created
    assert [c[0] for c in fake.calls].count("run") == 1
    d.stop(spec.name)
    assert d.state(spec.name) == "stopped"
    d.create(spec)                                  # a stopped container is started again, not recreated
    assert fake.containers[spec.name] == "running" and [c[0] for c in fake.calls].count("run") == 1
    d.remove(spec.name)
    assert spec.name not in fake.containers and spec.volume not in fake.volumes
    with pytest.raises(RuntimeError_, match="not a Work Mode container"):
        d.remove("smx-someone-elses")
    with pytest.raises(RuntimeError_):
        d.remove("work-x; rm -rf /")
    # only this install's containers are ever listed, and only work-<id> names
    got = d.list_managed("inst1")
    ps = [c for c in fake.calls if c[0] == "ps"][-1]
    assert "label=semechka.work.instance=inst1" in ps and "label=semechka.work" in ps
    assert sorted(m["name"] for m in got) == ["work-0123456789abcdef0123", "work-aaaaaaaaaaaaaaaa"]
    assert [m for m in got if m["name"] == "work-aaaaaaaaaaaaaaaa"][0]["volume_only"] is True


def test_docker_exec_goes_through_the_helper_with_a_fixed_argument_list(monkeypatch):
    seen = {}

    def fake(host, *args, timeout=20, input=None):
        seen["args"], seen["input"] = args, input
        return subprocess.CompletedProcess(args, 0, b'{"ok": true, "exit": 0, "stdout": "", "stderr": "", "duration_s": 0.1, "timed_out": false, '
                                                    b'"quota_killed": false, "oom_killed": false, "interrupted": false, "truncated_stdout": 0, '
                                                    b'"truncated_stderr": 0, "nbytes": 0}\n', b"")
    monkeypatch.setattr(sandbox_svc, "_docker", fake)
    d = DockerRuntime("unix:///x.sock")
    d.exec("work-0123456789abcdef0123", "echo $(rm -rf /); `evil` 'x'", timeout_s=5)
    a = seen["args"]
    assert a[:4] == ("exec", "-i", "work-0123456789abcdef0123", "python3") and a[4:6] == ("-I", "-c")
    assert "evil" not in " ".join(a)                       # model text is never part of the host argument list...
    assert b"echo $(rm -rf /)" in seen["input"]            # ...it travels as data on stdin and is run by bash INSIDE the container
