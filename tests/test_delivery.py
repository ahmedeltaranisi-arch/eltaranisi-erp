"""Starts the real HTTP server and checks how assets are delivered.

These are the properties that make the app feel fast, so they are worth
locking down: compressed transfer, ETag revalidation (304 with no body), a
graceful fallback for clients that cannot decode gzip, and compression of
JSON API responses.

Run with:  python3 -m unittest tests.test_delivery
"""
from __future__ import annotations

import gzip
import http.client
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def _start():
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class DeliveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = _start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        body = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        return r.status, hdrs, body

    # ---- static assets -----------------------------------------------------
    def test_assets_are_gzipped_for_clients_that_ask(self):
        status, hdrs, body = self.req("/pages.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(hdrs.get("content-encoding"), "gzip")
        raw_size = os.path.getsize(ROOT / "static" / "pages.js")
        self.assertLess(len(body), raw_size * 0.4,
                        f"gzip only saved {len(body)}B of {raw_size}B")
        # and it really is the same file
        self.assertEqual(len(gzip.decompress(body)), raw_size)

    def test_assets_are_plain_for_clients_that_cannot_decode(self):
        status, hdrs, body = self.req("/pages.js", {"Accept-Encoding": "identity"})
        self.assertEqual(status, 200)
        self.assertNotIn("content-encoding", hdrs)
        self.assertEqual(len(body), os.path.getsize(ROOT / "static" / "pages.js"))

    def test_etag_gives_304_without_a_body(self):
        _, hdrs, _ = self.req("/app.js", {"Accept-Encoding": "gzip"})
        etag = hdrs.get("etag")
        self.assertTrue(etag, "assets must carry an ETag to be revalidated")
        status, hdrs2, body = self.req(
            "/app.js", {"Accept-Encoding": "gzip", "If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(hdrs2.get("etag"), etag)

    def test_html_is_never_cached_hard(self):
        _, hdrs, _ = self.req("/index.html", {"Accept-Encoding": "gzip"})
        self.assertEqual(hdrs.get("cache-control"), "no-cache")

    def test_static_does_not_leak_outside_the_static_dir(self):
        status, _, _ = self.req("/../server.py")
        self.assertIn(status, (400, 404))

    def test_json_api_responses_are_compressed(self):
        status, hdrs, body = self.req("/api/health", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        # health is tiny, so compression is correctly skipped there; a larger
        # public response is what proves the JSON path compresses.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request("GET", "/api/dev/accounts", headers={"Accept-Encoding": "gzip"})
        r = conn.getresponse()
        big = r.read()
        big_hdrs = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(big_hdrs.get("content-encoding"), "gzip")
        self.assertGreater(len(gzip.decompress(big)), len(big))

    # ---- cache behaviour ---------------------------------------------------
    def test_asset_cache_is_content_addressed(self):
        # Same byte count on purpose: mtime+size can collide when two writes
        # land in the same clock tick, so the cache must key on content.
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "probe.js"
        tmp.write_bytes(b"var a = 1;\n" * 200)
        first = server.static_asset(tmp)
        self.assertIn("gzip", first["variants"])
        self.assertEqual(gzip.decompress(first["variants"]["gzip"]), b"var a = 1;\n" * 200)

        tmp.write_bytes(b"var a = 2;\n" * 200)     # identical size, new content
        second = server.static_asset(tmp)
        self.assertNotEqual(second["etag"], first["etag"],
                            "an ETag must change when the bytes change")
        self.assertEqual(len(gzip.decompress(second["variants"]["gzip"])),
                         len(b"var a = 2;\n" * 200))

        # an unchanged file reuses the cached entry instead of recompressing
        third = server.static_asset(tmp)
        self.assertIs(third, second)


if __name__ == "__main__":
    unittest.main()
