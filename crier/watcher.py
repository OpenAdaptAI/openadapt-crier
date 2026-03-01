"""GitHub event polling to detect new commits, releases, and PRs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from crier import filters
from crier.db import get_last_seen_sha, update_last_seen_sha

logger = logging.getLogger(__name__)


@dataclass
class GitHubEvent:
    """Represents a detected GitHub event (commit, release, or PR merge)."""

    repo: str
    event_type: str  # "push", "release", "pr_merged"
    ref: str  # commit SHA, tag name, or PR number
    payload: dict = field(default_factory=dict)


class GitHubWatcher:
    """Watches GitHub repos for new commits, releases, and merged PRs."""

    def __init__(self, github_token: str, db_path: str) -> None:
        self._token = github_token
        self._db_path = db_path
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

    async def _poll_single_repo(
        self, client: httpx.AsyncClient, repo: str
    ) -> list[GitHubEvent]:
        """Poll a single repo for new commits, releases, and merged PRs."""
        events: list[GitHubEvent] = []

        commit_events = await self._check_commits(client, repo)
        events.extend(commit_events)

        release_events = await self._check_releases(client, repo)
        events.extend(release_events)

        pr_events = await self._check_merged_prs(client, repo)
        events.extend(pr_events)

        return events

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
        """Check for new releases published since the last poll."""
        url = f"https://api.github.com/repos/{repo}/releases"
        params: dict[str, str | int] = {"per_page": 5}

        response = await client.get(url, params=params)
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

        events: list[GitHubEvent] = []
        # Track releases by tag to avoid re-announcing
        tracking_key = f"{repo}:release"
        last_tag = await get_last_seen_sha(self._db_path, tracking_key)

        for release in releases_data:
            tag = release.get("tag_name", "")
            if tag == last_tag:
                break
            if release.get("draft", False):
                continue

            events.append(
                GitHubEvent(
                    repo=repo,
                    event_type="release",
                    ref=tag,
                    payload={
                        "tag": tag,
                        "name": release.get("name", ""),
                        "body": release.get("body", ""),
                        "published_at": release.get("published_at", ""),
                        "html_url": release.get("html_url", ""),
                    },
                )
            )

        # Update tracking to the latest release tag
        if releases_data:
            latest_tag = releases_data[0].get("tag_name", "")
            if latest_tag:
                await update_last_seen_sha(self._db_path, tracking_key, latest_tag)

        return events

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
