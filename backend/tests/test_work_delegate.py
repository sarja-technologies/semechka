"""Work Mode delegation: a bounded sub-task on another model through the engine's chat path, with the person's model access, depth and
count limits, time and token caps, and the engine's refusals (memory, errors) reported as they are."""
import base64
import json

from semechka import agent_svc, work_delegate

from . import fake_llm as L
from . import fake_sarness as FS
from .work_helpers import (deny_model, events, last_result, new_session, results, say_and_run, setting, we, wk, worker, workspace_file)  # noqa: F401


def run(st, c, sid, *calls, final="ok"):
    return say_and_run(st, c, sid, "go", *calls, L.final(final))


def delegates(c, sid):
    return [e["data"] for e in events(c, sid) if e["kind"] == "delegate"]


def helper_chats():
    return [x for x in FS.STATE["chat_log"]]


def test_delegate_runs_the_task_on_another_model_and_records_it(we, st):
    FS.STATE["chat_script"] = ["The capital of France is Paris."]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="What is the capital of France?"))
    r = last_result(we, sid)
    assert r["ok"] and "Answer from fake-chat (depth 1" in r["output"] and "The capital of France is Paris." in r["output"]
    chat = helper_chats()
    assert [c["ref"] for c in chat] == ["local:fake-chat"]                                    # through the engine's chat path, by its ref
    msgs = chat[0]["messages"]
    assert msgs[0]["role"] == "system" and "ONE bounded task" in msgs[0]["content"] and "reply with ONLY this JSON" in msgs[0]["content"]   # depth 1 of 2
    assert msgs[-1] == {"role": "user", "content": "What is the capital of France?"}
    assert chat[0]["params"]["max_tokens"] == 1024                                             # the token cap is the setting
    assert chat[0]["params"]["thinking"] is False                                              # a bounded sub-task: no reasoning phase by default
    d = delegates(we, sid)
    assert [x["status"] for x in d] == ["running", "done"]
    assert d[0]["delegate_id"] == d[1]["delegate_id"] and d[0]["delegate_id"].startswith("dl_")
    assert d[1]["model"] == "fake-chat" and d[1]["depth"] == 1 and d[1]["task"] == "What is the capital of France?"
    assert d[1]["result"] == "The capital of France is Paris." and "error" not in d[1] and "parent_id" not in d[1]


def test_delegate_passes_text_files_and_pictures_only_to_models_that_can_use_them(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("work_write_file", path="notes.txt", content="Agenda: budget review"))
    workspace_file(st, sid, "pic.png").write_bytes(FS.make_png())
    workspace_file(st, sid, "blob.bin").write_bytes(bytes(range(256)) * 4)
    FS.STATE["chat_script"] = ["Budget review.", "A tiny picture.", ]
    L.reset()
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="Summarise", files=["notes.txt"]),
        L.tool("delegate", model="fake-big", task="Describe", files=["pic.png"]),
        L.tool("delegate", model="fake-chat", task="Describe", files=["pic.png"]),
        L.tool("delegate", model="fake-chat", task="Read", files=["blob.bin"]),
        L.tool("delegate", model="fake-chat", task="Read", files=["nothing.txt"]),
        L.tool("delegate", model="fake-chat", task="Read", files=["/etc/passwd"]))
    ok1, ok2, novision, binary, missing, outside = results(we, sid)[-6:]
    assert ok1["ok"] and ok2["ok"]
    first = helper_chats()[0]["messages"][-1]["content"]
    assert "Summarise" in first and "--- file /work/notes.txt ---\nAgenda: budget review" in first
    second = helper_chats()[1]["messages"][-1]["content"]
    assert second[0]["type"] == "text" and second[1]["type"] == "image_url"
    assert base64.b64decode(second[1]["image_url"]["url"].split(",", 1)[1]) == FS.make_png()
    assert "cannot read pictures" in novision["error"] and "binary file" in binary["error"] and "no such file" in missing["error"]
    assert "inside /work" in outside["error"]
    assert len(helper_chats()) == 2                                                          # refusals never reached the engine


def test_a_model_the_person_may_not_use_is_refused_like_one_that_does_not_exist(we, st, app):
    two = worker(we, app, "two@acme.test")
    deny_model(we, we.engine_models["local:fake-chat"], two.user_id)
    FS.STATE["chat_script"] = ["fine"]
    sid = new_session(two, model_id=we.script_model)
    run(st, two, sid, L.tool("delegate", model="fake-chat", task="hi"), L.tool("delegate", model="not-a-model", task="hi"),
        L.tool("delegate", model="fake-big", task="hi"))
    denied, missing, ok = results(two, sid)
    assert denied["ok"] is False and missing["ok"] is False and ok["ok"] is True
    assert denied["error"].replace("fake-chat", "X") == missing["error"].replace("not-a-model", "X") and "not a model you may use" in denied["error"]
    assert [c["ref"] for c in helper_chats()] == ["local:fake-big"]
    assert [d["status"] for d in delegates(two, sid)] == ["running", "done"]                  # nothing was recorded for the refused ones


def test_the_count_per_run_is_bounded_and_a_new_run_starts_again(we, st):
    setting(we, "work.delegation_max_count", 2)
    FS.STATE["chat_script"] = ["a", "b", "c"]
    sid = new_session(we)
    run(st, we, sid, *[L.tool("delegate", model="fake-chat", task=f"t{i}") for i in range(3)])
    r = results(we, sid)
    assert [x["ok"] for x in r] == [True, True, False] and "at most 2 delegations per run" in r[2]["error"]
    assert len(helper_chats()) == 2
    L.reset()
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="again"))
    assert last_result(we, sid)["ok"] is True and len(helper_chats()) == 3


def test_delegation_can_be_switched_off(we, st):
    setting(we, "work.delegation_max_depth", 0)
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="x"))
    assert "switched off" in last_result(we, sid)["error"] and helper_chats() == []


def test_the_helper_may_consult_one_more_model_up_to_the_depth_limit(we, st):
    nest = json.dumps({"delegate": {"model": "fake-chat", "task": "What is 6 times 7?"}})
    FS.STATE["chat_script"] = [nest, "42", "The consulted model says 42."]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-big", task="Ask fake-chat for 6 times 7 and report."))
    r = last_result(we, sid)
    assert r["ok"] and "The consulted model says 42." in r["output"]
    assert [c["ref"] for c in helper_chats()] == ["local:fake-big", "local:fake-chat", "local:fake-big"]
    assert "reply with ONLY this JSON" in helper_chats()[0]["messages"][0]["content"]
    assert "Answer from the consulted model" in helper_chats()[2]["messages"][-1]["content"] and "42" in helper_chats()[2]["messages"][-1]["content"]
    assert "reply with ONLY this JSON" not in helper_chats()[1]["messages"][0]["content"]       # at the limit nothing more is offered
    d = delegates(we, sid)
    top = next(x for x in d if x["depth"] == 1 and x["status"] == "done")
    inner = next(x for x in d if x["depth"] == 2 and x["status"] == "done")
    assert inner["parent_id"] == top["delegate_id"] and inner["model"] == "fake-chat" and inner["result"] == "42" and "parent_id" not in top
    assert top["model"] == "fake-big"


def test_depth_one_means_helpers_cannot_consult_and_the_same_model_is_refused_at_the_limit(we, st):
    setting(we, "work.delegation_max_depth", 1)
    FS.STATE["chat_script"] = [json.dumps({"delegate": {"model": "fake-chat", "task": "x"}})]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-big", task="try to pass it on"),
        L.tool("delegate", model="script-model", task="the model that is already running this session"))
    passed, same = results(we, sid)
    assert passed["ok"] and "reply with ONLY this JSON" not in helper_chats()[0]["messages"][0]["content"]
    assert '"delegate"' in passed["output"] and len(helper_chats()) == 1                      # no consultation happened: the JSON is just its answer
    assert same["ok"] is False and "already working on this task" in same["error"] and "depth limit (1)" in same["error"]


def test_the_session_model_may_be_asked_when_there_is_depth_to_spare(we, st):
    L.say(L.tool("delegate", model="script-model", task="second opinion"), "I agree with you.", L.final("done"))
    sid = new_session(we)
    r = say_and_run(st, we, sid, "go")
    res = last_result(we, sid)
    assert res["ok"] and "Answer from script-model (depth 1" in res["output"] and "I agree with you." in res["output"]


def test_a_nested_consultation_that_is_refused_goes_back_to_the_helper(we, st):
    setting(we, "work.delegation_max_count", 1)
    FS.STATE["chat_script"] = [json.dumps({"delegate": {"model": "fake-chat", "task": "x"}}), "I could not consult, so: my own answer."]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-big", task="t"))
    r = last_result(we, sid)
    assert r["ok"] and "my own answer" in r["output"]
    assert "The consultation was refused" in helper_chats()[1]["messages"][-1]["content"] and "at most 1 delegations per run" in helper_chats()[1]["messages"][-1]["content"]


def test_a_busy_engine_is_waited_for_visibly_and_the_engines_words_are_kept(we, st, monkeypatch):
    monkeypatch.setattr(agent_svc, "BUSY_POLL_S", 0.05)
    FS.STATE["chat_fail"]["local:fake-chat"] = [(503, "not enough memory to load fake-chat: 24 GiB free, 30 GiB needed")]
    FS.STATE["chat_script"] = ["done after waiting"]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="x"))
    assert last_result(we, sid)["ok"] and "done after waiting" in last_result(we, sid)["output"]
    notes = [d.get("note") for d in delegates(we, sid) if d.get("note")]
    assert notes and "Waiting for the engine: Sarness: not enough memory to load fake-chat: 24 GiB free, 30 GiB needed" in notes[0]
    assert len(helper_chats()) == 2                                                          # the refused try and the one that worked
    # a persistent refusal is reported in the engine's words when the time is up
    FS.STATE["chat_fail"]["local:fake-chat"] = [(503, "not enough memory to load fake-chat")] * 100
    monkeypatch.setattr(work_delegate, "_limits", lambda w: (2, 8, 1, 1024))
    L.reset()
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="y"))
    r = last_result(we, sid)
    assert r["ok"] is False and "could not run that model within the time limit" in r["error"] and "not enough memory to load fake-chat" in r["error"]
    assert delegates(we, sid)[-1]["status"] == "failed" and "not enough memory" in delegates(we, sid)[-1]["error"]


def test_other_engine_errors_pass_through_and_an_empty_answer_is_an_error(we, st):
    FS.STATE["chat_fail"]["local:fake-chat"] = [(400, "the request exceeds the context window of 4096 tokens")]
    FS.STATE["chat_script"] = ["<think>hmm</think>   "]
    sid = new_session(we)
    run(st, we, sid, L.tool("delegate", model="fake-chat", task="x"), L.tool("delegate", model="fake-big", task="y"))
    a, b = results(we, sid)
    assert a["ok"] is False and "The model backend failed: Sarness: the request exceeds the context window of 4096 tokens" in a["error"]
    assert b["ok"] is False and "returned an empty answer" in b["error"]
    assert [d["status"] for d in delegates(we, sid)] == ["running", "failed", "running", "failed"]
