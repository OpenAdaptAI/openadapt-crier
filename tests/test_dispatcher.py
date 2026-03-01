"""Tests for the dispatcher module with mock herald publisher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from crier.config import CrierSettings
from crier.db import create_draft, create_event, get_draft_by_id, init_db
from crier.dispatcher import _build_herald_settings, post_approved_draft, post_single_draft


@pytest.fixture
def settings(tmp_path):
    """Create test settings with a temp database."""
    db_path = str(tmp_path / "test_dispatch.db")
    return CrierSettings(
        _env_file=None,
        telegram_bot_token="123:ABC",
        telegram_owner_id=999,
        repos="org/repo",
        db_path=db_path,
        discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        twitter_consumer_key="ck",
        twitter_consumer_secret="cs",
        twitter_access_token="at",
        twitter_access_token_secret="ats",
        dry_run=False,
    )


@pytest.fixture
async def db_path(settings):
    """Initialize the database."""
    await init_db(settings.db_path)
    return settings.db_path


class TestBuildHeraldSettings:
    def test_maps_crier_to_herald(self):
        crier_settings = CrierSettings(
            _env_file=None,
            discord_webhook_url="https://discord.com/test",
            twitter_consumer_key="ck",
            twitter_consumer_secret="cs",
            twitter_access_token="at",
            twitter_access_token_secret="ats",
            linkedin_access_token="li-token",
            anthropic_api_key="sk-ant-123",
            github_token="ghp_test",
            repos="org/repo",
            dry_run=True,
        )
        herald_settings = _build_herald_settings(crier_settings)

        assert herald_settings.discord_webhook_url == "https://discord.com/test"
        assert herald_settings.twitter_consumer_key == "ck"
        assert herald_settings.twitter_consumer_secret == "cs"
        assert herald_settings.twitter_access_token == "at"
        assert herald_settings.twitter_access_token_secret == "ats"
        assert herald_settings.linkedin_access_token == "li-token"
        assert herald_settings.anthropic_api_key == "sk-ant-123"
        assert herald_settings.dry_run is True


class TestPostApprovedDraft:
    @pytest.mark.asyncio
    async def test_post_approved_draft_dry_run(self, settings, db_path):
        settings.dry_run = True

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="discord", content="Test discord post"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Test tweet"
        )

        results = await post_approved_draft(
            settings, event_id, platforms={"discord", "twitter"}
        )

        assert "discord" in results
        assert results["discord"]["success"] == "true"
        assert "DRY RUN" in results["discord"]["message"]
        assert "twitter" in results
        assert results["twitter"]["success"] == "true"

    @pytest.mark.asyncio
    async def test_post_approved_draft_no_drafts(self, settings, db_path):
        results = await post_approved_draft(
            settings, "nonexistent-event", platforms={"discord"}
        )
        assert results == {}

    @pytest.mark.asyncio
    async def test_post_approved_draft_filters_platforms(self, settings, db_path):
        settings.dry_run = True

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="discord", content="Discord post"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        # Only post to discord
        results = await post_approved_draft(
            settings, event_id, platforms={"discord"}
        )

        assert "discord" in results
        assert "twitter" not in results

    @pytest.mark.asyncio
    @patch("crier.dispatcher.publish_content")
    @patch("crier.dispatcher.get_publishers")
    async def test_post_approved_draft_success(
        self, mock_get_publishers, mock_publish, settings, db_path
    ):
        from herald.platforms.base import PublishResult
        from herald.publisher import PublishReport

        mock_publisher = MagicMock()
        mock_publisher.platform_name = "discord"
        mock_get_publishers.return_value = [mock_publisher]

        mock_publish.return_value = PublishReport(
            results=[
                PublishResult(
                    platform="discord",
                    success=True,
                    message="Posted",
                    url="https://discord.com/msg/123",
                )
            ]
        )

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="discord", content="Test post"
        )

        results = await post_approved_draft(
            settings, event_id, platforms={"discord"}
        )

        assert results["discord"]["success"] == "true"
        assert results["discord"]["url"] == "https://discord.com/msg/123"

        # Verify draft status was updated
        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["status"] == "posted"
        assert draft["post_url"] == "https://discord.com/msg/123"

    @pytest.mark.asyncio
    @patch("crier.dispatcher.publish_content")
    @patch("crier.dispatcher.get_publishers")
    async def test_post_approved_draft_failure(
        self, mock_get_publishers, mock_publish, settings, db_path
    ):
        from herald.platforms.base import PublishResult
        from herald.publisher import PublishReport

        mock_publisher = MagicMock()
        mock_publisher.platform_name = "twitter"
        mock_get_publishers.return_value = [mock_publisher]

        mock_publish.return_value = PublishReport(
            results=[
                PublishResult(
                    platform="twitter",
                    success=False,
                    message="Rate limited",
                )
            ]
        )

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        results = await post_approved_draft(
            settings, event_id, platforms={"twitter"}
        )

        assert results["twitter"]["success"] == "false"
        assert "Rate limited" in results["twitter"]["message"]

        # Draft should remain pending with error
        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["status"] == "pending"
        assert draft["error"] == "Rate limited"


class TestPostSingleDraft:
    @pytest.mark.asyncio
    async def test_post_single_draft_dry_run(self, settings, db_path):
        settings.dry_run = True

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="discord", content="Test"
        )

        result = await post_single_draft(settings, draft_id)
        assert result["success"] == "true"
        assert "DRY RUN" in result["message"]

    @pytest.mark.asyncio
    async def test_post_single_draft_not_found(self, settings, db_path):
        result = await post_single_draft(settings, "nonexistent-id")
        assert result["success"] == "false"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @patch("crier.dispatcher.publish_content")
    @patch("crier.dispatcher.get_publishers")
    async def test_post_single_draft_no_publisher(
        self, mock_get_publishers, mock_publish, settings, db_path
    ):
        mock_get_publishers.return_value = []  # no publishers configured

        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="linkedin", content="Post"
        )

        result = await post_single_draft(settings, draft_id)
        assert result["success"] == "false"
        assert "No publisher" in result["message"]
