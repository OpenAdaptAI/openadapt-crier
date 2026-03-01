"""Tests for crier configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

from crier.config import CrierSettings, get_settings


class TestCrierSettings:
    def test_default_values(self):
        settings = CrierSettings(_env_file=None)
        assert settings.telegram_bot_token == ""
        assert settings.telegram_owner_id == 0
        assert settings.poll_interval == 60
        assert settings.interest_threshold == 5.0
        assert settings.db_path == "./crier.db"
        assert settings.dry_run is False

    def test_repo_list_empty(self):
        settings = CrierSettings(_env_file=None, repos="")
        assert settings.repo_list == []

    def test_repo_list_single(self):
        settings = CrierSettings(_env_file=None, repos="OpenAdaptAI/OpenAdapt")
        assert settings.repo_list == ["OpenAdaptAI/OpenAdapt"]

    def test_repo_list_multiple(self):
        settings = CrierSettings(
            _env_file=None,
            repos="OpenAdaptAI/OpenAdapt, OpenAdaptAI/openadapt-evals",
        )
        assert settings.repo_list == [
            "OpenAdaptAI/OpenAdapt",
            "OpenAdaptAI/openadapt-evals",
        ]

    def test_repo_list_strips_whitespace(self):
        settings = CrierSettings(
            _env_file=None,
            repos=" org/repo1 , org/repo2 , ",
        )
        assert settings.repo_list == ["org/repo1", "org/repo2"]

    def test_has_telegram_false_when_no_token(self):
        settings = CrierSettings(_env_file=None, telegram_owner_id=123)
        assert not settings.has_telegram

    def test_has_telegram_false_when_no_owner(self):
        settings = CrierSettings(_env_file=None, telegram_bot_token="123:ABC")
        assert not settings.has_telegram

    def test_has_telegram_true(self):
        settings = CrierSettings(
            _env_file=None,
            telegram_bot_token="123:ABC",
            telegram_owner_id=456,
        )
        assert settings.has_telegram

    def test_has_discord(self):
        settings = CrierSettings(
            _env_file=None,
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        assert settings.has_discord

    def test_has_discord_false(self):
        settings = CrierSettings(_env_file=None)
        assert not settings.has_discord

    def test_has_twitter_true(self):
        settings = CrierSettings(
            _env_file=None,
            twitter_consumer_key="ck",
            twitter_consumer_secret="cs",
            twitter_access_token="at",
            twitter_access_token_secret="ats",
        )
        assert settings.has_twitter

    def test_has_twitter_false_missing_key(self):
        settings = CrierSettings(
            _env_file=None,
            twitter_consumer_key="ck",
            twitter_consumer_secret="",
            twitter_access_token="at",
            twitter_access_token_secret="ats",
        )
        assert not settings.has_twitter

    def test_has_linkedin(self):
        settings = CrierSettings(
            _env_file=None,
            linkedin_access_token="token123",
        )
        assert settings.has_linkedin

    def test_has_anthropic(self):
        settings = CrierSettings(
            _env_file=None,
            anthropic_api_key="sk-ant-123",
        )
        assert settings.has_anthropic

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fallback"}, clear=False)
    def test_fallback_anthropic_api_key(self):
        settings = CrierSettings(_env_file=None)
        assert settings.anthropic_api_key == "sk-ant-fallback"

    @patch.dict(os.environ, {"GH_TOKEN": "ghp_fallback"}, clear=False)
    def test_fallback_github_token(self):
        settings = CrierSettings(_env_file=None)
        assert settings.github_token == "ghp_fallback"

    @patch.dict(
        os.environ,
        {"DISCORD_WEBHOOK_URL": "https://discord.com/fallback"},
        clear=False,
    )
    def test_fallback_discord_webhook(self):
        settings = CrierSettings(_env_file=None)
        assert settings.discord_webhook_url == "https://discord.com/fallback"

    def test_get_settings_with_overrides(self):
        settings = get_settings(
            _env_file=None,
            telegram_bot_token="test-token",
            telegram_owner_id=999,
        )
        assert settings.telegram_bot_token == "test-token"
        assert settings.telegram_owner_id == 999
