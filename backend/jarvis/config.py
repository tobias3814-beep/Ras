"""YAML config + OS-keychain secrets.

Two places on disk:
- BUNDLED: read-only resources (the default config.yaml) — the project folder in dev, PyInstaller's bundle when frozen.
- ROOT: the user's data folder (config.yaml, data/, logs/). In dev it's the project folder; the installed app sets
  JARVIS_HOME to its per-user app-data folder, and the default config is copied there on first run."""
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import keyring
import yaml

BUNDLED = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def _home() -> Path:
    if env := os.environ.get("JARVIS_HOME"):
        return Path(env)
    if getattr(sys, "frozen", False):  # frozen but started by hand: same folder the app would use
        base = Path(os.environ.get("APPDATA", "")) if sys.platform == "win32" else Path.home() / "Library" / "Application Support"
        return base / "com.jarvis.station"
    return Path(__file__).resolve().parents[2]


ROOT = _home()
KEYRING_SERVICE = "jarvis"


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "config.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLED / "config.yaml", path)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


SECRET_NAMES = ("capital_api_key", "capital_identifier", "capital_password", "anthropic_api_key",
                "binance_api_key", "binance_api_secret", "telegram_bot_token", "telegram_chat_id")
_cache: dict[str, str | None] = {}
secrets_loaded = threading.Event()


def preload_secrets() -> None:
    """Read every key once. Call in a thread: on macOS a freshly installed app gets a Keychain "Allow" prompt
    per key, and this waits for your answer — the server keeps running meanwhile."""
    for name in SECRET_NAMES:
        _cache[name] = keyring.get_password(KEYRING_SERVICE, name)
    secrets_loaded.set()


def secret(name: str, block: bool = True) -> str | None:
    """Cached after preload. block=False never touches the Keychain (safe on the event loop)."""
    if name in _cache:
        return _cache[name]
    if secrets_loaded.is_set() or not block:
        return None
    return keyring.get_password(KEYRING_SERVICE, name)
