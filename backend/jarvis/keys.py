"""Store API credentials in the OS keychain:  uv run python -m jarvis.keys"""
import getpass

import keyring

from jarvis.config import KEYRING_SERVICE

KEYS = {
    "capital_api_key": "Capital.com API key",
    "capital_identifier": "Capital.com login (email)",
    "capital_password": "Capital.com API key custom password",
    "anthropic_api_key": "Anthropic API key (optional — news analysis, weekly review, voice)",
    "binance_api_key": "Binance DEMO trading API key (optional — approval-based crypto execution; trade-only, no withdrawals)",
    "binance_api_secret": "Binance DEMO trading API secret",
    "telegram_bot_token": "Telegram bot token (optional — phone alerts)",
    "telegram_chat_id": "Telegram chat id",
}

def main() -> None:
    for name, label in KEYS.items():
        value = getpass.getpass(f"{label} (input hidden, blank = keep current): ").strip()
        if value:
            keyring.set_password(KEYRING_SERVICE, name, value)
    print("Saved to OS keychain under service 'jarvis'.")


if __name__ == "__main__":
    main()
