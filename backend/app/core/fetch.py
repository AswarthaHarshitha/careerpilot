"""Outbound HTTP for untrusted URLs (job postings, career sites, repository content).

Every URL a user pastes is treated as hostile: only http(s), no credentials in the URL, and every hostname -
including each redirect target - must resolve exclusively to public IP addresses. This blocks requests to
localhost, private networks and cloud metadata endpoints (SSRF).
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import get_settings
from app.core.errors import ExternalFetchError

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; CareerPilot/0.1; personal job-application workspace)"
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml",
    "application/json",
    "text/plain",
    "application/pdf",
    "application/ld+json",
    "text/xml",
    "application/xml",
    "application/vnd.github",
)
MAX_REDIRECTS = 5


class UnsafeURLError(ExternalFetchError):
    def __init__(self, message: str):
        super().__init__(message, hint="Only public http(s) addresses can be opened.", actions=["paste_description"])
        self.code = "unsafe_url"


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status_code: int
    content_type: str
    body: bytes
    headers: dict

    @property
    def text(self) -> str:
        charset = "utf-8"
        if "charset=" in self.content_type:
            charset = self.content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def validate_url(url: str) -> str:
    """Returns the URL if it is safe to request; raises UnsafeURLError otherwise."""
    url = (url or "").strip()
    if len(url) > 2000:
        raise UnsafeURLError("That link is too long.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http:// and https:// links can be opened.")
    if parts.username or parts.password:
        raise UnsafeURLError("Links containing a username or password are not allowed.")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("That link has no host name.")
    settings = get_settings()
    if host in settings.fetch_allow_private_hosts:
        return url
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("That link has an invalid port.") from exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ExternalFetchError(
            f"The address {host} could not be found.", hint="Check the link for typos, or paste the job description."
        ) from exc
    ips = {info[4][0] for info in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise UnsafeURLError("That link points to a private or local network address.")
    return url


def fetch(
    url: str,
    *,
    headers: dict | None = None,
    accept: str = "text/html,application/json;q=0.9,*/*;q=0.5",
    max_bytes: int | None = None,
    allow_status: tuple[int, ...] = (200,),
) -> FetchResult:
    settings = get_settings()
    limit = max_bytes or settings.fetch_max_bytes
    current = validate_url(url)
    req_headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en;q=0.9"}
    if headers:
        req_headers.update(headers)
    timeout = httpx.Timeout(settings.fetch_timeout_seconds, connect=10.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current, headers=req_headers) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ExternalFetchError("The page redirected without a destination.")
                        current = validate_url(urljoin(current, location))
                        continue
                    ctype = resp.headers.get("content-type", "").lower()
                    if resp.status_code not in allow_status:
                        raise _status_error(resp.status_code, current)
                    if ctype and not any(ctype.startswith(t) for t in ALLOWED_CONTENT_TYPES):
                        raise ExternalFetchError(f"The link returned an unsupported file type ({ctype.split(';')[0]}).")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > limit:
                        raise ExternalFetchError("The page is too large to analyse.")
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ExternalFetchError("The page is too large to analyse.")
                        chunks.append(chunk)
                    return FetchResult(current, resp.status_code, ctype, b"".join(chunks), dict(resp.headers))
            raise ExternalFetchError("The page redirected too many times.")
    except httpx.TimeoutException as exc:
        raise ExternalFetchError(
            "The page took too long to respond.", hint="The site may be slow or blocking automated requests."
        ) from exc
    except httpx.HTTPError as exc:
        log.info("fetch failed for host=%s: %s", urlsplit(current).hostname, type(exc).__name__)
        raise ExternalFetchError(
            "The page could not be reached.", hint="The site may be down or blocking automated requests."
        ) from exc


def _status_error(status: int, url: str) -> ExternalFetchError:
    host = urlsplit(url).hostname
    if status in (401, 403):
        return ExternalFetchError(
            f"{host} refused the request (HTTP {status}).",
            hint="The company may require you to sign in, or it blocks automated access to job pages.",
            actions=["open_manually", "paste_description", "retry"],
        )
    if status == 404:
        return ExternalFetchError(
            f"The job page was not found on {host} (HTTP 404).",
            hint="The posting may have been closed or the link is incomplete.",
            actions=["open_manually", "paste_description"],
        )
    if status == 429:
        return ExternalFetchError(f"{host} is rate-limiting requests (HTTP 429).", hint="Wait a minute and retry.")
    return ExternalFetchError(
        f"{host} returned HTTP {status}.", actions=["retry", "open_manually", "paste_description"]
    )
