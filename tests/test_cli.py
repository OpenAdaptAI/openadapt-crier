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


def _github_transport(commit_message: str):
    """Serve one commit, no releases, and no PRs for any repo."""
    import httpx

    commits = [
        {
            "sha": "a" * 40,
            "author": {"login": "alice"},
            "commit": {
                "message": commit_message,
                "author": {"name": "Alice", "date": "2026-10-01T00:00:00Z"},
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return httpx.Response(200, json=commits)
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


class TestOneShotDraft:
    @pytest.mark.asyncio
    async def test_draft_leaves_the_bot_database_alone(self, settings, monkeypatch):
        """`crier draft` must not mark activity as seen in the bot's database.

        If it did, a second run would find no new commits, and a running bot
        that shares the database would never draft them.
        """
        import os

        import httpx

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        settings.anthropic_api_key = ""  # placeholder drafts, no API call
        transport = _github_transport("feat: add a thing")
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            "crier.watcher.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        )

        first = await cli._generate_one_shot_draft(settings, "org/repo", "digest", 7)
        second = await cli._generate_one_shot_draft(settings, "org/repo", "digest", 7)

        expected = "1 new commit(s) on org/repo: feat: add a thing"
        assert first["summary"] == expected
        assert second["summary"] == expected
        assert not os.path.exists(settings.db_path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [200, 404], ids=["no-activity", "unknown-repo"])
    async def test_draft_returns_nothing_when_github_has_no_activity(
        self, settings, monkeypatch, status_code
    ):
        """With nothing to draft, `crier draft` must not invent a post about zero commits."""
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            body = [] if status_code == 200 else {"message": "Not Found"}
            return httpx.Response(status_code, json=body)

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            "crier.watcher.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        )

        with patch("crier.drafter.generate_drafts") as generate_drafts:
            result = await cli._generate_one_shot_draft(settings, "org/missing", "digest", 7)

        assert result == {}
        generate_drafts.assert_not_called()

    def test_draft_command_exits_nonzero_with_nothing_to_draft(self, settings, monkeypatch):
        from typer.testing import CliRunner

        monkeypatch.setattr(cli, "_get_settings", lambda **kwargs: settings)
        monkeypatch.setattr(cli, "_generate_one_shot_draft", AsyncMock(return_value={}))

        result = CliRunner().invoke(cli.app, ["draft", "org/missing"])

        assert result.exit_code == 1
        assert "Found no commits" in result.output


class TestReadCommandsWithoutDatabase:
    """`crier drafts`, `history`, and `status` before `crier run` has created the database."""

    @pytest.mark.parametrize("command", ["drafts", "history", "status"])
    @pytest.mark.parametrize("empty_file", [False, True], ids=["missing", "empty-file"])
    def test_reports_missing_database_without_creating_it(
        self, settings, monkeypatch, command, empty_file
    ):
        import os

        from typer.testing import CliRunner

        if empty_file:
            open(settings.db_path, "w").close()
        monkeypatch.setattr(cli, "_get_settings", lambda **kwargs: settings)

        result = CliRunner().invoke(cli.app, [command])

        assert result.exit_code == 0, result.output
        assert "crier run" in result.output
        assert os.path.exists(settings.db_path) == empty_file
        if empty_file:
            assert os.path.getsize(settings.db_path) == 0

    @pytest.mark.parametrize("command", ["drafts", "history"])
    def test_reads_an_initialized_database(self, settings, monkeypatch, command):
        import asyncio

        from typer.testing import CliRunner

        from crier.db import init_db

        asyncio.run(init_db(settings.db_path))
        monkeypatch.setattr(cli, "_get_settings", lambda **kwargs: settings)

        result = CliRunner().invoke(cli.app, [command])

        assert result.exit_code == 0, result.output
        assert "crier run" not in result.output
        assert "No " in result.output
