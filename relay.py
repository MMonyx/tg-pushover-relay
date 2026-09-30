import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

import httpx
from telethon import TelegramClient, events


SESSION_PATH = "/var/lib/tg-pushover-relay/telegram.session"
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TITLE = "Telegram Alert"
FRESHNESS_TTL_SECONDS = 5 * 60
PUSHOVER_MESSAGE_MAX_CHARS = 1024
TRUNCATION_SUFFIX = "... [truncated]"
MAX_AUTOMATIC_RETRIES = 1
RETRY_DELAY_SECONDS = 5

CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
CONFIRMED_API_FAILURE = "CONFIRMED_API_FAILURE"
AMBIGUOUS = "AMBIGUOUS"
PERMANENT_CLIENT_FAILURE = "PERMANENT_CLIENT_FAILURE"
RETRY_ELIGIBLE_SERVER_FAILURE = "RETRY_ELIGIBLE_SERVER_FAILURE"
SAFE_TRANSIENT_TRANSPORT_FAILURE = "SAFE_TRANSIENT_TRANSPORT_FAILURE"
UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"{name}: missing")
    return value


def _positive_decimal(name: str) -> int:
    value = _required(name)
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name}: invalid (expected positive decimal integer)")

    parsed = int(value, 10)
    if parsed <= 0:
        raise ValueError(f"{name}: invalid (expected value > 0)")
    return parsed


def _non_empty(name: str) -> str:
    value = _required(name)
    if value == "":
        raise ValueError(f"{name}: invalid (expected non-empty string)")
    return value


def _pushover_credential(name: str) -> str:
    value = _required(name)
    if len(value) != 30 or not value.isascii() or not value.isalnum():
        raise ValueError(
            f"{name}: invalid (expected exactly 30 alphanumeric characters)"
        )
    return value


def load_config() -> dict[str, int | str]:
    return {
        "telegram_api_id": _positive_decimal("TELEGRAM_API_ID"),
        "telegram_api_hash": _non_empty("TELEGRAM_API_HASH"),
        "telegram_source_id": _positive_decimal("TELEGRAM_SOURCE_ID"),
        "pushover_user_key": _pushover_credential("PUSHOVER_USER_KEY"),
        "pushover_app_token": _pushover_credential("PUSHOVER_APP_TOKEN"),
    }


def is_target_event(event, telegram_source_id: int) -> bool:
    return (
        event.out is False
        and event.is_private is True
        and event.sender_id == telegram_source_id
    )


def is_fresh(message_date: datetime, now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(timezone.utc)
    deadline = message_date + timedelta(seconds=FRESHNESS_TTL_SECONDS)
    return now < deadline


def extract_text(event) -> str | None:
    text = event.message.message
    if not isinstance(text, str) or text == "":
        return None
    return text


def truncate_message(text: str) -> str:
    if len(text) <= PUSHOVER_MESSAGE_MAX_CHARS:
        return text

    prefix_length = PUSHOVER_MESSAGE_MAX_CHARS - len(TRUNCATION_SUFFIX)
    return text[:prefix_length] + TRUNCATION_SUFFIX


def classify_pushover_response(response: httpx.Response) -> str:
    status_code = response.status_code

    if status_code == 200:
        try:
            body = response.json()
        except ValueError:
            return AMBIGUOUS

        if not isinstance(body, dict):
            return AMBIGUOUS

        status = body.get("status")
        if type(status) is not int:
            return AMBIGUOUS

        if status == 1:
            return CONFIRMED_SUCCESS
        return CONFIRMED_API_FAILURE

    if 400 <= status_code < 500:
        return PERMANENT_CLIENT_FAILURE

    if 500 <= status_code < 600:
        return RETRY_ELIGIBLE_SERVER_FAILURE

    return UNEXPECTED_RESPONSE


async def send_pushover_attempt(
    http_client: httpx.AsyncClient,
    config: dict[str, int | str],
    message: str,
) -> str:
    payload = {
        "token": config["pushover_app_token"],
        "user": config["pushover_user_key"],
        "message": message,
        "title": PUSHOVER_TITLE,
        "priority": 1,
    }

    try:
        response = await http_client.post(PUSHOVER_URL, data=payload)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
        logging.warning(
            "pushover transport failure: outcome=%s exception=%s",
            SAFE_TRANSIENT_TRANSPORT_FAILURE,
            type(exc).__name__,
        )
        return SAFE_TRANSIENT_TRANSPORT_FAILURE
    except httpx.RequestError as exc:
        logging.warning(
            "pushover transport failure: outcome=%s exception=%s",
            AMBIGUOUS,
            type(exc).__name__,
        )
        return AMBIGUOUS

    outcome = classify_pushover_response(response)
    logging.info(
        "pushover response: http_status=%s outcome=%s",
        response.status_code,
        outcome,
    )
    return outcome


async def send_pushover(
    http_client: httpx.AsyncClient,
    config: dict[str, int | str],
    message: str,
    message_date: datetime,
) -> None:
    retry_count = 0

    while True:
        outcome = await send_pushover_attempt(http_client, config, message)

        if outcome not in (
            RETRY_ELIGIBLE_SERVER_FAILURE,
            SAFE_TRANSIENT_TRANSPORT_FAILURE,
        ):
            return

        if retry_count >= MAX_AUTOMATIC_RETRIES:
            logging.warning("pushover retry limit reached: outcome=%s", outcome)
            return

        logging.warning("pushover retry scheduled: outcome=%s", outcome)
        await asyncio.sleep(RETRY_DELAY_SECONDS)

        if not is_fresh(message_date):
            logging.info("pushover retry dropped: message stale")
            return

        retry_count += 1


async def handle_message(event, telegram_source_id: int) -> str | None:
    if not is_target_event(event, telegram_source_id):
        return None

    message_date = event.date
    if (
        not isinstance(message_date, datetime)
        or message_date.tzinfo is None
        or message_date.utcoffset() is None
    ):
        logging.error("telegram target message has unusable timestamp")
        return None

    if not is_fresh(message_date):
        return None

    text = extract_text(event)
    if text is None:
        return None

    return truncate_message(text)


async def run_telegram(config: dict[str, int | str]) -> int:
    client = None
    http_client = None
    stage = "http client construction"
    loop = asyncio.get_running_loop()
    run_task = asyncio.current_task()
    shutdown_requested = False

    def request_shutdown() -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return

        shutdown_requested = True
        logging.info("shutdown requested")
        if run_task is not None:
            run_task.cancel()

    loop.add_signal_handler(signal.SIGTERM, request_shutdown)

    try:
        http_client = httpx.AsyncClient()

        async def production_handler(event) -> None:
            prepared_text = await handle_message(event, config["telegram_source_id"])
            if prepared_text is None:
                return

            await send_pushover(http_client, config, prepared_text, event.date)

        stage = "client construction"
        client = TelegramClient(
            SESSION_PATH,
            config["telegram_api_id"],
            config["telegram_api_hash"],
            sequential_updates=True,
            auto_reconnect=True,
        )
        client.add_event_handler(production_handler, events.NewMessage(incoming=True))

        stage = "connect"
        await client.connect()

        stage = "authorization check"
        if not await client.is_user_authorized():
            logging.error("telegram session is not authorized")
            return 3

        stage = "receive loop"
        logging.info("telegram startup complete; entering receive loop")
        await client.run_until_disconnected()
        return 0
    except asyncio.CancelledError:
        if shutdown_requested:
            return 0
        raise
    except Exception as exc:
        logging.error(
            "telegram failure: stage=%s exception=%s",
            stage,
            type(exc).__name__,
        )
        return 3
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        try:
            if client is not None and client.is_connected():
                await client.disconnect()
        finally:
            if http_client is not None:
                await http_client.aclose()


def main() -> int:
    configure_logging()

    try:
        config = load_config()
    except ValueError as exc:
        logging.error("configuration invalid: %s", exc)
        return 2

    if not os.path.isfile(SESSION_PATH):
        logging.error("telegram session missing")
        return 3

    try:
        return asyncio.run(run_telegram(config))
    except Exception as exc:
        logging.error("telegram runtime failed: exception=%s", type(exc).__name__)
        return 3


if __name__ == "__main__":
    sys.exit(main())
