"""Renders JavaScript-built career pages in a sandboxed headless browser.

Used only when a page's HTML has no readable job description. Every request the page makes - including
redirects, scripts and XHR - is checked against the same rules as the plain fetcher (public http(s) addresses
only), so a page cannot use the browser to reach private networks. Images, media and fonts are not loaded.
"""

import contextlib
import logging
import threading
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.errors import ExternalFetchError
from app.core.fetch import USER_AGENT, validate_url

log = logging.getLogger(__name__)
_LOCK = threading.Semaphore(1)  # one browser at a time keeps memory bounded on small servers
BLOCKED_TYPES = {"image", "media", "font"}


class BrowserUnavailable(Exception):
    pass


def render_page(url: str, timeout_ms: int = 25_000) -> tuple[str, str]:
    """Returns (rendered HTML, final URL)."""
    if not get_settings().browser_render_enabled:
        raise BrowserUnavailable("disabled")
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable("playwright not installed") from exc

    checked: dict[str, bool] = {}

    def allowed(request_url: str) -> bool:
        host = urlsplit(request_url).hostname or ""
        if request_url.startswith(("data:", "blob:")):
            return True
        if host not in checked:
            try:
                validate_url(request_url)
                checked[host] = True
            except ExternalFetchError:
                checked[host] = False
        return checked[host]

    def guard(route):
        req = route.request
        if req.resource_type in BLOCKED_TYPES or not allowed(req.url):
            return route.abort()
        return route.continue_()

    validate_url(url)
    with _LOCK:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu"])
                try:
                    ctx = browser.new_context(
                        user_agent=USER_AGENT, java_script_enabled=True, service_workers="block", accept_downloads=False
                    )
                    ctx.route("**/*", guard)
                    page = ctx.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    with contextlib.suppress(PWError):
                        page.wait_for_load_state("networkidle", timeout=12_000)
                    page.wait_for_timeout(800)
                    if not allowed(page.url):
                        raise ExternalFetchError("The page redirected to a private address.")
                    return page.content(), page.url
                finally:
                    browser.close()
        except PWError as exc:
            log.info("render failed for host=%s: %s", urlsplit(url).hostname, str(exc).splitlines()[0][:120])
            raise BrowserUnavailable(str(exc)[:200]) from exc
