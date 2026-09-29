"""Minimal GitHub REST client built on the SSRF-safe fetcher.

Per repository it spends one API request (the recursive git tree, which carries file sizes, so the language
breakdown can be computed locally). README and manifest files are read from raw.githubusercontent.com, which
does not consume REST API quota. This keeps a full scan of a typical profile inside the 60 requests/hour
that GitHub allows without a token.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from app.core.config import get_settings
from app.core.errors import AppError, ExternalFetchError
from app.core.fetch import fetch


class RateLimited(AppError):
    def __init__(self, reset_at: datetime | None):
        when = reset_at.strftime("%H:%M UTC") if reset_at else "later"
        super().__init__(
            f"GitHub's rate limit was reached; the scan will continue after {when}.",
            code="github_rate_limited",
            hint="Add a read-only GitHub token in Settings to raise the limit from 60 to 5,000 requests per hour.",
            status_code=429,
        )
        self.reset_at = reset_at


@dataclass
class RepoInfo:
    name: str
    full_name: str
    description: str
    html_url: str
    homepage: str
    private: bool
    fork: bool
    archived: bool
    default_branch: str
    pushed_at: datetime | None
    stars: int
    topics: list[str]
    language: str


@dataclass
class TreeEntry:
    path: str
    size: int


class GitHubClient:
    def __init__(self, token: str = "", api_base: str | None = None, raw_base: str | None = None):
        s = get_settings()
        self.token = token or s.github_token
        self.api = (api_base or s.github_api_base).rstrip("/")
        self.raw = (raw_base or s.github_raw_base).rstrip("/")
        self.remaining: int | None = None
        self.reset_at: datetime | None = None

    def _headers(self, accept: str = "application/vnd.github+json") -> dict:
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _api_json(self, path: str):
        if self.remaining is not None and self.remaining <= 1:
            raise RateLimited(self.reset_at)
        try:
            res = fetch(
                self.api + path,
                headers=self._headers(),
                accept="application/vnd.github+json",
                allow_status=(200, 403, 404, 409, 429),
            )
        except ExternalFetchError as exc:
            raise ExternalFetchError(f"GitHub could not be reached: {exc.message}") from exc
        rem = res.headers.get("x-ratelimit-remaining")
        reset = res.headers.get("x-ratelimit-reset")
        if rem is not None and rem.isdigit():
            self.remaining = int(rem)
        if reset and reset.isdigit():
            self.reset_at = datetime.fromtimestamp(int(reset)).astimezone()
        if res.status_code in (403, 429) and (self.remaining == 0 or "rate limit" in res.text.lower()):
            raise RateLimited(self.reset_at)
        if res.status_code == 404:
            return None
        if res.status_code == 409:  # empty repository
            return None
        if res.status_code != 200:
            raise ExternalFetchError(f"GitHub returned HTTP {res.status_code}.")
        return json.loads(res.body)

    def get_user(self, username: str) -> dict | None:
        return self._api_json(f"/users/{quote(username)}")

    def list_repos(self, username: str, include_private: bool) -> list[RepoInfo]:
        repos: list[RepoInfo] = []
        for page in range(1, 11):
            if include_private and self.token:
                path = f"/user/repos?per_page=100&page={page}&affiliation=owner&sort=pushed"
            else:
                path = f"/users/{quote(username)}/repos?per_page=100&page={page}&type=owner&sort=pushed"
            data = self._api_json(path)
            if data is None:
                if page == 1:
                    raise AppError(
                        f"GitHub user '{username}' was not found.", code="github_user_not_found", status_code=404
                    )
                break
            for r in data:
                if include_private and self.token and r.get("owner", {}).get("login", "").lower() != username.lower():
                    continue
                repos.append(_repo_info(r))
            if len(data) < 100:
                break
        return repos

    def get_repo(self, full_name: str) -> RepoInfo | None:
        data = self._api_json(f"/repos/{full_name}")
        return _repo_info(data) if data else None

    def tree(self, full_name: str, branch: str) -> tuple[list[TreeEntry], bool]:
        data = self._api_json(f"/repos/{full_name}/git/trees/{quote(branch, safe='')}?recursive=1")
        if not data:
            return [], False
        entries = [
            TreeEntry(t["path"], int(t.get("size") or 0)) for t in data.get("tree", []) if t.get("type") == "blob"
        ]
        return entries, bool(data.get("truncated"))

    def raw_file(self, full_name: str, branch: str, path: str, max_bytes: int = 400_000) -> str | None:
        url = f"{self.raw}/{full_name}/{quote(branch, safe='')}/{quote(path)}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            res = fetch(url, headers=headers, accept="text/plain,*/*", max_bytes=max_bytes, allow_status=(200, 404))
        except ExternalFetchError:
            return None
        if res.status_code != 200:
            return None
        return res.body.decode("utf-8", errors="replace")


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _repo_info(r: dict) -> RepoInfo:
    return RepoInfo(
        name=r["name"],
        full_name=r["full_name"],
        description=r.get("description") or "",
        html_url=r.get("html_url") or "",
        homepage=(r.get("homepage") or "").strip(),
        private=bool(r.get("private")),
        fork=bool(r.get("fork")),
        archived=bool(r.get("archived")),
        default_branch=r.get("default_branch") or "main",
        pushed_at=_parse_dt(r.get("pushed_at")),
        stars=int(r.get("stargazers_count") or 0),
        topics=list(r.get("topics") or []),
        language=r.get("language") or "",
    )


GITHUB_PROFILE_RX = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/?$")
REPO_URL_RX = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


def parse_username(value: str) -> str:
    value = (value or "").strip()
    m = GITHUB_PROFILE_RX.match(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9-]{1,39}", value):
        return value
    raise AppError(
        "Enter a GitHub username or profile URL such as https://github.com/octocat.", code="invalid_github_username"
    )


def parse_repo_url(value: str) -> str:
    m = REPO_URL_RX.match((value or "").strip())
    if not m:
        raise AppError("Enter a repository URL such as https://github.com/owner/repo.", code="invalid_repo_url")
    return f"{m.group(1)}/{m.group(2)}"
