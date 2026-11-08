from jarvis.bus import Bus
from jarvis.feeds.capital import parse_quote
from jarvis.feeds.crypto import to_candle

EPICS = {"GOLD": "XAU_USD"}  # epic -> symbol


def test_parse_quote_maps_epic_and_uses_ofr_as_ask():
    msg = {"status": "OK", "destination": "quote",
           "payload": {"epic": "GOLD", "bid": 2410.1, "ofr": 2410.5, "timestamp": 1}}
    assert parse_quote(msg, EPICS) == {"type": "price", "symbol": "XAU_USD", "bid": 2410.1, "ask": 2410.5, "ts": 1}


def test_parse_quote_ignores_other_messages_and_unknown_epics():
    assert parse_quote({"destination": "ping", "payload": {}}, EPICS) is None
    assert parse_quote({"destination": "quote", "payload": {"epic": "X", "bid": 1, "ofr": 1, "timestamp": 1}}, EPICS) is None


def test_to_candle_converts_ms_to_seconds():
    assert to_candle([1_700_000_060_000, 1, 2, 0.5, 1.5, 10]) == \
        {"time": 1_700_000_060, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}


async def test_bus_replays_snapshot_then_streams():
    bus = Bus()
    bus.publish({"n": 1}, key="a")
    q = bus.subscribe()
    bus.publish({"n": 2})
    assert [q.get_nowait(), q.get_nowait()] == [{"n": 1}, {"n": 2}]


def test_capital_login_backs_off_after_failure(monkeypatch):
    import asyncio
    import httpx
    import pytest
    from jarvis.feeds import capital

    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(401, json={"errorCode": "error.invalid.details"})

    monkeypatch.setattr(capital, "secret", lambda k, **_: "x")
    s = capital.Session("demo")
    s.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go():
        with pytest.raises(httpx.HTTPStatusError):
            await s.login()
        for _ in range(5):  # every instrument's poll fails fast instead of logging in again
            with pytest.raises(RuntimeError, match="backing off"):
                await s.get("/api/v1/prices/GOLD", {})

    asyncio.run(go())
    assert calls == ["/api/v1/session"]


def test_secret_nonblocking_never_touches_keychain(monkeypatch):
    from jarvis import config

    monkeypatch.setattr(config, "_cache", {})
    monkeypatch.setattr(config.keyring, "get_password", lambda *a: (_ for _ in ()).throw(AssertionError("keychain hit")))
    assert config.secret("capital_api_key", block=False) is None   # e.g. while macOS waits for 'Allow'
    monkeypatch.setattr(config, "_cache", {"capital_api_key": "k"})
    assert config.secret("capital_api_key") == "k"


def test_capital_stream_uses_bundled_ca_list():
    import ssl
    from jarvis.feeds import capital

    assert isinstance(capital.TLS, ssl.SSLContext) and capital.TLS.verify_mode == ssl.CERT_REQUIRED
    assert capital.TLS.cert_store_stats()["x509_ca"] > 100  # certifi's roots are loaded
