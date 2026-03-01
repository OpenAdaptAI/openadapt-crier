"""Tests for the drafter module with mock Claude API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from crier.drafter import (
    _format_event_for_prompt,
    _parse_draft_response,
    _placeholder_drafts,
    generate_drafts,
    score_interestingness,
)
from crier.watcher import GitHubEvent


class TestScoreInterestingness:
    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_returns_float(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="7.5")]
        mock_client.messages.create.return_value = mock_response

        commits = [{"sha": "abc123", "author": "alice", "message": "feat: add login"}]
        score = score_interestingness(commits, "org/repo", api_key="sk-test")

        assert score == 7.5
        mock_client.messages.create.assert_called_once()

    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_clamps_high(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="15")]
        mock_client.messages.create.return_value = mock_response

        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", "sk-test"
        )
        assert score == 10.0

    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_clamps_low(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="-3")]
        mock_client.messages.create.return_value = mock_response

        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", "sk-test"
        )
        assert score == 0.0

    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_handles_text_with_number(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="I would rate this a 6 out of 10")]
        mock_client.messages.create.return_value = mock_response

        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", "sk-test"
        )
        assert score == 6.0

    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_handles_unparseable_response(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not a number at all")]
        mock_client.messages.create.return_value = mock_response

        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", "sk-test"
        )
        assert score == 5.0  # default fallback

    def test_score_no_api_key(self):
        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", ""
        )
        assert score == 5.0

    @patch("crier.drafter.anthropic.Anthropic")
    def test_score_handles_exception(self, mock_anthropic_cls):
        mock_anthropic_cls.side_effect = Exception("API error")

        score = score_interestingness(
            [{"sha": "a", "author": "b", "message": "c"}], "org/repo", "sk-test"
        )
        assert score == 5.0


class TestGenerateDrafts:
    @patch("crier.drafter.anthropic.Anthropic")
    def test_generate_drafts_returns_dict(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        expected = {
            "twitter": "New feature shipped!",
            "discord": "**New Feature**\nShipped login flow.",
            "linkedin": "Shipped a new login feature.",
            "summary": "New login feature added.",
        }
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(expected))]
        mock_client.messages.create.return_value = mock_response

        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc123",
            payload={
                "commits": [
                    {"sha": "abc123", "author": "alice", "message": "feat: add login"}
                ]
            },
        )

        drafts = generate_drafts(event, api_key="sk-test")
        assert drafts["twitter"] == expected["twitter"]
        assert drafts["discord"] == expected["discord"]

    @patch("crier.drafter.anthropic.Anthropic")
    def test_generate_drafts_handles_markdown_json(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        expected = {
            "twitter": "Tweet",
            "discord": "Discord msg",
            "linkedin": "LinkedIn post",
            "summary": "Summary",
        }
        wrapped = f"```json\n{json.dumps(expected)}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=wrapped)]
        mock_client.messages.create.return_value = mock_response

        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc",
            payload={"commits": []},
        )

        drafts = generate_drafts(event, api_key="sk-test")
        assert drafts["twitter"] == "Tweet"

    def test_generate_drafts_no_api_key(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc",
            payload={"commits": [{"sha": "abc", "author": "a", "message": "feat: X"}]},
        )

        drafts = generate_drafts(event, api_key="")
        assert "twitter" in drafts
        assert "discord" in drafts
        assert "org/repo" in drafts["twitter"]

    @patch("crier.drafter.anthropic.Anthropic")
    def test_generate_drafts_handles_api_error(self, mock_anthropic_cls):
        mock_anthropic_cls.side_effect = Exception("API down")

        event = GitHubEvent(
            repo="org/repo",
            event_type="release",
            ref="v1.0.0",
            payload={"tag": "v1.0.0", "name": "v1.0.0"},
        )

        drafts = generate_drafts(event, api_key="sk-test")
        assert "twitter" in drafts
        assert "v1.0.0" in drafts["twitter"]


class TestFormatEventForPrompt:
    def test_format_push_event(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc123",
            payload={
                "commits": [
                    {"sha": "abc123", "author": "alice", "message": "feat: add login"},
                    {"sha": "def456", "author": "bob", "message": "fix: typo"},
                ]
            },
        )
        text = _format_event_for_prompt(event)
        assert "org/repo" in text
        assert "push" in text
        assert "feat: add login" in text
        assert "fix: typo" in text

    def test_format_release_event(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="release",
            ref="v2.0.0",
            payload={
                "tag": "v2.0.0",
                "name": "Version 2.0",
                "body": "Major update with breaking changes",
            },
        )
        text = _format_event_for_prompt(event)
        assert "release" in text
        assert "v2.0.0" in text
        assert "Major update" in text

    def test_format_pr_event(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="pr_merged",
            ref="42",
            payload={
                "number": 42,
                "title": "feat: add auth flow",
                "author": "carol",
                "body": "Implemented OAuth",
            },
        )
        text = _format_event_for_prompt(event)
        assert "pr_merged" in text
        assert "#42" in text
        assert "feat: add auth flow" in text


class TestParseDraftResponse:
    def test_parse_valid_json(self):
        event = GitHubEvent(
            repo="org/repo", event_type="push", ref="abc", payload={}
        )
        data = {
            "twitter": "Tweet",
            "discord": "Discord msg",
            "linkedin": "LinkedIn post",
            "summary": "Summary",
        }
        result = _parse_draft_response(json.dumps(data), event)
        assert result["twitter"] == "Tweet"
        assert result["discord"] == "Discord msg"

    def test_parse_json_with_code_fences(self):
        event = GitHubEvent(
            repo="org/repo", event_type="push", ref="abc", payload={}
        )
        data = {"twitter": "T", "discord": "D", "linkedin": "L", "summary": "S"}
        text = f"```json\n{json.dumps(data)}\n```"
        result = _parse_draft_response(text, event)
        assert result["twitter"] == "T"

    def test_parse_invalid_json_falls_back(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc",
            payload={"commits": [{"sha": "abc", "author": "a", "message": "test"}]},
        )
        result = _parse_draft_response("not valid json", event)
        assert "twitter" in result
        assert "discord" in result

    def test_parse_truncates_long_content(self):
        event = GitHubEvent(
            repo="org/repo", event_type="push", ref="abc", payload={}
        )
        data = {
            "twitter": "x" * 500,
            "discord": "y" * 3000,
            "linkedin": "z" * 5000,
            "summary": "w" * 300,
        }
        result = _parse_draft_response(json.dumps(data), event)
        assert len(result["twitter"]) <= 280
        assert len(result["discord"]) <= 2000
        assert len(result["linkedin"]) <= 3000
        assert len(result["summary"]) <= 200


class TestPlaceholderDrafts:
    def test_placeholder_push(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="push",
            ref="abc",
            payload={"commits": [{"sha": "abc", "message": "feat: X"}]},
        )
        drafts = _placeholder_drafts(event)
        assert "org/repo" in drafts["twitter"]
        assert "feat: X" in drafts["twitter"]

    def test_placeholder_release(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="release",
            ref="v1.0",
            payload={"tag": "v1.0"},
        )
        drafts = _placeholder_drafts(event)
        assert "v1.0" in drafts["twitter"]
        assert "release" in drafts["twitter"].lower()

    def test_placeholder_pr(self):
        event = GitHubEvent(
            repo="org/repo",
            event_type="pr_merged",
            ref="42",
            payload={"title": "feat: auth"},
        )
        drafts = _placeholder_drafts(event)
        assert "feat: auth" in drafts["twitter"]
