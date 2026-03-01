"""Tests for the GitHub watcher module with mock API responses."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from crier.watcher import GitHubEvent, GitHubWatcher, events_to_json


@pytest.fixture
async def db_path(tmp_path):
    """Create an initialized temporary database."""
    from crier.db import init_db

    path = str(tmp_path / "test_watcher.db")
    await init_db(path)
    return path


def _mock_response(status_code: int, json_data) -> httpx.Response:
    """Create a mock httpx.Response."""
    response = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )
    return response


class TestGitHubWatcher:
    @pytest.mark.asyncio
    async def test_poll_repos_empty(self, db_path):
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)

        with patch.object(
            watcher, "_poll_single_repo", new_callable=AsyncMock, return_value=[]
        ):
            events = await watcher.poll_repos(["org/repo"])
            assert events == []

    @pytest.mark.asyncio
    async def test_poll_repos_returns_events(self, db_path):
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        expected = [
            GitHubEvent(
                repo="org/repo",
                event_type="push",
                ref="abc123",
                payload={"commits": [{"sha": "abc123", "message": "feat: X"}]},
            )
        ]

        with patch.object(
            watcher,
            "_poll_single_repo",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            events = await watcher.poll_repos(["org/repo"])
            assert len(events) == 1
            assert events[0].event_type == "push"

    @pytest.mark.asyncio
    async def test_poll_repos_handles_error(self, db_path):
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)

        with patch.object(
            watcher,
            "_poll_single_repo",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            events = await watcher.poll_repos(["org/repo"])
            assert events == []

    @pytest.mark.asyncio
    async def test_check_commits_no_new(self, db_path):
        """Returns empty when HEAD matches last seen SHA."""
        from crier.db import update_last_seen_sha

        await update_last_seen_sha(db_path, "org/repo", "abc123")

        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [{"sha": "abc123", "commit": {"message": "old", "author": {"name": "A", "date": ""}}, "author": {"login": "A"}}],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_commits(mock_client, "org/repo")
        assert events == []

    @pytest.mark.asyncio
    async def test_check_commits_new_commits(self, db_path):
        """Returns push event when new commits are found."""
        from crier.db import update_last_seen_sha

        await update_last_seen_sha(db_path, "org/repo", "old_sha")

        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "sha": "new_sha",
                    "commit": {
                        "message": "feat: add login",
                        "author": {"name": "Alice", "date": "2026-03-01T10:00:00Z"},
                    },
                    "author": {"login": "alice"},
                },
                {
                    "sha": "old_sha",
                    "commit": {
                        "message": "old commit",
                        "author": {"name": "Bob", "date": "2026-02-28T10:00:00Z"},
                    },
                    "author": {"login": "bob"},
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_commits(mock_client, "org/repo")
        assert len(events) == 1
        assert events[0].event_type == "push"
        assert len(events[0].payload["commits"]) == 1
        assert events[0].payload["commits"][0]["sha"] == "new_sha"

    @pytest.mark.asyncio
    async def test_check_commits_filters_merge_commits(self, db_path):
        """Merge commits are filtered out by should_skip_commit."""
        from crier.db import update_last_seen_sha

        await update_last_seen_sha(db_path, "org/repo", "old_sha")

        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "sha": "merge_sha",
                    "commit": {
                        "message": "Merge branch 'feature' into main",
                        "author": {"name": "Alice", "date": "2026-03-01T10:00:00Z"},
                    },
                    "author": {"login": "alice"},
                },
                {
                    "sha": "old_sha",
                    "commit": {
                        "message": "old commit",
                        "author": {"name": "Bob", "date": ""},
                    },
                    "author": {"login": "bob"},
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_commits(mock_client, "org/repo")
        assert events == []

    @pytest.mark.asyncio
    async def test_check_commits_first_poll(self, db_path):
        """First poll with no last-seen SHA collects all commits."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "sha": "sha1",
                    "commit": {
                        "message": "feat: something",
                        "author": {"name": "Alice", "date": ""},
                    },
                    "author": {"login": "alice"},
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_commits(mock_client, "org/repo")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_check_commits_api_error(self, db_path):
        """Returns empty on API error."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(403, {"message": "Forbidden"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_commits(mock_client, "org/repo")
        assert events == []

    @pytest.mark.asyncio
    async def test_check_releases_new(self, db_path):
        """Detects new releases."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "tag_name": "v1.0.0",
                    "name": "Release 1.0.0",
                    "body": "First stable release",
                    "published_at": "2026-03-01T12:00:00Z",
                    "html_url": "https://github.com/org/repo/releases/v1.0.0",
                    "draft": False,
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_releases(mock_client, "org/repo")
        assert len(events) == 1
        assert events[0].event_type == "release"
        assert events[0].ref == "v1.0.0"

    @pytest.mark.asyncio
    async def test_check_releases_skips_drafts(self, db_path):
        """Draft releases are skipped."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "tag_name": "v2.0.0-beta",
                    "name": "Beta",
                    "body": "",
                    "published_at": "",
                    "html_url": "",
                    "draft": True,
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_releases(mock_client, "org/repo")
        assert events == []

    @pytest.mark.asyncio
    async def test_check_merged_prs(self, db_path):
        """Detects newly merged PRs."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "number": 42,
                    "title": "feat: add auth",
                    "body": "Added authentication flow",
                    "merged_at": "2026-03-01T10:00:00Z",
                    "html_url": "https://github.com/org/repo/pull/42",
                    "user": {"login": "alice"},
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_merged_prs(mock_client, "org/repo")
        assert len(events) == 1
        assert events[0].event_type == "pr_merged"
        assert events[0].ref == "42"

    @pytest.mark.asyncio
    async def test_check_merged_prs_skips_unmerged(self, db_path):
        """Closed but unmerged PRs are skipped."""
        watcher = GitHubWatcher(github_token="ghp_test", db_path=db_path)
        mock_response = _mock_response(
            200,
            [
                {
                    "number": 43,
                    "title": "feat: rejected PR",
                    "body": "",
                    "merged_at": None,
                    "html_url": "",
                    "user": {"login": "bob"},
                },
            ],
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await watcher._check_merged_prs(mock_client, "org/repo")
        assert events == []


class TestEventsToJson:
    def test_serialize_events(self):
        events = [
            GitHubEvent(
                repo="org/repo",
                event_type="push",
                ref="abc123",
                payload={"commits": [{"sha": "abc123"}]},
            ),
        ]
        result = events_to_json(events)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["repo"] == "org/repo"
        assert parsed[0]["event_type"] == "push"

    def test_serialize_empty(self):
        result = events_to_json([])
        assert json.loads(result) == []
