"""Tests for the Telegram approval bot with mock telegram objects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crier.bot import CrierBot, _escape_html
from crier.config import CrierSettings


@pytest.fixture
def settings():
    """Create test settings."""
    return CrierSettings(
        _env_file=None,
        telegram_bot_token="123456:ABC-DEF",
        telegram_owner_id=999,
        repos="org/repo",
        db_path=":memory:",
        dry_run=True,
    )


@pytest.fixture
def bot(settings):
    """Create a CrierBot instance."""
    return CrierBot(settings)


class TestCrierBotSetup:
    def test_setup_returns_application(self, bot):
        app = bot.setup()
        assert app is not None

    def test_setup_registers_handlers(self, bot):
        app = bot.setup()
        # Application should have handlers registered
        assert len(app.handlers) > 0


class TestAuthorization:
    def test_authorized_user(self, bot):
        assert bot._is_authorized(999)

    def test_unauthorized_user(self, bot):
        assert not bot._is_authorized(123)

    def test_unauthorized_zero(self, bot):
        assert not bot._is_authorized(0)


class TestFormatDraftMessage:
    def test_format_push_event(self, bot):
        event_info = {
            "repo": "org/repo",
            "event_type": "push",
            "ref": "abc123",
            "commits": [
                {"sha": "abc12345", "author": "alice", "message": "feat: add login"},
            ],
        }
        drafts = {
            "twitter": "New login feature shipped!",
            "discord": "**Login Feature**\nAdded OAuth login flow.",
        }
        text = bot._format_draft_message(event_info, drafts)
        assert "push" in text
        assert "org/repo" in text
        assert "abc12345" in text
        assert "Twitter" in text
        assert "Discord" in text

    def test_format_release_event(self, bot):
        event_info = {
            "repo": "org/repo",
            "event_type": "release",
            "ref": "v1.0.0",
            "tag": "v1.0.0",
            "name": "Release 1.0",
        }
        drafts = {"twitter": "v1.0 is out!"}
        text = bot._format_draft_message(event_info, drafts)
        assert "release" in text
        assert "v1.0.0" in text

    def test_format_pr_event(self, bot):
        event_info = {
            "repo": "org/repo",
            "event_type": "pr_merged",
            "ref": "42",
            "title": "feat: auth flow",
            "author": "carol",
        }
        drafts = {"discord": "Auth flow merged!"}
        text = bot._format_draft_message(event_info, drafts)
        assert "pr_merged" in text
        assert "#42" in text

    def test_format_escapes_html(self, bot):
        event_info = {
            "repo": "org/repo",
            "event_type": "push",
            "ref": "abc",
            "commits": [
                {"sha": "abc12345", "author": "alice", "message": "fix: <script>alert</script>"},
            ],
        }
        drafts = {"twitter": "Fixed <script> issue"}
        text = bot._format_draft_message(event_info, drafts)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_format_many_commits_truncates(self, bot):
        commits = [
            {"sha": f"sha{i:04d}xx", "author": "alice", "message": f"commit {i}"}
            for i in range(10)
        ]
        event_info = {
            "repo": "org/repo",
            "event_type": "push",
            "ref": "latest",
            "commits": commits,
        }
        drafts = {"twitter": "lots of commits"}
        text = bot._format_draft_message(event_info, drafts)
        assert "5 more commits" in text


class TestBuildKeyboard:
    def test_keyboard_has_action_buttons(self, bot):
        keyboard = bot._build_keyboard("evt-1", {"twitter", "discord"})
        # First row: Approve, Edit, Reject
        assert len(keyboard.inline_keyboard) == 2
        row1 = keyboard.inline_keyboard[0]
        assert len(row1) == 3
        texts = [btn.text for btn in row1]
        assert "Approve" in texts
        assert "Edit" in texts
        assert "Reject" in texts

    def test_keyboard_has_platform_toggles(self, bot):
        keyboard = bot._build_keyboard("evt-1", {"twitter", "discord"})
        row2 = keyboard.inline_keyboard[1]
        texts = [btn.text for btn in row2]
        # Should have toggle buttons for all platforms
        assert any("[on]" in t for t in texts)

    def test_keyboard_toggle_off(self, bot):
        keyboard = bot._build_keyboard("evt-1", {"twitter"})
        row2 = keyboard.inline_keyboard[1]
        texts = [btn.text for btn in row2]
        # Discord should be off, Twitter on
        twitter_btn = [t for t in texts if "Twitter" in t][0]
        discord_btn = [t for t in texts if "Discord" in t][0]
        assert "[on]" in twitter_btn
        assert "[off]" in discord_btn

    def test_keyboard_callback_data_format(self, bot):
        keyboard = bot._build_keyboard("evt-123", {"twitter"})
        row1 = keyboard.inline_keyboard[0]
        approve_btn = [b for b in row1 if b.text == "Approve"][0]
        assert approve_btn.callback_data == "approve:evt-123"

        row2 = keyboard.inline_keyboard[1]
        toggle_btns = [b for b in row2 if "toggle:" in b.callback_data]
        assert len(toggle_btns) == 3
        # Verify callback data includes event_id and platform
        data_parts = [b.callback_data.split(":") for b in toggle_btns]
        for parts in data_parts:
            assert parts[0] == "toggle"
            assert parts[1] == "evt-123"
            assert parts[2] in ("twitter", "discord", "linkedin")


class TestCallbackApprove:
    @pytest.mark.asyncio
    async def test_callback_approve_unauthorized(self, bot):
        """Unauthorized users get denied."""
        bot.setup()

        query = MagicMock()
        query.from_user = MagicMock()
        query.from_user.id = 123  # not the owner
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()

        await bot._callback_approve(update, context)
        query.answer.assert_called_with("Unauthorized.")


class TestCallbackReject:
    @pytest.mark.asyncio
    async def test_callback_reject_unauthorized(self, bot):
        """Unauthorized users get denied."""
        bot.setup()

        query = MagicMock()
        query.from_user = MagicMock()
        query.from_user.id = 123
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()

        await bot._callback_reject(update, context)
        query.answer.assert_called_with("Unauthorized.")


class TestCallbackTogglePlatform:
    @pytest.mark.asyncio
    async def test_toggle_platform_unauthorized(self, bot):
        """Unauthorized users get denied."""
        bot.setup()

        query = MagicMock()
        query.from_user = MagicMock()
        query.from_user.id = 123
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()

        await bot._callback_toggle_platform(update, context)
        query.answer.assert_called_with("Unauthorized.")

    @pytest.mark.asyncio
    async def test_toggle_platform_on_off(self, bot):
        """Toggle platform on and off."""
        bot.setup()
        event_id = "test-event"
        bot._platform_toggles[event_id] = {"twitter", "discord"}

        query = MagicMock()
        query.from_user = MagicMock()
        query.from_user.id = 999  # authorized
        query.data = f"toggle:{event_id}:twitter"
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()

        # Toggle off
        await bot._callback_toggle_platform(update, context)
        assert "twitter" not in bot._platform_toggles[event_id]
        query.answer.assert_called_with("Twitter disabled")

        # Toggle back on
        await bot._callback_toggle_platform(update, context)
        assert "twitter" in bot._platform_toggles[event_id]
        query.answer.assert_called_with("Twitter enabled")


class TestEscapeHtml:
    def test_escape_angle_brackets(self):
        assert _escape_html("<script>") == "&lt;script&gt;"

    def test_escape_ampersand(self):
        assert _escape_html("a & b") == "a &amp; b"

    def test_no_escape_needed(self):
        assert _escape_html("hello world") == "hello world"

    def test_multiple_escapes(self):
        assert _escape_html("<a & b>") == "&lt;a &amp; b&gt;"
