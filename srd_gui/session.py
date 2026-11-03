"""A real browser session on the site, driven through pywebview.

The site sits behind a bot check that only a real browser can pass, and it
is the *user* who passes it (by clicking the check in the visible browser
window). Everything afterwards is plain ``fetch()`` calls executed inside
that page, so cookies, TLS and headers are the browser's own. Nothing here
spoofs, hides or auto-clicks anything.

All JavaScript calls are serialised behind one lock. Promise results are
collected by polling ``window.__srd`` because pywebview's synchronous
``evaluate_js`` does not await promises.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from typing import Callable

from .parser import looks_like_challenge

CHUNK = 4 * 1024 * 1024  # bytes of blob per bridge round-trip


class SessionError(Exception):
    pass


class ChallengeRequired(SessionError):
    """The site asked for a human check; the user needs to pass it."""


class Cancelled(SessionError):
    pass


_PROBE_JS = """(function(){
  var t = document.title || '';
  var cf = /just a moment/i.test(t) ||
           !!document.querySelector('#challenge-running,#challenge-stage,#challenge-error-text,#challenge-form,iframe[src*="challenges.cloudflare.com"]');
  var ready = !cf && !!document.querySelector('#content, div.icondisplay, a#download, div.zoomdisplay, #wrapper');
  return {title: t, url: location.href, challenge: cf, ready: ready, state: document.readyState};
})()"""


class SiteSession:
    def __init__(self, window, base_url: str, on_status: Callable[[dict], None] | None = None):
        self.window = window
        self.base_url = base_url.rstrip("/")
        self._lock = threading.RLock()
        self._on_status = on_status or (lambda d: None)
        self.cancel_event = threading.Event()

    # ----- low level -----------------------------------------------------
    def _js(self, script: str):
        with self._lock:
            try:
                return self.window.evaluate_js(script)
            except Exception:
                return None

    def probe(self) -> dict | None:
        r = self._js(_PROBE_JS)
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except ValueError:
                return None
        return r if isinstance(r, dict) else None

    def js_await(self, async_body: str, timeout: float = 180.0, poll: float = 0.2):
        """Run ``async_body`` (the body of an async function, must ``return``)
        inside the page and wait for its promise to settle."""
        token = uuid.uuid4().hex
        starter = (
            "(function(){ window.__srd = window.__srd || {};"
            f" window.__srd['{token}'] = {{done:false}};"
            f" (async function(){{ {async_body} }})()"
            f".then(function(v){{ window.__srd['{token}'] = {{done:true, value:v}}; }})"
            f".catch(function(e){{ window.__srd['{token}'] = {{done:true, error:String(e && e.message || e)}}; }});"
            " return true; })()"
        )
        if self._js(starter) is not True:
            raise SessionError("The browser page is not ready (bridge unavailable).")
        poller = (
            f"(function(){{ var x = window.__srd && window.__srd['{token}'];"
            " if (!x) return {gone:true}; if (!x.done) return null;"
            f" delete window.__srd['{token}']; return x; }})()"
        )
        deadline = time.monotonic() + timeout
        while True:
            if self.cancel_event.is_set():
                raise Cancelled()
            r = self._js(poller)
            if isinstance(r, dict):
                if r.get("gone"):
                    raise SessionError("The page navigated away while waiting (session lost).")
                if r.get("done"):
                    if "error" in r:
                        raise SessionError(r["error"])
                    return r.get("value")
            if time.monotonic() > deadline:
                raise SessionError("Timed out waiting for the browser.")
            time.sleep(poll)

    # ----- page readiness ------------------------------------------------
    def ensure_ready(self, url: str, timeout: float | None = None) -> dict:
        """Navigate the session window to ``url`` and wait until real site
        content is showing. If the bot check appears, tell the UI and keep
        waiting for the user to pass it."""
        self.window.load_url(url)
        self._on_status({"state": "loading", "url": url})
        start = time.monotonic()
        announced = False
        last_probe = None
        while True:
            if self.cancel_event.is_set():
                raise Cancelled()
            time.sleep(1.0)
            p = self.probe()
            last_probe = p
            if p and p.get("ready"):
                self._on_status({"state": "ready", "url": p.get("url")})
                return p
            if p and p.get("challenge"):
                if not announced:
                    announced = True
                    try:
                        self.window.show()
                        self.window.restore()
                    except Exception:
                        pass
                    self._on_status({"state": "challenge", "url": p.get("url")})
            if timeout is not None and time.monotonic() - start > timeout:
                raise SessionError(f"Page did not become ready: {last_probe}")

    def page_html(self) -> str:
        r = self._js("document.documentElement.outerHTML")
        if not isinstance(r, str):
            raise SessionError("Could not read the page.")
        return r

    def current_url(self) -> str | None:
        p = self.probe()
        return p.get("url") if p else None

    # ----- fetching through the page ------------------------------------
    def fetch_text(self, url: str) -> str:
        body = (
            f"var r = await fetch({json.dumps(url)}, {{credentials:'include'}});"
            " var t = await r.text(); return {status:r.status, text:t};"
        )
        r = self.js_await(body)
        status, text = r.get("status"), r.get("text", "")
        if status == 403 or looks_like_challenge(text):
            raise ChallengeRequired(url)
        if status != 200:
            raise SessionError(f"HTTP {status} for {url}")
        return text

    def fetch_bytes(self, url: str) -> bytes:
        token = uuid.uuid4().hex
        body = (
            f"var r = await fetch({json.dumps(url)}, {{credentials:'include'}});"
            " if (r.status !== 200) return {status:r.status};"
            " var b = await r.blob(); window.__srdBlobs = window.__srdBlobs || {};"
            f" window.__srdBlobs['{token}'] = b; return {{status:200, size:b.size, type:b.type}};"
        )
        meta = self.js_await(body)
        status = meta.get("status")
        if status == 403:
            raise ChallengeRequired(url)
        if status != 200:
            raise SessionError(f"HTTP {status} for {url}")
        size = int(meta.get("size") or 0)
        out = bytearray()
        try:
            offset = 0
            while offset < size:
                end = min(offset + CHUNK, size)
                part = (
                    f"var b = window.__srdBlobs['{token}'].slice({offset}, {end});"
                    " var du = await new Promise(function(res, rej){ var fr = new FileReader();"
                    " fr.onload = function(){ res(fr.result); }; fr.onerror = function(){ rej(fr.error); };"
                    " fr.readAsDataURL(b); });"
                    " return du.substring(du.indexOf(',') + 1);"
                )
                b64 = self.js_await(part)
                if not isinstance(b64, str):
                    raise SessionError("Bad chunk from browser.")
                out += base64.b64decode(b64)
                offset = end
        finally:
            self._js(f"(function(){{ if (window.__srdBlobs) delete window.__srdBlobs['{token}']; return true; }})()")
        if len(out) != size:
            raise SessionError(f"Size mismatch for {url}: got {len(out)} of {size} bytes")
        data = bytes(out)
        if size < 20000 and looks_like_challenge(data[:6000].decode("utf-8", "ignore")):
            raise ChallengeRequired(url)
        return data

    def fetch_many_data_urls(self, urls: list[str], concurrency: int = 6) -> dict[str, str | None]:
        """Fetch small images as data: URLs in one bridge call (for thumbnails)."""
        body = (
            f"var urls = {json.dumps(urls)}; var out = {{}}; var i = 0;"
            " async function one(u){ try { var r = await fetch(u, {credentials:'include'});"
            " if (r.status !== 200) { out[u] = null; return; } var b = await r.blob();"
            " if (b.size > 2000000 || /text\\/html/.test(b.type)) { out[u] = null; return; }"
            " out[u] = await new Promise(function(res){ var fr = new FileReader();"
            " fr.onload = function(){ res(fr.result); }; fr.onerror = function(){ res(null); }; fr.readAsDataURL(b); });"
            " } catch (e) { out[u] = null; } }"
            f" async function worker(){{ while (i < urls.length) {{ var u = urls[i++]; await one(u); }} }}"
            f" var ws = []; for (var k = 0; k < {int(concurrency)}; k++) ws.push(worker());"
            " await Promise.all(ws); return out;"
        )
        r = self.js_await(body, timeout=300)
        return r if isinstance(r, dict) else {}
