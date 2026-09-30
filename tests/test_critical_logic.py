import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from telethon import events
from telethon.tl.custom.message import Message
from telethon.tl.types import PeerUser

import relay


SOURCE_ID = 123456789
FIXED_MESSAGE_DATE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = {
    "pushover_app_token": "A" * 30,
    "pushover_user_key": "B" * 30,
}


class FakeEvent:
    def __init__(self, *, out, is_private, sender_id):
        self.out = out
        self.is_private = is_private
        self.sender_id = sender_id


class RejectedContentTrapEvent(FakeEvent):
    @property
    def message(self):
        raise AssertionError("content accessor must not be touched")

    @property
    def date(self):
        raise AssertionError("post-filter metadata must not be touched")


class FakeResponse:
    def __init__(self, status_code, json_body=None, *, invalid_json=False):
        self.status_code = status_code
        self._json_body = json_body
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("synthetic invalid JSON")
        return self._json_body


class FakeHttpClient:
    def __init__(self, effects):
        self.effects = list(effects)
        self.post_calls = 0

    async def post(self, url, data):
        self.post_calls += 1
        if not self.effects:
            raise AssertionError("unexpected extra Pushover attempt")
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


def request_error(exc_type):
    request = httpx.Request("POST", relay.PUSHOVER_URL)
    return exc_type("synthetic transport failure", request=request)


class OtherRequestError(httpx.RequestError):
    pass


class SenderFilterTests(unittest.IsolatedAsyncioTestCase):
    def test_strict_sender_filter(self):
        cases = (
            ("accept", False, True, SOURCE_ID, True),
            ("wrong sender", False, True, SOURCE_ID + 1, False),
            ("non-private", False, False, SOURCE_ID, False),
            ("outgoing", True, True, SOURCE_ID, False),
        )
        for name, out, is_private, sender_id, expected in cases:
            with self.subTest(name=name):
                event = FakeEvent(
                    out=out,
                    is_private=is_private,
                    sender_id=sender_id,
                )
                self.assertIs(relay.is_target_event(event, SOURCE_ID), expected)

    async def test_metadata_rejection_happens_before_content_access(self):
        event = RejectedContentTrapEvent(
            out=False,
            is_private=True,
            sender_id=SOURCE_ID + 1,
        )
        result = await relay.handle_message(event, SOURCE_ID)
        self.assertIsNone(result)


class TelethonCompatibilityTests(unittest.TestCase):
    def test_new_message_event_exposes_out_direction(self):
        cases = (
            ("incoming", False),
            ("outgoing", True),
        )
        for name, out in cases:
            with self.subTest(name=name):
                message = Message(
                    id=1,
                    peer_id=PeerUser(SOURCE_ID),
                    date=FIXED_MESSAGE_DATE,
                    message="synthetic alert",
                    out=out,
                )
                event = events.NewMessage.Event(message)
                self.assertIs(event.out, out)


class FreshnessTests(unittest.TestCase):
    def test_five_minute_deadline_boundary(self):
        self.assertEqual(relay.FRESHNESS_TTL_SECONDS, 5 * 60)
        deadline = FIXED_MESSAGE_DATE + timedelta(minutes=5)

        self.assertTrue(
            relay.is_fresh(FIXED_MESSAGE_DATE, deadline - timedelta(microseconds=1))
        )
        self.assertFalse(relay.is_fresh(FIXED_MESSAGE_DATE, deadline))
        self.assertFalse(
            relay.is_fresh(FIXED_MESSAGE_DATE, deadline + timedelta(microseconds=1))
        )


class PushoverClassificationTests(unittest.TestCase):
    def test_confirmed_success(self):
        response = FakeResponse(200, {"status": 1})
        self.assertEqual(
            relay.classify_pushover_response(response), relay.CONFIRMED_SUCCESS
        )

    def test_confirmed_api_failure(self):
        response = FakeResponse(200, {"status": 0})
        self.assertEqual(
            relay.classify_pushover_response(response), relay.CONFIRMED_API_FAILURE
        )

    def test_invalid_json_is_ambiguous(self):
        response = FakeResponse(200, invalid_json=True)
        self.assertEqual(relay.classify_pushover_response(response), relay.AMBIGUOUS)

    def test_missing_status_is_ambiguous(self):
        response = FakeResponse(200, {})
        self.assertEqual(relay.classify_pushover_response(response), relay.AMBIGUOUS)

    def test_4xx_is_permanent_client_failure(self):
        response = FakeResponse(400)
        self.assertEqual(
            relay.classify_pushover_response(response),
            relay.PERMANENT_CLIENT_FAILURE,
        )

    def test_5xx_is_retry_eligible_server_failure(self):
        response = FakeResponse(503)
        self.assertEqual(
            relay.classify_pushover_response(response),
            relay.RETRY_ELIGIBLE_SERVER_FAILURE,
        )

    def test_unexpected_2xx_and_3xx_are_no_retry_category(self):
        retry_categories = {
            relay.RETRY_ELIGIBLE_SERVER_FAILURE,
            relay.SAFE_TRANSIENT_TRANSPORT_FAILURE,
        }
        for status_code in (201, 302):
            with self.subTest(status_code=status_code):
                outcome = relay.classify_pushover_response(FakeResponse(status_code))
                self.assertEqual(outcome, relay.UNEXPECTED_RESPONSE)
                self.assertNotIn(outcome, retry_categories)


class TransportAllowlistTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_transport_allowlist_is_classified_and_retry_eligible(self):
        for exc_type in (
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.PoolTimeout,
        ):
            with self.subTest(exception=exc_type.__name__):
                classify_client = FakeHttpClient([request_error(exc_type)])
                outcome = await relay.send_pushover_attempt(
                    classify_client, CONFIG, "synthetic alert"
                )
                self.assertEqual(outcome, relay.SAFE_TRANSIENT_TRANSPORT_FAILURE)

                retry_client = FakeHttpClient(
                    [request_error(exc_type), FakeResponse(200, {"status": 1})]
                )
                with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
                    "relay.is_fresh", return_value=True
                ):
                    await relay.send_pushover(
                        retry_client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
                    )
                self.assertEqual(retry_client.post_calls, 2)
                sleep_mock.assert_awaited_once_with(relay.RETRY_DELAY_SECONDS)

    async def test_read_timeout_is_ambiguous_and_not_retried(self):
        classify_client = FakeHttpClient([request_error(httpx.ReadTimeout)])
        outcome = await relay.send_pushover_attempt(
            classify_client, CONFIG, "synthetic alert"
        )
        self.assertEqual(outcome, relay.AMBIGUOUS)

        send_client = FakeHttpClient([request_error(httpx.ReadTimeout)])
        with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await relay.send_pushover(
                send_client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
            )
        self.assertEqual(send_client.post_calls, 1)
        sleep_mock.assert_not_awaited()

    async def test_other_request_error_is_ambiguous_and_not_retried(self):
        classify_client = FakeHttpClient([request_error(OtherRequestError)])
        outcome = await relay.send_pushover_attempt(
            classify_client, CONFIG, "synthetic alert"
        )
        self.assertEqual(outcome, relay.AMBIGUOUS)

        send_client = FakeHttpClient([request_error(OtherRequestError)])
        with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await relay.send_pushover(
                send_client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
            )
        self.assertEqual(send_client.post_calls, 1)
        sleep_mock.assert_not_awaited()


class RetrySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_eligible_and_still_fresh_retries_exactly_once(self):
        client = FakeHttpClient(
            [FakeResponse(503), FakeResponse(200, {"status": 1})]
        )
        with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
            "relay.is_fresh", return_value=True
        ) as fresh_mock:
            await relay.send_pushover(
                client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
            )

        self.assertEqual(relay.MAX_AUTOMATIC_RETRIES, 1)
        self.assertEqual(relay.RETRY_DELAY_SECONDS, 5)
        self.assertGreaterEqual(relay.RETRY_DELAY_SECONDS, 5)
        self.assertEqual(client.post_calls, 2)
        sleep_mock.assert_awaited_once_with(5)
        fresh_mock.assert_called_once_with(FIXED_MESSAGE_DATE)

    async def test_stale_before_retry_prevents_retry_attempt(self):
        client = FakeHttpClient([FakeResponse(503)])
        with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
            "relay.is_fresh", return_value=False
        ) as fresh_mock:
            await relay.send_pushover(
                client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
            )

        self.assertEqual(client.post_calls, 1)
        sleep_mock.assert_awaited_once_with(relay.RETRY_DELAY_SECONDS)
        fresh_mock.assert_called_once_with(FIXED_MESSAGE_DATE)

    async def test_retry_eligible_retry_does_not_trigger_second_retry(self):
        client = FakeHttpClient([FakeResponse(503), FakeResponse(503)])
        with patch("relay.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
            "relay.is_fresh", return_value=True
        ) as fresh_mock:
            await relay.send_pushover(
                client, CONFIG, "synthetic alert", FIXED_MESSAGE_DATE
            )

        self.assertEqual(client.post_calls, 2)
        sleep_mock.assert_awaited_once_with(relay.RETRY_DELAY_SECONDS)
        fresh_mock.assert_called_once_with(FIXED_MESSAGE_DATE)


if __name__ == "__main__":
    unittest.main()
