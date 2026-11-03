"""A tiny local stand-in for spriters-resource.com, built from the archived
fixtures, so the whole app can be exercised offline.

* game pages  : /game_boy_gbc/pokemongoldsilver/ and /nes/1942/ (real archived HTML)
* asset pages : /<console>/<game>/asset/<id>/ (templated from an archived asset page)
* files       : /media/assets/<id>.png|zip, /media/asset_icons/... (generated)
* a fake "human check" interstitial (title "Just a moment...") that is cleared
  by clicking a button on *our own* page - it stands in for the real site's
  check so the app's waiting / resume logic can be tested.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import struct
import threading
import zipfile
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

ZIP_IDS = {"9100"}          # asset ids served as .zip
BIG_IDS = {"9077"}          # asset id served as a multi-chunk file (> 4 MiB)
FAIL_IDS = {"94430"}        # asset id whose page returns HTTP 500 (1942's first item)
GAME_PAGES = {
    "/game_boy_gbc/pokemongoldsilver/": "game_pokemongoldsilver_2026-10.html",
    "/nes/1942/": "game_1942_2026-03.html",
}


def _png(color: tuple[int, int, int], size: int = 4) -> bytes:
    raw = b"".join(b"\x00" + bytes(color) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _color_for(key: str) -> tuple[int, int, int]:
    h = hashlib.sha256(key.encode()).digest()
    return h[0], h[1], h[2]


def asset_bytes(asset_id: str) -> bytes:
    """The exact bytes the server serves for an asset file (test oracle)."""
    if asset_id in ZIP_IDS:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Sheet A.png", _png(_color_for(asset_id + "a")))
            zf.writestr("sub folder/Sheet B.png", _png(_color_for(asset_id + "b")))
            zf.writestr("../evil.png", _png((255, 0, 0)))
        return buf.getvalue()
    png = _png(_color_for(asset_id))
    if asset_id in BIG_IDS:
        # Valid PNG followed by padding; PNG decoders ignore trailing data.
        return png + (hashlib.sha256(asset_id.encode()).digest() * (9 * 1024 * 1024 // 32))
    return png


def asset_sha(asset_id: str) -> str:
    return hashlib.sha256(asset_bytes(asset_id)).hexdigest()


class _State:
    def __init__(self, challenge: bool):
        self.challenge = challenge
        self.cleared = False
        self.lock = threading.Lock()
        self.html_requests = 0

    def revoke(self):
        with self.lock:
            self.cleared = False


_ASSET_TPL = None


def _asset_template() -> str:
    global _ASSET_TPL
    if _ASSET_TPL is None:
        with open(os.path.join(FIX, "asset_106321_2026-08.html"), encoding="utf-8", errors="replace") as f:
            _ASSET_TPL = f.read()
    return _ASSET_TPL


def render_asset_page(console: str, game: str, asset_id: str) -> str:
    html = _asset_template()
    ext = "zip" if asset_id in ZIP_IDS else "png"
    file_url = f"/media/assets/{asset_id}.{ext}"
    nice = f"Test Console - Test Game - Section - Asset {asset_id}.{ext}"
    html = html.replace("/media/assets/103/106321.png?updated=1755477189", file_url)
    html = re.sub(r'download="[^"]*"', f'download="{nice}"', html)
    html = re.sub(r'data-download="[^"]*"', f'data-download="{nice}"', html)
    html = html.replace("Maps (World Map &amp; Flight)", f"Asset {asset_id}")
    return html


CHALLENGE_HTML = """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="challenge-running">Fake verification page for offline testing.</div>
<p>This is NOT the real site's check. It is served by tests/fixture_server.py.</p>
<form method="get" action="/__clear"><input type="hidden" name="return" value="{ret}">
<button id="fake-verify" type="submit">Verify (fake)</button></form>
<script>
// Like the real interstitial, move on by itself once the server says we are cleared.
setInterval(function () {
  fetch('/__status').then(function (r) { return r.json(); }).then(function (s) {
    if (s.cleared) { location.href = {ret_js}; }
  }).catch(function () {});
}, 500);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    state: _State = None  # set by FixtureServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str, status: int = 200, extra=None):
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8", extra)

    def _redirect(self, location: str, extra=None):
        self._send(302, b"", "text/plain", {"Location": location, **(extra or {})})

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        path = u.path
        qs = parse_qs(u.query)

        if path == "/__status":
            with st.lock:
                body = b'{"cleared": true}' if st.cleared else b'{"cleared": false}'
            return self._send(200, body, "application/json")

        if path == "/__clear":
            with st.lock:
                st.cleared = True
            return self._redirect(unquote(qs.get("return", ["/"])[0]), {"Set-Cookie": "cleared=1; Path=/"})

        if path == "/process/setnsfw/":
            return self._redirect(unquote(qs.get("return", ["/"])[0]), {"Set-Cookie": "nsfw=1; Path=/"})

        if path.startswith("/media/asset_icons/"):
            return self._send(200, _png(_color_for(path), 8), "image/png")

        m = re.match(r"^/media/assets/(\d+)\.(png|zip)$", path)
        if m:
            data = asset_bytes(m.group(1))
            return self._send(200, data, "application/zip" if m.group(2) == "zip" else "image/png")

        # Everything below is HTML and subject to the fake check.
        if st.challenge:
            with st.lock:
                cleared = st.cleared
            if not cleared:
                import json as _json
                page = CHALLENGE_HTML.replace("{ret}", self.path).replace("{ret_js}", _json.dumps(self.path))
                return self._html(page, status=403)
        with st.lock:
            st.html_requests += 1

        if path in GAME_PAGES:
            with open(os.path.join(FIX, GAME_PAGES[path]), encoding="utf-8", errors="replace") as f:
                return self._html(f.read())

        m = re.match(r"^/([a-z0-9_]+)/([a-z0-9_]+)/asset/(\d+)/$", path)
        if m:
            if m.group(3) in FAIL_IDS:
                return self._html("<html><body>server error</body></html>", status=500)
            return self._html(render_asset_page(m.group(1), m.group(2), m.group(3)))

        if path == "/":
            return self._html("<html><head><title>Fixture home</title></head><body id='wrapper'><div id='content'>home</div></body></html>")
        return self._html("<html><body>not found</body></html>", status=404)


class FixtureServer:
    def __init__(self, challenge: bool = False):
        self.state = _State(challenge)
        handler = type("BoundHandler", (Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def revoke(self):
        """Simulate the site's clearance expiring mid-run."""
        self.state.revoke()

    def clear(self):
        """Simulate the user passing the check: the fake page then moves on by itself."""
        with self.state.lock:
            self.state.cleared = True


if __name__ == "__main__":
    import time
    s = FixtureServer(challenge="--challenge" in os.sys.argv).start()
    print("Fixture server on", s.base_url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.stop()
