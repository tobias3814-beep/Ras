import httpx
import pytest

from jarvis import capital_check as cc
from jarvis.config import ROOT


def fake(accept: set[str], code: str = "error.invalid.details", status: int = 401):
    def handler(req: httpx.Request) -> httpx.Response:
        env = "demo" if req.url.host.startswith("demo-") else "live"
        if req.url.path == "/api/v1/accounts":
            return httpx.Response(200, json={"accounts": [{"accountName": f"{env} CFD", "currency": "USD"}]})
        if env in accept:
            return httpx.Response(200, headers={"CST": "c", "X-SECURITY-TOKEN": "t"}, json={})
        return httpx.Response(status, json={"errorCode": code})
    return handler


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "secret", lambda k, **_: "x")
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text((ROOT / "config.yaml").read_text())
    monkeypatch.setattr(cc, "config_paths", lambda: [cfg])

    def run(handler):
        real = httpx.Client
        monkeypatch.setattr(cc.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        return cc.main()
    return cfg, run


def test_switches_to_live_when_only_live_accepts(setup, capsys):
    cfg, run = setup
    assert run(fake({"live"})) == 0
    out = capsys.readouterr().out
    assert "DEMO  ✗ error.invalid.details" in out and "LIVE  ✓ accepted — accounts: live CFD (USD)" in out
    assert "  env: live            # demo | live" in cfg.read_text()      # comment kept
    assert "prices only" in out


def test_keeps_demo_when_both_work(setup, capsys):
    cfg, run = setup
    assert run(fake({"demo", "live"})) == 0
    assert "  env: demo " in cfg.read_text() and "already pointed there" in capsys.readouterr().out


def test_neither_works_explains_and_changes_nothing(setup, capsys):
    cfg, run = setup
    before = cfg.read_text()
    assert run(fake(set(), "error.invalid.api.key")) == 1
    out = capsys.readouterr().out
    assert "created in the other environment" in out and "Neither environment" in out and cfg.read_text() == before


def test_rate_limit_hint(setup, capsys):
    _, run = setup
    run(fake(set(), "", 429))
    assert "rate limited" in capsys.readouterr().out


def test_set_env_only_touches_capital(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("execution:\n  environment: demo\ncapital:\n  env: demo   # note\n  candle_poll_seconds: 60\n")
    assert cc.set_env(p, "live") and p.read_text() == "execution:\n  environment: demo\ncapital:\n  env: live   # note\n  candle_poll_seconds: 60\n"
    assert not cc.set_env(p, "live")
