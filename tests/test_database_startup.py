import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy.exc import OperationalError

from app import database


def _dns_error() -> OperationalError:
    return OperationalError(
        "connect", {}, Exception('could not translate host name "db" to address: Temporary failure in name resolution')
    )


class WaitForDatabaseTests(unittest.TestCase):
    def test_retries_until_the_database_is_reachable(self):
        engine = MagicMock()
        engine.url.host, engine.url.port, engine.url.database = "db", 5432, "git_analysis"
        engine.connect.side_effect = [_dns_error(), _dns_error(), MagicMock()]
        with patch.object(database, "engine", engine), patch.object(database.time, "sleep") as sleep:
            with self.assertLogs("app.database", level="WARNING") as logs:
                database.wait_for_database(timeout_seconds=30, interval_seconds=2)
        self.assertEqual(engine.connect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn('could not translate host name "db"', logs.output[0])

    def test_gives_up_with_an_explanation(self):
        engine = MagicMock()
        engine.url.host, engine.url.port, engine.url.database = "db", 5432, "git_analysis"
        engine.connect.side_effect = _dns_error()
        clock = iter(range(0, 1000, 2))
        with patch.object(database, "engine", engine), patch.object(database.time, "sleep"), patch.object(
            database.time, "monotonic", side_effect=lambda: next(clock)
        ), self.assertLogs("app.database", level="WARNING"):
            with self.assertRaises(database.DatabaseUnavailableError) as ctx:
                database.wait_for_database(timeout_seconds=10, interval_seconds=2)
        message = str(ctx.exception)
        self.assertIn("db:5432/git_analysis", message)
        self.assertIn("docker compose ps", message)
        self.assertIn("Temporary failure in name resolution", message)


if __name__ == "__main__":
    unittest.main()
