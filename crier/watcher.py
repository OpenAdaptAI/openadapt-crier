"""GitHub event polling to detect new commits, releases, PRs, and blog posts."""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx

from crier import filters
from crier.allowlist import (
    BLOG_POSTS_PATH,
    DEFAULT_BLOG_REPOS,
    DEFAULT_RELEASE_REPOS,
    blog_public_url,
    is_blog_repo,
    is_release_repo,
)
from crier.db import get_last_seen_sha, update_last_seen_sha

logger = logging.getLogger(__name__)

_FRONT_MATTER_TITLE = re.compile(
    r"^title:\s*[\"']?(.*?)[\"']?\s*$", re.IGNORECASE | re.MULTILINE
)
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass
class GitHubEvent:
    """Represents a detected GitHub event (commit, release, PR, or blog post)."""

    repo: str
    event_type: str  # "push", "release", "pr_merged", "blog_post"
    ref: str  # commit SHA, tag name, PR number, or post slug
    payload: dict = field(default_factory=dict)


class GitHubWatcher:
    """Watches GitHub repos for new commits, releases, merged PRs, and blog posts."""

    def __init__(
        self,
        github_token: str,
        db_path: str,
        release_repos: Iterable[str] | None = None,
        blog_repos: Iterable[str] | None = None,
    ) -> None:
        self._token = github_token
        self._db_path = db_path
        self._release_repos = tuple(release_repos or DEFAULT_RELEASE_REPOS)
        self._blog_repos = tuple(blog_repos or DEFAULT_BLOG_REPOS)
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

    async def poll_repos(self, repos: list[str]) -> list[GitHubEvent]:
        """Poll a list of repos for new events since the last seen SHA.

        Returns a list of GitHubEvent objects for any new activity detected.
        """
        all_events: list[GitHubEvent] = []
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            for repo in repos:
                try:
                    events = await self._poll_single_repo(client, repo)
                    all_events.extend(events)
                except Exception:
                    logger.exception("Error polling repo %s", repo)
        return all_events

    def _is_release_repo(self, repo: str) -> bool:
        return is_release_repo(repo, self._release_repos)

    def _is_blog_repo(self, repo: str) -> bool:
        return is_blog_repo(repo, self._blog_repos)

    async def _poll_single_repo(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Poll a single repo for allowlisted announcement events.

        Product repos: tagged releases only. Blog repo: new posts only.
        Other repos are skipped so commits, chore PRs, and research-repo
        noise never enter the scorer.
        """
        if self._is_blog_repo(repo):
            return await self._check_blog_posts(client, repo)
        if self._is_release_repo(repo):
            return await self._check_releases(client, repo)
        logger.info("Skipping %s: not on the announcement allowlist", repo)
        return []

    async def fetch_latest_announcement(self, repo: str) -> GitHubEvent | None:
        """Return the latest allowlisted event without updating tracking.

        Used by `crier draft` so a local draft does not depend on poll state.
        """
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            if self._is_blog_repo(repo):
                slugs = await self._list_blog_slugs(client, repo)
                if not slugs:  # None on API error, [] if the posts dir is empty
                    return None
                slug = _newest_slug(slugs)
                return await self._blog_event_for_slug(client, repo, slug)
            if self._is_release_repo(repo):
                published = await self._list_published_releases(client, repo)
                if not published:
                    return None
                return _release_event(repo, published[0])
        return None

    async def _check_commits(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Check for new commits on the default branch since the last seen SHA."""
        last_sha = await get_last_seen_sha(self._db_path, repo)

        url = f"https://api.github.com/repos/{repo}/commits"
        params: dict[str, str | int] = {"per_page": 50}

        response = await client.get(url, params=params)
        if response.status_code != 200:
            logger.warning(
                "GitHub API returned %d for %s commits: %s",
                response.status_code,
                repo,
                response.text[:200],
            )
            return []

        commits_data = response.json()
        if not commits_data:
            return []

        # Update the last seen SHA to the newest commit
        newest_sha = commits_data[0]["sha"]
        if newest_sha == last_sha:
            return []

        # Collect new commits (those after last_sha)
        new_commits = []
        for commit_data in commits_data:
            sha = commit_data["sha"]
            if sha == last_sha:
                break

            message = commit_data.get("commit", {}).get("message", "")
            author_login = ""
            if commit_data.get("author"):
                author_login = commit_data["author"].get("login", "")
            elif commit_data.get("commit", {}).get("author"):
                author_login = commit_data["commit"]["author"].get("name", "")

            # Apply filters
            if filters.should_skip_commit(message, author_login):
                logger.debug("Skipping commit %s: filtered", sha[:8])
                continue

            new_commits.append({
                "sha": sha,
                "message": message.split("\n")[0],  # first line only
                "author": author_login,
                "date": commit_data.get("commit", {})
                .get("author", {})
                .get("date", ""),
            })

        # Update tracking
        await update_last_seen_sha(self._db_path, repo, newest_sha)

        if not new_commits:
            return []

        # Group all new commits into a single push event
        return [
            GitHubEvent(
                repo=repo,
                event_type="push",
                ref=newest_sha,
                payload={"commits": new_commits},
            )
        ]

    async def _check_releases(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Check for new tagged, non-prerelease releases since the last poll."""
        published = await self._list_published_releases(client, repo)
        if not published:
            return []

        tracking_key = f"{repo}:release"
        last_tag = await get_last_seen_sha(self._db_path, tracking_key)
        latest_tag = published[0].get("tag_name", "")

        # First poll seeds the cursor so historical releases are not drafted.
        if not last_tag:
            if latest_tag:
                await update_last_seen_sha(self._db_path, tracking_key, latest_tag)
            return []

        events: list[GitHubEvent] = []
        for release in published:
            tag = release.get("tag_name", "")
            if tag == last_tag:
                break
            events.append(_release_event(repo, release))

        if latest_tag:
            await update_last_seen_sha(self._db_path, tracking_key, latest_tag)

        return events

    async def _list_published_releases(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[dict]:
        url = f"https://api.github.com/repos/{repo}/releases"
        response = await client.get(url, params={"per_page": 5})
        if response.status_code != 200:
            logger.warning(
                "GitHub API returned %d for %s releases",
                response.status_code,
                repo,
            )
            return []
        releases_data = response.json()
        if not releases_data:
            return []
        return [
            r
            for r in releases_data
            if not r.get("draft") and not r.get("prerelease") and r.get("tag_name")
        ]

    async def _check_blog_posts(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Detect new Hugo posts under content/posts/."""
        slugs = await self._list_blog_slugs(client, repo)
        if slugs is None:
            return []

        tracking_key = f"{repo}:blog"
        last_seen = await get_last_seen_sha(self._db_path, tracking_key)
        encoded = json.dumps(sorted(slugs))

        if not last_seen:
            # Seed even when the posts dir is empty so the first real post is emitted.
            await update_last_seen_sha(self._db_path, tracking_key, encoded)
            return []

        try:
            known = set(json.loads(last_seen))
        except json.JSONDecodeError:
            known = {last_seen} if last_seen else set()

        new_slugs = [s for s in slugs if s not in known]
        await update_last_seen_sha(self._db_path, tracking_key, encoded)

        events: list[GitHubEvent] = []
        for slug in new_slugs:
            event = await self._blog_event_for_slug(client, repo, slug)
            if event is not None:
                events.append(event)
        return events

    async def _list_blog_slugs(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[str] | None:
        url = f"https://api.github.com/repos/{repo}/contents/{BLOG_POSTS_PATH}"
        response = await client.get(url)
        if response.status_code != 200:
            logger.warning(
                "GitHub API returned %d for %s %s",
                response.status_code,
                repo,
                BLOG_POSTS_PATH,
            )
            return None
        entries = response.json()
        if not isinstance(entries, list):
            return []
        slugs: list[str] = []
        for entry in entries:
            name = entry.get("name") or ""
            entry_type = entry.get("type")
            if entry_type == "dir" and name:
                slugs.append(name)
            elif entry_type == "file" and name.endswith(".md") and name != "_index.md":
                slugs.append(name.removesuffix(".md"))
        return slugs

    async def _blog_event_for_slug(
        self, client: httpx.AsyncClient, repo: str, slug: str
    ) -> GitHubEvent | None:
        title = await self._fetch_blog_title(client, repo, slug)
        url = blog_public_url(slug)
        return GitHubEvent(
            repo=repo,
            event_type="blog_post",
            ref=slug,
            payload={
                "slug": slug,
                "title": title or slug,
                "url": url,
                "html_url": url,
            },
        )

    async def _fetch_blog_title(
        self, client: httpx.AsyncClient, repo: str, slug: str
    ) -> str:
        candidates = [
            f"{BLOG_POSTS_PATH}/{slug}/index.md",
            f"{BLOG_POSTS_PATH}/{slug}.md",
        ]
        for path in candidates:
            url = f"https://api.github.com/repos/{repo}/contents/{path}"
            response = await client.get(url)
            if response.status_code != 200:
                continue
            data = response.json()
            text = _decode_github_file(data)
            if not text:
                continue
            title = _parse_markdown_title(text)
            if title:
                return title
        return ""

    async def _check_merged_prs(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Check for recently merged PRs."""
        url = f"https://api.github.com/repos/{repo}/pulls"
        params: dict[str, str | int] = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 10,
        }

        response = await client.get(url, params=params)
        if response.status_code != 200:
            logger.warning(
                "GitHub API returned %d for %s PRs",
                response.status_code,
                repo,
            )
            return []

        prs_data = response.json()
        if not prs_data:
            return []

        events: list[GitHubEvent] = []
        tracking_key = f"{repo}:pr"
        last_pr_number = await get_last_seen_sha(self._db_path, tracking_key)
        last_pr_int = int(last_pr_number) if last_pr_number else 0

        newest_merged_pr = 0
        for pr in prs_data:
            if not pr.get("merged_at"):
                continue

            pr_number = pr.get("number", 0)
            if pr_number <= last_pr_int:
                continue

            if pr_number > newest_merged_pr:
                newest_merged_pr = pr_number

            author = pr.get("user", {}).get("login", "")
            title = pr.get("title", "")

            # Skip bot PRs
            if filters.should_skip_commit(title, author):
                continue

            events.append(
                GitHubEvent(
                    repo=repo,
                    event_type="pr_merged",
                    ref=str(pr_number),
                    payload={
                        "number": pr_number,
                        "title": title,
                        "body": (pr.get("body") or "")[:500],
                        "author": author,
                        "merged_at": pr.get("merged_at", ""),
                        "html_url": pr.get("html_url", ""),
                    },
                )
            )

        # Update tracking to the highest merged PR number
        if newest_merged_pr > last_pr_int:
            await update_last_seen_sha(
                self._db_path, tracking_key, str(newest_merged_pr)
            )

        return events


def _release_event(repo: str, release: dict) -> GitHubEvent:
    tag = release.get("tag_name", "")
    return GitHubEvent(
        repo=repo,
        event_type="release",
        ref=tag,
        payload={
            "tag": tag,
            "name": release.get("name", "") or "",
            "body": release.get("body", "") or "",
            "published_at": release.get("published_at", "") or "",
            "html_url": release.get("html_url", "") or "",
            "prerelease": bool(release.get("prerelease")),
            "draft": bool(release.get("draft")),
        },
    )


def _newest_slug(slugs: list[str]) -> str:
    dated = [s for s in slugs if _DATE_PREFIX.match(s)]
    if dated:
        return sorted(dated)[-1]
    return sorted(slugs)[-1]


def _decode_github_file(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    content = data.get("content") or ""
    encoding = data.get("encoding") or ""
    if encoding == "base64" and content:
        try:
            return base64.b64decode(content).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    if isinstance(content, str) and content:
        return content
    return ""


def _parse_markdown_title(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            match = _FRONT_MATTER_TITLE.search(parts[1])
            if match:
                return match.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def events_to_json(events: list[GitHubEvent]) -> str:
    """Serialize a list of GitHubEvent objects to JSON for storage."""
    return json.dumps(
        [
            {
                "repo": e.repo,
                "event_type": e.event_type,
                "ref": e.ref,
                "payload": e.payload,
            }
            for e in events
        ]
    )
