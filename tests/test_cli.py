"""Tests for the CLI poll cycle (score gate, drafting, and approval hand-off)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crier import cli
from crier.config import CrierSettings
from crier.watcher import GitHubEvent


@pytest.fixture
def settings(tmp_path):
    """Create test settings with the default interest threshold."""
    return CrierSettings(
        _env_file=None,
        telegram_bot_token="123:ABC",
        telegram_owner_id=999,
        repos="org/repo",
        db_path=str(tmp_path / "test_cli.db"),
        anthropic_api_key="sk-test",
    )


def _push_event() -> GitHubEvent:
    return GitHubEvent(
        repo="org/repo",
        event_type="push",
        ref="abc123",
        payload={"commits": [{"sha": "abc123", "author": "alice", "message": "chore: bump"}]},
    )


def _stubs():
    watcher = MagicMock()
    watcher.poll_repos = AsyncMock(return_value=[_push_event()])
    bot = MagicMock()
    bot.send_draft_for_approval = AsyncMock()
    return watcher, bot


_DRAFTS = {"twitter": "t", "discord": "d", "linkedin": "l", "summary": "s"}


class TestPollAndDraft:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("api_key", ["", "sk-test"], ids=["no-key", "api-error"])
    async def test_poll_skips_event_when_scoring_fails(self, settings, api_key):
        """If commits can't be scored, nothing is stored, drafted, or sent."""
        settings.anthropic_api_key = api_key
        assert settings.interest_threshold == 5.0
        watcher, bot = _stubs()

        with (
            patch("crier.drafter.anthropic.Anthropic", side_effect=RuntimeError("API down")),
            patch("crier.db.create_event", new=AsyncMock(return_value="event-1")) as create_event,
            patch("crier.db.create_draft", new=AsyncMock()) as create_draft,
            patch("crier.drafter.generate_drafts", return_value=_DRAFTS) as generate_drafts,
        ):
            await cli._poll_and_draft(settings, watcher, bot)

        watcher.poll_repos.assert_awaited_once()
        create_event.assert_not_called()
        create_draft.assert_not_called()
        generate_drafts.assert_not_called()
        bot.send_draft_for_approval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unscored_push_is_skipped_and_later_events_still_run(
        self, settings, caplog
    ):
        """An unscored push is skipped with a warning; the rest of the poll cycle continues."""
        settings.anthropic_api_key = ""
        watcher, bot = _stubs()
        release = GitHubEvent(
            repo="org/repo", event_type="release", ref="v1.0.0", payload={"name": "v1.0.0"}
        )
        watcher.poll_repos = AsyncMock(return_value=[_push_event(), release])

        with (
            caplog.at_level("WARNING", logger="crier"),
            patch("crier.db.create_event", new=AsyncMock(return_value="event-1")) as create_event,
            patch("crier.db.create_draft", new=AsyncMock()),
            patch("crier.drafter.generate_drafts", return_value=_DRAFTS) as generate_drafts,
        ):
            await cli._poll_and_draft(settings, watcher, bot)

        create_event.assert_awaited_once()
        assert create_event.await_args.kwargs["event_type"] == "release"
        generate_drafts.assert_called_once()
        assert generate_drafts.call_args.args[0] is release
        bot.send_draft_for_approval.assert_awaited_once()
        assert "could not score" in caplog.text
        assert "Error in poll cycle" not in caplog.text

    @pytest.mark.asyncio
    async def test_poll_skips_event_below_threshold(self, settings):
        watcher, bot = _stubs()

        with (
            patch("crier.drafter.score_interestingness", return_value=2.0),
            patch("crier.db.create_event", new=AsyncMock(return_value="event-1")) as create_event,
            patch("crier.drafter.generate_drafts", return_value=_DRAFTS),
        ):
            await cli._poll_and_draft(settings, watcher, bot)

        create_event.assert_not_called()
        bot.send_draft_for_approval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_drafts_event_that_passes_threshold(self, settings):
        """Control case: a real score above the threshold reaches Telegram."""
        watcher, bot = _stubs()

        with (
            patch("crier.drafter.score_interestingness", return_value=8.0),
            patch("crier.db.create_event", new=AsyncMock(return_value="event-1")) as create_event,
            patch("crier.db.create_draft", new=AsyncMock()) as create_draft,
            patch("crier.drafter.generate_drafts", return_value=_DRAFTS),
        ):
            await cli._poll_and_draft(settings, watcher, bot)

        create_event.assert_awaited_once()
        assert create_draft.await_count == 3
        assert create_draft.await_args.kwargs["interestingness_score"] == 8.0
        bot.send_draft_for_approval.assert_awaited_once()


class _StopRun(Exception):
    """Raised by the stub Telegram app so _run_bot_and_watcher returns after setup."""


class _RecordingScheduler:
    """Stands in for AsyncIOScheduler and records the jobs it is given."""

    def __init__(self, *args, **kwargs):
        self.jobs: list[tuple] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, kwargs.get("seconds"), tuple(kwargs.get("args", ()))))

    def start(self):
        pass

    def shutdown(self, *args, **kwargs):
        pass


class TestScheduledPollJob:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("api_key", ["", "sk-test"], ids=["no-key", "api-error"])
    async def test_scheduled_poll_skips_event_when_scoring_fails(self, settings, api_key):
        """The poll job that `crier run` schedules fails closed when scoring fails."""
        settings.anthropic_api_key = api_key
        settings.poll_interval = 77  # tells the poll job apart from the 5 s manual job
        watcher, bot = _stubs()
        bot_app = MagicMock()
        bot_app.__aenter__ = AsyncMock(side_effect=_StopRun)
        bot.setup.return_value = bot_app
        schedulers: list[_RecordingScheduler] = []

        def make_scheduler(*args, **kwargs):
            schedulers.append(_RecordingScheduler())
            return schedulers[-1]

        with (
            patch("apscheduler.schedulers.asyncio.AsyncIOScheduler", side_effect=make_scheduler),
            patch("crier.bot.CrierBot", return_value=bot),
            patch("crier.watcher.GitHubWatcher", return_value=watcher),
            patch("crier.drafter.anthropic.Anthropic", side_effect=RuntimeError("API down")),
            patch("crier.db.create_event", new=AsyncMock(return_value="event-1")) as create_event,
            patch("crier.db.create_draft", new=AsyncMock()) as create_draft,
            patch("crier.drafter.generate_drafts", return_value=_DRAFTS) as generate_drafts,
        ):
            with pytest.raises(_StopRun):
                await cli._run_bot_and_watcher(settings)

            (scheduler,) = schedulers
            poll_jobs = [(func, args) for func, seconds, args in scheduler.jobs if seconds == 77]
            assert len(poll_jobs) == 1
            func, args = poll_jobs[0]
            await func(*args)

        watcher.poll_repos.assert_awaited_once()
        create_event.assert_not_called()
        create_draft.assert_not_called()
        generate_drafts.assert_not_called()
        bot.send_draft_for_approval.assert_not_awaited()
