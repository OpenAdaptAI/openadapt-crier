"""Ingest path: missing Anthropic key is fail-closed and still drafts allowlisted events."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from crier.config import CrierSettings
from crier.db import get_drafts_by_event, init_db
from crier.pipeline import ingest_event
from crier.watcher import GitHubEvent


@pytest.fixture
async def settings(tmp_path):
    db_path = str(tmp_path / "pipeline.db")
    await init_db(db_path)
    return CrierSettings(
        _env_file=None,
        db_path=db_path,
        anthropic_api_key="",
        use_claude=False,
        repos="OpenAdaptAI/openadapt-flow",
    )


class TestIngestFailClosed:
    @pytest.mark.asyncio
    async def test_commit_noise_creates_no_drafts(self, settings):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="push",
            ref="abc123",
            payload={"commits": [{"sha": "abc123", "message": "feat: add login"}]},
        )
        with patch("crier.drafter.anthropic.Anthropic") as mock_cls:
            event_id = await ingest_event(event, settings)
            mock_cls.assert_not_called()
        assert event_id is None

    @pytest.mark.asyncio
    async def test_chore_pr_creates_no_drafts(self, settings):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="pr_merged",
            ref="8",
            payload={"number": 8, "title": "chore: ignore me"},
        )
        event_id = await ingest_event(event, settings)
        assert event_id is None

    @pytest.mark.asyncio
    async def test_allowlisted_release_drafts_without_key(self, settings):
        url = "https://github.com/OpenAdaptAI/openadapt-flow/releases/tag/v1.20.0"
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="release",
            ref="v1.20.0",
            payload={"tag": "v1.20.0", "html_url": url},
        )
        with patch("crier.drafter.anthropic.Anthropic") as mock_cls:
            event_id = await ingest_event(event, settings)
            mock_cls.assert_not_called()
        assert event_id is not None
        drafts = await get_drafts_by_event(settings.db_path, event_id)
        platforms = {d["platform"] for d in drafts}
        assert platforms >= {"twitter", "discord", "linkedin"}
        twitter = next(d for d in drafts if d["platform"] == "twitter")
        assert twitter["interestingness_score"] == 9.0
        assert "pip install openadapt" in twitter["content"]
        assert url in twitter["content"]

    @pytest.mark.asyncio
    async def test_allowlisted_blog_drafts_without_key(self, settings):
        url = "https://blog.openadapt.ai/posts/the-500th-run/"
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-blog",
            event_type="blog_post",
            ref="the-500th-run",
            payload={"slug": "the-500th-run", "title": "The 500th run", "url": url},
        )
        event_id = await ingest_event(event, settings)
        assert event_id is not None
        drafts = await get_drafts_by_event(settings.db_path, event_id)
        discord = next(d for d in drafts if d["platform"] == "discord")
        assert "VERIFIED" in discord["content"]
        assert url in discord["content"]
