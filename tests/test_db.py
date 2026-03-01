"""Tests for the database module using in-memory SQLite."""

from __future__ import annotations

import pytest

from crier.db import (
    create_draft,
    create_event,
    create_feedback,
    get_draft_by_id,
    get_drafts_by_event,
    get_last_seen_sha,
    get_pending_drafts,
    get_recent_drafts,
    init_db,
    update_draft_content,
    update_draft_status,
    update_last_seen_sha,
)

# Use a unique in-memory DB per test via tmp_path
DB_PATH = ":memory:"


@pytest.fixture
async def db_path(tmp_path):
    """Create an initialized temporary database."""
    path = str(tmp_path / "test_crier.db")
    await init_db(path)
    return path


class TestInitDb:
    @pytest.mark.asyncio
    async def test_init_creates_tables(self, tmp_path):
        path = str(tmp_path / "init_test.db")
        await init_db(path)

        import aiosqlite

        async with aiosqlite.connect(path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in await cursor.fetchall()]

        assert "events" in tables
        assert "drafts" in tables
        assert "feedback" in tables
        assert "tracking" in tables

    @pytest.mark.asyncio
    async def test_init_idempotent(self, tmp_path):
        path = str(tmp_path / "idempotent_test.db")
        await init_db(path)
        await init_db(path)  # should not raise


class TestEvents:
    @pytest.mark.asyncio
    async def test_create_event(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc123"
        )
        assert event_id
        assert len(event_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_create_event_with_payload(self, db_path):
        event_id = await create_event(
            db_path,
            repo="org/repo",
            event_type="release",
            ref="v1.0.0",
            payload='{"tag": "v1.0.0"}',
        )
        assert event_id


class TestDrafts:
    @pytest.mark.asyncio
    async def test_create_draft(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path,
            event_id=event_id,
            platform="twitter",
            content="Test tweet",
            interestingness_score=7.5,
        )
        assert draft_id
        assert len(draft_id) == 36

    @pytest.mark.asyncio
    async def test_get_draft_by_id(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="discord", content="Test discord"
        )

        draft = await get_draft_by_id(db_path, draft_id)
        assert draft is not None
        assert draft["id"] == draft_id
        assert draft["platform"] == "discord"
        assert draft["content"] == "Test discord"
        assert draft["status"] == "pending"
        assert draft["repo"] == "org/repo"

    @pytest.mark.asyncio
    async def test_get_draft_by_id_not_found(self, db_path):
        draft = await get_draft_by_id(db_path, "nonexistent-id")
        assert draft is None

    @pytest.mark.asyncio
    async def test_get_drafts_by_event(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )
        await create_draft(
            db_path, event_id=event_id, platform="discord", content="Discord msg"
        )

        drafts = await get_drafts_by_event(db_path, event_id)
        assert len(drafts) == 2
        platforms = {d["platform"] for d in drafts}
        assert platforms == {"twitter", "discord"}

    @pytest.mark.asyncio
    async def test_get_pending_drafts(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )
        draft_id_2 = await create_draft(
            db_path, event_id=event_id, platform="discord", content="Discord"
        )

        # Mark one as posted
        await update_draft_status(db_path, draft_id_2, status="posted")

        pending = await get_pending_drafts(db_path)
        assert len(pending) == 1
        assert pending[0]["platform"] == "twitter"

    @pytest.mark.asyncio
    async def test_update_draft_status_approved(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        await update_draft_status(db_path, draft_id, status="approved")

        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["status"] == "approved"
        assert draft["approved_at"] is not None

    @pytest.mark.asyncio
    async def test_update_draft_status_posted_with_url(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        await update_draft_status(
            db_path,
            draft_id,
            status="posted",
            post_url="https://twitter.com/status/123",
        )

        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["status"] == "posted"
        assert draft["posted_at"] is not None
        assert draft["post_url"] == "https://twitter.com/status/123"

    @pytest.mark.asyncio
    async def test_update_draft_status_with_telegram_ids(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        await update_draft_status(
            db_path,
            draft_id,
            status="pending",
            telegram_message_id=42,
            telegram_chat_id=12345,
        )

        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["telegram_message_id"] == 42
        assert draft["telegram_chat_id"] == 12345

    @pytest.mark.asyncio
    async def test_update_draft_content(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Original"
        )

        await update_draft_content(db_path, draft_id, "Edited content")

        draft = await get_draft_by_id(db_path, draft_id)
        assert draft["content"] == "Edited content"
        assert draft["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_recent_drafts_by_status(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )
        draft_id_2 = await create_draft(
            db_path, event_id=event_id, platform="discord", content="Discord"
        )
        await update_draft_status(db_path, draft_id_2, status="posted")

        posted = await get_recent_drafts(db_path, status="posted", days=7)
        assert len(posted) == 1
        assert posted[0]["platform"] == "discord"

    @pytest.mark.asyncio
    async def test_get_recent_drafts_by_platform(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )
        await create_draft(
            db_path, event_id=event_id, platform="discord", content="Discord"
        )

        twitter_only = await get_recent_drafts(
            db_path, platform="twitter", days=7
        )
        assert len(twitter_only) == 1
        assert twitter_only[0]["platform"] == "twitter"


class TestFeedback:
    @pytest.mark.asyncio
    async def test_create_feedback(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        feedback_id = await create_feedback(
            db_path, draft_id=draft_id, action="approve"
        )
        assert feedback_id
        assert len(feedback_id) == 36

    @pytest.mark.asyncio
    async def test_create_feedback_with_text(self, db_path):
        event_id = await create_event(
            db_path, repo="org/repo", event_type="push", ref="abc"
        )
        draft_id = await create_draft(
            db_path, event_id=event_id, platform="twitter", content="Tweet"
        )

        feedback_id = await create_feedback(
            db_path,
            draft_id=draft_id,
            action="reject",
            user_text="Too promotional",
        )
        assert feedback_id


class TestTracking:
    @pytest.mark.asyncio
    async def test_get_last_seen_sha_none(self, db_path):
        sha = await get_last_seen_sha(db_path, "org/repo")
        assert sha is None

    @pytest.mark.asyncio
    async def test_update_and_get_last_seen_sha(self, db_path):
        await update_last_seen_sha(db_path, "org/repo", "abc123")
        sha = await get_last_seen_sha(db_path, "org/repo")
        assert sha == "abc123"

    @pytest.mark.asyncio
    async def test_update_last_seen_sha_upsert(self, db_path):
        await update_last_seen_sha(db_path, "org/repo", "abc123")
        await update_last_seen_sha(db_path, "org/repo", "def456")
        sha = await get_last_seen_sha(db_path, "org/repo")
        assert sha == "def456"

    @pytest.mark.asyncio
    async def test_tracking_per_repo(self, db_path):
        await update_last_seen_sha(db_path, "org/repo1", "sha1")
        await update_last_seen_sha(db_path, "org/repo2", "sha2")

        assert await get_last_seen_sha(db_path, "org/repo1") == "sha1"
        assert await get_last_seen_sha(db_path, "org/repo2") == "sha2"
