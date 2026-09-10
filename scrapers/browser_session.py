"""Minimal Playwright session wrapper.

Page loading is deliberately plain here: a standard Playwright browser, default
settings, no evasion of any kind. This excerpt is about the *extraction* layer —
how a page's products are read once you have the HTML — not about how pages are
fetched. If a site declines to serve an automated client, the scan skips it.

Interface:
    with BrowserSession(headless=True) as s:
        page = s.new_page()
        page.goto(url, wait_until="domcontentloaded")
        ...

Anything beyond this (session reuse, rate limiting, honoring robots.txt, and
respecting a site's terms of service) is the caller's responsibility.
"""

import time

DEFAULT_TIMEOUT_MS = 45_000
# A plain, honest UA. Identify your crawler; don't impersonate a person's browser.
USER_AGENT = "FlipFinderBot/0.1 (+https://github.com/jdcon31/flip-finder)"


class BrowserSession:
    """Owns a Playwright browser + context for the life of a scan."""

    def __init__(self, headless: bool = True, user_agent: str = USER_AGENT,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.headless = headless
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None

    def start(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(user_agent=self.user_agent)
        self._context.set_default_timeout(self.timeout_ms)
        return self

    def new_page(self):
        if self._context is None:
            raise RuntimeError("BrowserSession.start() must be called first")
        return self._context.new_page()

    def close(self):
        for obj in (self._context, self._browser, self._pw):
            if obj is None:
                continue
            try:
                obj.close() if obj is not self._pw else obj.stop()
            except Exception:
                pass
        self._pw = self._browser = self._context = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def polite_pause(seconds: float = 1.0) -> None:
    """Space out requests. Called between page loads so a scan stays low-volume."""
    time.sleep(seconds)
