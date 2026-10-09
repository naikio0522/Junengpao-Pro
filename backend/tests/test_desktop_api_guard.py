"""The packaged loopback server must not trust arbitrary websites."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class DesktopApiGuardTests(unittest.TestCase):
    def test_every_api_route_needs_the_per_launch_token(self):
        with patch.dict(os.environ, {"VIDEOMATRIX_API_TOKEN": "a" * 64}):
            with TestClient(app) as client:
                for path in ("/api/health", "/api/link-watermark/jobs/no-such-task"):
                    self.assertEqual(client.get(path).status_code, 403)
                    self.assertEqual(client.get(path, headers={"Origin": "null"}).status_code, 403)
                authorized = client.get("/api/health", headers={
                    "X-VideoMatrix-Token": "a" * 64,
                })
                self.assertEqual(authorized.status_code, 200)
                self.assertEqual(authorized.json()["version"], app.version)

    def test_untrusted_web_origin_cannot_read_or_send_preflight(self):
        with patch.dict(os.environ, {"VIDEOMATRIX_API_TOKEN": "b" * 64}):
            with TestClient(app) as client:
                response = client.options("/api/link-watermark/resolve", headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "x-videomatrix-token,content-type",
                })
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)
                self.assertNotEqual(response.status_code, 200)
                response = client.get("/api/health", headers={
                    "Origin": "https://evil.example",
                    "X-VideoMatrix-Token": "b" * 64,
                })
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)


if __name__ == "__main__":
    unittest.main()
