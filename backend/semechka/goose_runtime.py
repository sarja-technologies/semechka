"""Container runtime for Work Mode on Goose.

Re-uses the Work Mode container facilities (`work_runtime.DockerRuntime`: the unprivileged daemon of account `semsbx`
reached through `semechka-sandbox-relay`, the in-container file helper, volumes) and changes exactly what a Goose session
needs:

  * its own container name prefix (`gwork-`) and label (`semechka.goose`), so the older Work Mode reconciler never mistakes
    these containers for its orphans and vice versa
  * the image that carries the upstream Goose binary
  * `--network none` ALWAYS: the only way out is the per-session unix socket directory mounted at /run/llm
  * `goose acp` is started with `docker exec -i`, and its stdin/stdout are the ACP transport
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import sandbox_svc
from .work_runtime import WORK_ROOT, ContainerSpec, DockerRuntime, RuntimeError_

GNAME_RE = re.compile(r"^gwork-[A-Za-z0-9]{8,40}$")
SOCK_MOUNT = "/run/llm"
GOOSE_ROOT = f"{WORK_ROOT}/.home/goose"


def container_name(wid: str) -> str:
    return "gwork-" + re.sub(r"[^A-Za-z0-9]", "", wid.split("_", 1)[-1])


@dataclass
class GooseSpec(ContainerSpec):
    sock_dir: str = ""   # host directory holding llm.sock; mounted read-write at /run/llm

    @property
    def name(self) -> str:  # type: ignore[override]
        return container_name(self.session_id)


class GooseRuntime(DockerRuntime):
    def run_command(self, spec: GooseSpec) -> list[str]:  # type: ignore[override]
        mem = f"{int(spec.memory_mb)}m"
        return [
            "run", "-d", "--pull", "never", "--name", spec.name,
            "--label", f"semechka.goose={spec.session_id}", "--label", f"semechka.goose.instance={spec.instance}",
            "--network", "none", "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={int(spec.tmp_mb)}m,mode=1777",
            "-v", f"{spec.volume}:{WORK_ROOT}",
            "-v", f"{spec.sock_dir}:{SOCK_MOUNT}",
            "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(int(spec.pids)), "--memory", mem, "--memory-swap", mem, "--cpus", str(float(spec.cpus)),
            "--ulimit", "nofile=4096:4096", "--ulimit", "core=0",
            "--init", "--hostname", "work", "--log-driver", "none", "--stop-timeout", "3",
            "-e", f"HOME={WORK_ROOT}/.home",
            spec.image,
        ]

    def create(self, spec: GooseSpec) -> None:  # type: ignore[override]
        st = self.state(spec.name)
        if st == "running":
            return
        if st == "stopped":
            self.start(spec.name)
            return
        if st == "error":
            raise RuntimeError_("The session container is in an unusable state; delete the Work session to start over.")
        r = self._d("volume", "create", "--label", f"semechka.goose={spec.session_id}", "--label", f"semechka.goose.instance={spec.instance}",
                    spec.volume)
        if r.returncode != 0:
            raise RuntimeError_(f"The workspace volume could not be created: {self._err(r)}")
        r = self._d(*self.run_command(spec), timeout=60)
        if r.returncode != 0:
            raise RuntimeError_(f"The session container could not be started: {self._err(r)}")

    def remove(self, name: str, *, volume: bool = True) -> None:
        if not GNAME_RE.match(name):
            raise RuntimeError_("Refusing to remove a container that is not a Work (Goose) container.")
        r = self._d("rm", "-f", name, timeout=40)
        if r.returncode != 0 and not self._missing(r):
            raise RuntimeError_(f"The session container could not be removed: {self._err(r)}")
        if volume:
            r = self._d("volume", "rm", "-f", name, timeout=60)
            if r.returncode != 0 and not self._missing(r):
                raise RuntimeError_(f"The workspace volume could not be removed: {self._err(r)}")

    def list_managed(self, instance: str) -> list[dict]:  # type: ignore[override]
        r = self._d("ps", "-a", "--filter", "label=semechka.goose", "--filter", f"label=semechka.goose.instance={instance}", "--format",
                    '{{.Names}}|{{.Label "semechka.goose"}}|{{.State}}', timeout=30)
        out = []
        if r.returncode != 0:
            raise RuntimeError_(f"Could not list session containers: {self._err(r)}")
        for line in r.stdout.decode().splitlines():
            n, _, rest = line.partition("|")
            sid, _, state = rest.partition("|")
            if GNAME_RE.match(n):
                out.append({"name": n, "session_id": sid, "state": "running" if state == "running" else "stopped"})
        return out

    # ---- Goose process ------------------------------------------------------------------------------------------
    def goose_argv(self, name: str, env: dict[str, str]) -> tuple[list[str], dict]:
        """`docker exec -i` argv + the environment of the docker CLI itself. Goose's own environment travels in `-e` flags,
        so nothing secret is placed in a shell string; the router key is never among them."""
        argv = ["docker", "exec", "-i", "-w", WORK_ROOT]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        argv += [name, "/usr/local/bin/goose", "acp"]
        return argv, sandbox_svc._env(self.host)

    def kill_goose(self, name: str) -> None:
        """Stop any goose process left in the container (a crashed exec client does not always take its child with it)."""
        try:
            self._d("exec", name, "/usr/local/bin/python3", "-I", "-c",
                    "import os,signal\nme=os.getpid()\nfor d in os.listdir('/proc'):\n"
                    " if d.isdigit() and int(d)!=me:\n"
                    "  try:\n   c=open('/proc/'+d+'/cmdline','rb').read().split(b'\\0')\n"
                    "   if c[0].endswith(b'/goose') or c[0]==b'goose': os.kill(int(d),signal.SIGKILL)\n"
                    "  except OSError: pass\n", timeout=15)
        except RuntimeError_:
            pass

    def run_in(self, name: str, argv: list[str], *, stdin: bytes | None = None, timeout: float = 60) -> tuple[int, str, str]:
        """Run a fixed argv in the session container (used for the shadow-git checkpoints; model text is never part of it)."""
        r = self._d("exec", *(["-i"] if stdin is not None else []), name, *argv, timeout=timeout, input=stdin)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
