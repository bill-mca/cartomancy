"""Smoke tests for the local mock backend.

Boots the server on an ephemeral port in a background thread and exercises the
endpoints the plugin actually depends on, asserting the response shapes match
what `TerraLabClient` / `GenerationService` parse.

Run:  python -m unittest tests.test_mockserver      (or: python tests/test_mockserver.py)
Pure stdlib — no pytest, requests, or QGIS required.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mockserver.app import DEV_KEY, make_server  # noqa: E402

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _get(url: str, headers: dict | None = None):
    req = Request(url, headers=headers or {}, method="GET")
    with urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost test traffic)
        return resp.status, resp.read(), dict(resp.headers)


def _post(url: str, body: dict, headers: dict | None = None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = Request(url, data=data, headers=h, method="POST")
    with urlopen(req, timeout=10) as resp:  # noqa: S310
        return resp.status, resp.read(), dict(resp.headers)


def _put(url: str, data: bytes):
    req = Request(url, data=data, method="PUT")
    with urlopen(req, timeout=5) as resp:  # noqa: S310
        return resp.status, resp.read()


class MockServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # max_dim small so PNG generation is instant in CI.
        cls.server = make_server("127.0.0.1", 0, max_dim=64)
        cls.host, cls.port = cls.server.server_address
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _auth(self):
        return {"Authorization": f"Bearer {DEV_KEY}", "X-Product-ID": "ai-edit"}

    def test_health(self):
        status, raw, _ = _get(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ok"])

    def test_usage_requires_auth(self):
        # No Authorization header -> NO_AUTH error (401). urlopen raises on 4xx.
        from urllib.error import HTTPError

        with self.assertRaises(HTTPError) as cm:
            _get(f"{self.base}/api/plugin/usage")
        self.assertEqual(cm.exception.code, 401)
        body = json.loads(cm.exception.read())
        self.assertEqual(body["code"], "NO_AUTH")

    def test_usage_authenticated_unlocks_credits(self):
        status, raw, _ = _get(f"{self.base}/api/plugin/usage", self._auth())
        self.assertEqual(status, 200)
        usage = json.loads(raw)
        self.assertNotIn("error", usage)
        self.assertEqual(usage["product_id"], "ai-edit")
        # The pre-generation check allows when images_used < images_limit.
        self.assertLess(usage["images_used"], usage["images_limit"])

    def test_bootstrap_404_triggers_fallback(self):
        from urllib.error import HTTPError

        with self.assertRaises(HTTPError) as cm:
            _get(f"{self.base}/api/plugin/bootstrap", self._auth())
        self.assertEqual(cm.exception.code, 404)

    def test_pairing_poll_returns_dev_key(self):
        status, raw, _ = _get(f"{self.base}/api/plugin/pair/poll?code=abc")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["activation_key"], DEV_KEY)

    def test_generate_returns_inline_result_and_real_png(self):
        status, raw, _ = _post(
            f"{self.base}/api/ai-edit/generate",
            {"prompt": "remove clouds", "resolution": "1K", "image": "x"},
            self._auth(),
        )
        self.assertEqual(status, 200)
        resp = json.loads(raw)
        # GenerationService requires request_id; sync path needs completed+url.
        self.assertIn("request_id", resp)
        self.assertEqual(resp["status"], "completed")
        self.assertTrue(resp["image_url"].startswith("http"))

        # The result URL must serve real image bytes (download_image rejects
        # non-image / <64-byte bodies).
        istatus, img, headers = _get(resp["image_url"])
        self.assertEqual(istatus, 200)
        self.assertTrue(img.startswith(_PNG_MAGIC))
        self.assertGreater(len(img), 64)
        self.assertEqual(headers.get("Content-Type"), "image/png")

    def test_export_config_serves_sizing_fields(self):
        # The layout capture path (canvas_exporter.prepare_export) needs
        # max_dimension + align; encoding needs input_format.
        status, raw, _ = _get(f"{self.base}/api/ai-edit/export-config")
        self.assertEqual(status, 200)
        cfg = json.loads(raw)
        for key in ("max_dimension", "align", "input_format"):
            self.assertIn(key, cfg)
        self.assertGreater(cfg["max_dimension"], 0)
        self.assertGreater(cfg["align"], 0)

    def test_upload_url_and_put(self):
        status, raw, _ = _post(
            f"{self.base}/api/ai-edit/upload-url", {"format": "png"}, self._auth()
        )
        self.assertEqual(status, 200)
        resp = json.loads(raw)
        for key in ("upload_token", "upload_url", "required_headers"):
            self.assertIn(key, resp)
        self.assertEqual(resp["required_headers"]["Content-Type"], "image/png")

        pstatus, _ = _put(resp["upload_url"], b"\x00\x01\x02\x03")
        self.assertEqual(pstatus, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
