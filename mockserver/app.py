#!/usr/bin/env python3
"""Cartomancy local mock backend.

A zero-dependency stand-in for the proprietary TerraLab API, so the QGIS plugin
can run **end to end on your machine** without ever touching `terra-lab.ai`.

It implements just enough of the request/response contract that the plugin's
`TerraLabClient` (`src/api/terralab_client.py`) expects:

  * auth / quota  -> GET  /api/plugin/usage
  * generation    -> POST /api/ai-edit/generate           (returns a result inline)
  * result image  -> GET  /generated/<id>.png             (a real PNG, GDAL-readable)
  * browser pair  -> GET  /api/plugin/pair/poll            (auto-"ready" with a dev key)
  * misc config   -> /api/plugin/config, /api/ai-edit/export-config, ...

This is intentionally the *seed of the future Cloudflare Worker*: keep the routes
and JSON shapes identical to what the Worker will serve (see docs/PLAN.md §4).

Run it:

    python mockserver/app.py            # serves on 127.0.0.1:8787
    python mockserver/app.py --port 9000

Then point the plugin at it by creating `.env.local` in the plugin root:

    TERRALAB_BASE_URL=http://127.0.0.1:8787
    SKIP_TRIAL_CHECK=true        # optional: skip the credit pre-flight
    DEBUG=true                   # optional: plugin dev mode

…and sign in with the dev activation key printed at startup.

NOT for production. No real auth, no persistence, canned responses.
"""
from __future__ import annotations

import argparse
import json
import struct
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# A dev activation key that satisfies the plugin's format gate
# (^tl_[0-9a-f]{32}$ in src/core/auth/activation_manager.py). Any well-formed
# key is accepted by this mock; this is the one we advertise for copy/paste.
DEV_KEY = "tl_" + "0" * 32

# The product the plugin sends as X-Product-ID; echoed back so key validation
# (which checks product_id) passes.
PRODUCT_ID = "ai-edit"

# Keep pure-Python PNG generation snappy. The exact pixel size does not matter
# for exercising the auth/generation/download/write flow; raise if you want the
# mock result to match a layout's real output dimensions.
DEFAULT_MAX_DIM = 1024

_RESOLUTION_DIMS = {"1K": 1024, "2K": 2048, "4K": 4096}

# In-memory stores (reset on restart).
_GENERATIONS: dict[str, bytes] = {}   # request_id -> PNG bytes
_UPLOADS: dict[str, bytes] = {}       # upload_token -> uploaded bytes


# --------------------------------------------------------------------------- #
# Tiny pure-stdlib PNG encoder (no Pillow/numpy needed).
# --------------------------------------------------------------------------- #
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, max_dim: int = DEFAULT_MAX_DIM) -> bytes:
    """Generate a recognizable synthetic RGB PNG (gradient + checker).

    Capped at ``max_dim`` on each side so generation stays fast in pure Python.
    Color type 2 (truecolor, 8-bit) — read fine by GDAL in write_geotiff().
    """
    width = max(1, min(int(width), max_dim))
    height = max(1, min(int(height), max_dim))
    wm1 = max(1, width - 1)
    hm1 = max(1, height - 1)

    # Red depends on column only; precompute once.
    reds = [(x * 255) // wm1 for x in range(width)]

    raw = bytearray()
    for y in range(height):
        green = (y * 255) // hm1
        raw.append(0)  # PNG filter type 0 (None) for this scanline
        row = bytearray(width * 3)
        i = 0
        for x in range(width):
            row[i] = reds[x]
            row[i + 1] = green
            # Coarse checker in blue so the result reads as obviously synthetic.
            row[i + 2] = 200 if ((x >> 5) + (y >> 5)) & 1 else 70
            i += 3
        raw += row

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


def _dims_for(body: dict, max_dim: int) -> tuple[int, int]:
    """Pick output dimensions from the submit body (export_width/height), else
    from the resolution tier, capped by max_dim."""
    w = body.get("export_width")
    h = body.get("export_height")
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
        return int(w), int(h)
    side = _RESOLUTION_DIMS.get(str(body.get("resolution", "1K")).upper(), 1024)
    return side, side


class MockBackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CartomancyMock/0.1"
    max_dim = DEFAULT_MAX_DIM  # overridable via make_server()

    # -- helpers ----------------------------------------------------------- #
    def _send_json(self, obj: dict | list, status: int = 200) -> None:
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _has_auth(self) -> bool:
        return bool(self.headers.get("Authorization", "").strip())

    def _abs_url(self, path: str) -> str:
        host = self.headers.get("Host") or "{}:{}".format(*self.server.server_address)
        return f"http://{host}{path}"

    def log_message(self, fmt: str, *args) -> None:  # concise one-liners
        print(f"  mock  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # -- routing ----------------------------------------------------------- #
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/health"):
            return self._send_json({"ok": True, "service": "cartomancy-mockserver"})

        # AUTH + quota. The single most important endpoint: a non-error payload
        # here means the plugin treats you as signed in with credits.
        if path == "/api/plugin/usage":
            if not self._has_auth():
                return self._send_json(
                    {"error": "No activation key", "code": "NO_AUTH"}, status=401
                )
            return self._send_json(
                {
                    "product_id": PRODUCT_ID,
                    "images_used": 0,
                    "images_limit": 9999,
                    "is_free_tier": False,
                    "plan": "studio",
                }
            )

        if path == "/api/plugin/config":
            return self._send_json(
                {
                    "free_credits": 75,
                    "free_tier_active": True,
                    "upgrade_url": self._abs_url("/dashboard"),
                    "tutorial_url": self._abs_url("/tutorial"),
                }
            )

        if path == "/api/ai-edit/export-config":
            return self._send_json(
                {"submit_timeouts_ms": {"1K": 45000, "2K": 60000, "4K": 90000}}
            )

        # Newer-server-only bundle; the plugin explicitly falls back to the
        # individual endpoints (usage/config/export-config) when this 404s.
        if path == "/api/plugin/bootstrap":
            return self._send_json({"error": "not found", "code": "NOT_FOUND"}, status=404)

        if path == "/api/plugin/account":
            return self._send_json(
                {
                    "email": "dev@localhost",
                    "plan": "studio",
                    "images_used": 0,
                    "images_limit": 9999,
                }
            )

        if path in ("/api/plugin/history", "/api/plugin/favorites"):
            return self._send_json({"prompts": []})

        if path == "/api/ai-edit/history":
            return self._send_json({"jobs": [], "has_more": False})

        # Browser sign-in poll: auto-succeed with the dev key so the "Connect"
        # button works without a real browser handoff. (Manual key entry with
        # the dev key is the simpler path and needs none of this.)
        if path == "/api/plugin/pair/poll":
            return self._send_json({"status": "ready", "activation_key": DEV_KEY})

        # Friendly page so the browser tab the plugin opens isn't a 404.
        if path == "/connect":
            html = (
                b"<!doctype html><meta charset=utf-8>"
                b"<title>Cartomancy mock pairing</title>"
                b"<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto'>"
                b"<h1>Mock pairing complete</h1>"
                b"<p>This is the local Cartomancy mock backend. The plugin has "
                b"been paired with the dev activation key. You can close this tab "
                b"and return to QGIS.</p></body>"
            )
            return self._send_bytes(html, "text/html; charset=utf-8")

        # Generation status (only reached if a submit didn't return inline).
        if path == "/api/ai-edit/generate/status":
            rid = (query.get("request_id") or [""])[0]
            png = _GENERATIONS.get(rid)
            if png is None:
                return self._send_json(
                    {"error": "unknown request_id", "code": "NOT_FOUND", "status": "failed"}
                )
            return self._send_json(
                {
                    "status": "completed",
                    "image_url": self._abs_url(f"/generated/{rid}.png"),
                    "output_width": 0,
                    "output_height": 0,
                }
            )

        # The generated result image itself.
        if path.startswith("/generated/"):
            rid = path[len("/generated/"):].removesuffix(".png")
            png = _GENERATIONS.get(rid)
            if png is None:
                return self._send_json({"error": "not found"}, status=404)
            return self._send_bytes(png, "image/png")

        return self._send_json({"error": f"no mock route for GET {path}"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()

        # THE money route. Generate a placeholder result and return it inline
        # (status=completed + image_url), so the plugin's sync path skips polling.
        if path == "/api/ai-edit/generate":
            rid = uuid.uuid4().hex
            w, h = _dims_for(body, self.max_dim)
            _GENERATIONS[rid] = make_png(w, h, self.max_dim)
            return self._send_json(
                {
                    "request_id": rid,
                    "status": "completed",
                    "image_url": self._abs_url(f"/generated/{rid}.png"),
                    "resolution": body.get("resolution", "1K"),
                    "aspect_ratio": body.get("aspect_ratio", "1:1"),
                    "credit_cost": 20,
                    "estimated_time": 2,
                }
            )

        # Presigned-upload handshake (used only for large inputs).
        if path == "/api/ai-edit/upload-url":
            token = uuid.uuid4().hex
            fmt = str(body.get("format", "png")).lower()
            content_type = {
                "png": "image/png",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
            }.get(fmt, "image/png")
            return self._send_json(
                {
                    "upload_token": token,
                    "upload_url": self._abs_url(f"/mock-upload/{token}"),
                    "expires_at": int(time.time()) + 900,
                    "max_bytes": 50_000_000,
                    "required_headers": {"Content-Type": content_type},
                }
            )

        if path in (
            "/api/ai-edit/generate/cancel",
            "/api/ai-edit/generate/refund",
            "/api/ai-edit/history/favorite",
            "/api/plugin/favorites",
            "/api/plugin/favorites/delete",
            "/api/plugin/pair/cancel",
            "/api/plugin/track",
        ):
            return self._send_json({"status": "ok"})

        return self._send_json({"error": f"no mock route for POST {path}"}, status=404)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/mock-upload/"):
            token = path[len("/mock-upload/"):]
            length = int(self.headers.get("Content-Length", 0) or 0)
            _UPLOADS[token] = self.rfile.read(length) if length > 0 else b""
            return self._send_json({"status": "stored", "bytes": len(_UPLOADS[token])})
        return self._send_json({"error": f"no mock route for PUT {path}"}, status=404)


def make_server(host: str = "127.0.0.1", port: int = 8787,
                max_dim: int = DEFAULT_MAX_DIM) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (MockBackendHandler,), {"max_dim": max_dim})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cartomancy local mock backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--max-dim", type=int, default=DEFAULT_MAX_DIM,
        help="cap on generated result image dimensions (pure-Python speed)",
    )
    args = parser.parse_args()

    httpd = make_server(args.host, args.port, args.max_dim)
    print("Cartomancy mock backend")
    print(f"  listening : http://{args.host}:{args.port}")
    print(f"  dev key   : {DEV_KEY}")
    print("  point the plugin at it via .env.local:")
    print(f"      TERRALAB_BASE_URL=http://{args.host}:{args.port}")
    print("  Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
