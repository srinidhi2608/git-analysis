import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app.main import app, target_branches


class SecurityHeaderTests(unittest.TestCase):
    def test_hardening_headers_and_hsts_only_over_https(self):
        http = TestClient(app).get("/health")
        self.assertEqual(http.status_code, 200)
        self.assertEqual(http.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(http.headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Strict-Transport-Security", http.headers)

        https = TestClient(app, base_url="https://testserver").get("/health")
        self.assertIn("max-age=", https.headers["Strict-Transport-Security"])


class BranchParameterTests(unittest.TestCase):
    def test_empty_values_mean_all_branches(self):
        self.assertIsNone(target_branches([]))
        self.assertIsNone(target_branches([""]))
        self.assertEqual(target_branches(["main", "", "release/1.0"]), ["main", "release/1.0"])


if __name__ == "__main__":
    unittest.main()
