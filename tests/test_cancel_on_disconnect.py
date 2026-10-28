import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi import HTTPException

from app import main


class FakeRequest:
    """Reports a disconnect after ``connected_polls`` checks."""

    def __init__(self, connected_polls: int) -> None:
        self.connected_polls = connected_polls
        self.polls = 0

    async def is_disconnected(self) -> bool:
        self.polls += 1
        return self.polls > self.connected_polls


class CancelOnDisconnectTests(unittest.TestCase):
    def test_returns_result_when_client_stays_connected(self):
        async def work():
            await asyncio.sleep(0.03)
            return "analytics"

        with patch.object(main, "DISCONNECT_POLL_SECONDS", 0.01):
            result = asyncio.run(main.cancel_on_disconnect(FakeRequest(connected_polls=100), work()))
        self.assertEqual(result, "analytics")

    def test_cancels_slow_work_when_client_disconnects(self):
        state = {"cancelled": False, "finished": False}

        async def slow_llm_call():
            try:
                await asyncio.sleep(10)
                state["finished"] = True
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        async def run():
            with patch.object(main, "DISCONNECT_POLL_SECONDS", 0.01):
                await main.cancel_on_disconnect(FakeRequest(connected_polls=2), slow_llm_call())

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(asyncio.wait_for(run(), timeout=2))

        self.assertEqual(ctx.exception.status_code, main.CLIENT_CLOSED_REQUEST)
        self.assertTrue(state["cancelled"])
        self.assertFalse(state["finished"])

    def test_propagates_errors_from_work(self):
        async def failing():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            asyncio.run(main.cancel_on_disconnect(FakeRequest(connected_polls=100), failing()))


if __name__ == "__main__":
    unittest.main()
