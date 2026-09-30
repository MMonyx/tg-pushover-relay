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


async def show_sender_id(api_id: int, api_hash: str, identity: str) -> int:
    if not os.path.isfile(SESSION_PATH):
        print("Telegram session missing", file=sys.stderr)
        return 1

    client = TelegramClient(SESSION_PATH, api_id, api_hash)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("Telegram session is not authorized", file=sys.stderr)
            return 1

        entity = await client.get_entity(identity)
        if not getattr(entity, "bot", False):
            print("Resolved Telegram identity is not a bot", file=sys.stderr)
            return 1

        print(entity.id)
        return 0
    finally:
        if client.is_connected():
            await client.disconnect()


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: show_sender_id.py <source-bot-identity>", file=sys.stderr)
        return 2

    try:
        api_id, api_hash = _telegram_credentials()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        return asyncio.run(show_sender_id(api_id, api_hash, sys.argv[1]))
    except Exception as exc:
        print(
            f"Telegram source bot lookup failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
