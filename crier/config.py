"""Configuration via environment variables and .env files."""

from __future__ import annotations

import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from crier.allowlist import DEFAULT_BLOG_REPOS, DEFAULT_RELEASE_REPOS


class CrierSettings(BaseSettings):
    """Crier configuration. All values can be set via CRIER_ prefixed env vars."""

    model_config = SettingsConfigDict(
        env_prefix="CRIER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram (optional: drafts still write to sqlite)
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0

    # GitHub. Empty repos falls back to the announcement allowlist.
    repos: str = ""
    github_token: str = ""
    poll_interval: int = 60
    interest_threshold: float = 5.0
    allowlist_release_repos: str = ",".join(DEFAULT_RELEASE_REPOS)
    allowlist_blog_repos: str = ",".join(DEFAULT_BLOG_REPOS)

    # LLM. Off by default; templates do not need a key.
    anthropic_api_key: str = ""
    use_claude: bool = False

    # Discord
    discord_webhook_url: str = ""

    # Twitter/X
    twitter_consumer_key: str = ""
    twitter_consumer_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""

    # LinkedIn
    linkedin_access_token: str = ""

    # Storage
    db_path: str = "./crier.db"

    # Behavior
    dry_run: bool = False

    @model_validator(mode="after")
    def _fallback_env_vars(self):
        """Fall back to non-prefixed env vars for common API keys."""
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.github_token:
            self.github_token = os.environ.get("GH_TOKEN", "")
        if not self.discord_webhook_url:
            self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not self.twitter_consumer_key:
            self.twitter_consumer_key = os.environ.get("TWITTER_CONSUMER_KEY", "")
        if not self.twitter_consumer_secret:
            self.twitter_consumer_secret = os.environ.get("TWITTER_CONSUMER_SECRET", "")
        if not self.twitter_access_token:
            self.twitter_access_token = os.environ.get("TWITTER_ACCESS_TOKEN", "")
        if not self.twitter_access_token_secret:
            self.twitter_access_token_secret = os.environ.get(
                "TWITTER_ACCESS_TOKEN_SECRET", ""
            )
        if not self.linkedin_access_token:
            self.linkedin_access_token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
        return self

    @staticmethod
    def _split_repos(value: str) -> list[str]:
        return [r.strip() for r in value.split(",") if r.strip()]

    @property
    def release_repo_list(self) -> list[str]:
        """Repos whose tagged GitHub Releases are allowlisted."""
        return self._split_repos(self.allowlist_release_repos)

    @property
    def blog_repo_list(self) -> list[str]:
        """Repos whose new posts are allowlisted."""
        return self._split_repos(self.allowlist_blog_repos)

    @property
    def repo_list(self) -> list[str]:
        """Repos to poll. Empty CRIER_REPOS uses the announcement allowlist."""
        if not self.repos:
            seen: set[str] = set()
            ordered: list[str] = []
            for repo in (*self.release_repo_list, *self.blog_repo_list):
                key = repo.lower()
                if key not in seen:
                    seen.add(key)
                    ordered.append(repo)
            return ordered
        return self._split_repos(self.repos)

    @property
    def has_telegram(self) -> bool:
        """Check if Telegram bot is configured."""
        return bool(self.telegram_bot_token) and self.telegram_owner_id > 0

    @property
    def has_discord(self) -> bool:
        """Check if Discord publishing is configured."""
        return bool(self.discord_webhook_url)

    @property
    def has_twitter(self) -> bool:
        """Check if Twitter publishing is configured."""
        return all([
            self.twitter_consumer_key,
            self.twitter_consumer_secret,
            self.twitter_access_token,
            self.twitter_access_token_secret,
        ])

    @property
    def has_linkedin(self) -> bool:
        """Check if LinkedIn publishing is configured."""
        return bool(self.linkedin_access_token)

    @property
    def has_anthropic(self) -> bool:
        """Check if Anthropic API is configured for LLM calls."""
        return bool(self.anthropic_api_key)


def get_settings(**overrides) -> CrierSettings:
    """Load settings with optional overrides."""
    return CrierSettings(**overrides)
