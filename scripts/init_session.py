import asyncio
import os
import sys

from telethon import TelegramClient


SESSION_PATH = "/var/lib/tg-pushover-relay/telegram.session"


def _telegram_credentials() -> tuple[int, str]:
    api_id_raw = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")

    if (
        api_id_raw is None
        or not api_id_raw
        or not api_id_raw.isascii()
        or not api_id_raw.isdecimal()
        or int(api_id_raw, 10) <= 0
    ):
        raise ValueError("TELEGRAM_API_ID: invalid or missing")
    if api_hash is None or api_hash == "":
        raise ValueError("TELEGRAM_API_HASH: invalid or missing")

    return int(api_id_raw, 10), api_hash


async def init_session(api_id: int, api_hash: str) -> int:
    os.umask(0o077)
    client = TelegramClient(SESSION_PATH, api_id, api_hash)

    try:
        await client.start()
        if not await client.is_user_authorized():
            print("Telegram session authorization failed", file=sys.stderr)
            return 1
        print("Telegram session is authorized")
        return 0
    finally:
        if client.is_connected():
            await client.disconnect()


def main() -> int:
    try:
        api_id, api_hash = _telegram_credentials()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        return asyncio.run(init_session(api_id, api_hash))
    except Exception as exc:
        print(
            f"Telegram session setup failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
