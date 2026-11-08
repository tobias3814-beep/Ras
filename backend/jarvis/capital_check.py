"""Capital.com login check: tries your stored key on Demo and on Live, explains each answer, and points Ras at the
one that works. Prints no secrets. Read-only: it logs in and lists your accounts — it never places or changes anything.

Run (quit Ras first, so its own login attempts don't trip Capital.com's rate limit):
    uv run python -m jarvis.capital_check
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from jarvis.config import ROOT, secret
from jarvis.feeds.capital import CRED_KEYS, REST

HINTS = {
    "error.invalid.details": "the email/password pair isn't accepted here — the password must be the API key's custom "
                             "password (not your login password), and the key must belong to this environment",
    "error.invalid.api.key": "this server doesn't recognise the API key — it was probably created in the other environment",
    "error.null.api.key": "no API key was sent — re-enter it with: uv run python -m jarvis.keys",
    "error.invalid.api.key.disabled": "the API key is switched off — enable it in Capital.com › Settings › API integrations",
    "error.too-many.requests": "rate limited — wait a minute (and make sure Ras is closed), then run this again",
}


def try_env(env: str, client: httpx.Client) -> dict[str, Any]:
    """One login attempt. Returns {ok, status, code, hint, accounts}."""
    r = client.post(f"{REST[env]}/api/v1/session", headers={"X-CAP-API-KEY": secret("capital_api_key") or ""},
                    json={"identifier": secret("capital_identifier"), "password": secret("capital_password")})
    if r.is_success:
        acc = client.get(f"{REST[env]}/api/v1/accounts",
                         headers={"CST": r.headers.get("CST", ""), "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN", "")})
        names = [f"{a.get('accountName', '?')} ({a.get('currency', '?')})" for a in (acc.json().get("accounts", []) if acc.is_success else [])]
        return {"ok": True, "status": r.status_code, "code": "", "hint": "", "accounts": names}
    code = ""
    try:
        code = r.json().get("errorCode", "")
    except ValueError:
        pass
    hint = HINTS.get(code) or ("rate limited — wait a minute, then retry" if r.status_code == 429 else f"HTTP {r.status_code}")
    return {"ok": False, "status": r.status_code, "code": code, "hint": hint, "accounts": []}


def config_paths() -> list[Path]:
    """Every config.yaml Ras may read: this one (dev or installed) plus the installed app's, if present."""
    app = (Path(os.environ.get("APPDATA", "")) if sys.platform == "win32" else Path.home() / "Library" / "Application Support") \
        / "com.jarvis.station" / "config.yaml"
    return [p for p in dict.fromkeys([ROOT / "config.yaml", app]) if p.exists()]


ENV_LINE = re.compile(r"^(capital:\n(?:[ \t].*\n)*?[ \t]+env:[ \t]*)(demo|live)\b", re.M)


def set_env(path: Path, env: str) -> bool:
    """Change capital.env in place, keeping every comment. True if the file changed."""
    text = path.read_text(encoding="utf-8")
    new, n = ENV_LINE.subn(lambda m: m.group(1) + env, text, count=1)
    if n and new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> int:
    if not all(secret(k) for k in CRED_KEYS):
        print("No Capital.com key stored. Run:  uv run python -m jarvis.keys")
        return 1
    results: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=20) as client:
        for env in ("demo", "live"):
            results[env] = try_env(env, client)
            time.sleep(1.2)  # POST /session allows one per second per key
    for env, r in results.items():
        if r["ok"]:
            print(f"  {env.upper():5} ✓ accepted" + (f" — accounts: {', '.join(r['accounts'])}" if r["accounts"] else ""))
        else:
            print(f"  {env.upper():5} ✗ {r['code'] or r['status']}: {r['hint']}")

    working = [e for e, r in results.items() if r["ok"]]
    if not working:
        print("\nNeither environment accepts this key. Most likely fix: in Capital.com switch to the account you want "
              "(Demo or Live), Settings › API integrations › generate a key with a custom password, then run "
              "`uv run python -m jarvis.keys` and enter that key, your login email and the key's password.")
        return 1
    env = "demo" if "demo" in working else working[0]  # both work: keep demo
    changed = [p for p in config_paths() if set_env(p, env)]
    print(f"\nRas will use Capital.com {env.upper()}.")
    for p in changed:
        print(f"  updated {p}")
    if env == "live":
        print("  Live is used for prices only — Signal Station has no order code.")
    print("Restart Ras to connect." if changed else "Config already pointed there — restart Ras if it's running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
