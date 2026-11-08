import asyncio

import httpx
import pytest

from jarvis.voice import assistant as A

GB = A.GB
INSTALLED = {"qwen3:8b": int(5.2 * GB), "qwen3.5:2b": int(2.7 * GB), "qwen3.5:cloud": 0, "qwen3-coder:30b": 18 * GB}


def test_pick_model_fits_the_mac():
    assert A.pick_model("qwen3:8b", INSTALLED, 8 * GB) == "qwen3.5:2b"      # 5.2 GB is too big for 8 GB
    assert A.pick_model("qwen3:8b", INSTALLED, 32 * GB) == "qwen3:8b"       # a bigger Mac keeps the choice
    assert A.pick_model("qwen3:8b", {"qwen3.5:cloud": 0, "qwen3:8b": int(5.2 * GB)}, 8 * GB) is None  # never auto-cloud
    assert A.pick_model("nope", INSTALLED, 64 * GB) == "qwen3-coder:30b"     # largest that fits


def test_free_memory_reads_vm_stat(monkeypatch):
    out = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:  1000.\nPages active:  5000.\n"
           "Pages inactive:  2000.\nPages speculative:  500.\n")
    monkeypatch.setattr(A.os.path, "exists", lambda p: False)
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert A.free_memory() == 16384 * 3500


def ollama(monkeypatch, free_gb, total_gb=8):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n, "size": s} for n, s in INSTALLED.items()]})
        seen.append(req)
        return httpx.Response(200, json={"message": {"content": "Gold is short, sir."}})

    monkeypatch.setattr(A, "total_memory", lambda: total_gb * GB)
    monkeypatch.setattr(A, "free_memory", lambda: int(free_gb * GB))
    o = A.Ollama("qwen3:8b", "http://127.0.0.1:11434")
    o.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return o, seen


def test_ollama_uses_the_model_that_fits_and_unloads_soon(monkeypatch):
    o, seen = ollama(monkeypatch, free_gb=5)
    assert asyncio.run(o.chat([{"role": "user", "content": "hi"}], None)) == "Gold is short, sir."
    import json
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen3.5:2b" and body["keep_alive"] == "2m" and body["options"]["num_ctx"] == 4096


def test_ollama_refuses_when_memory_is_short(monkeypatch):
    o, seen = ollama(monkeypatch, free_gb=1.4)
    with pytest.raises(RuntimeError, match="freeze"):
        asyncio.run(o.chat([{"role": "user", "content": "hi"}], None))
    assert seen == []  # nothing was loaded
    assert "skipped" in A.unavailable("ollama", RuntimeError("only 1.4 GB free; loading x now would freeze the Mac"))
    assert "credit" in A.unavailable("claude", RuntimeError("Your credit balance is too low"))
