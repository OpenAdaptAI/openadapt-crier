"""Tests for the announcement allowlist and fail-closed scorer."""

from __future__ import annotations

from unittest.mock import patch

from crier.allowlist import (
    ALLOWLIST_SCORE,
    REJECT_SCORE,
    is_allowlisted,
    score_event,
)
from crier.watcher import GitHubEvent


def _release(repo: str, tag: str = "v1.0.0", **payload) -> GitHubEvent:
    data = {
        "tag": tag,
        "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
        **payload,
    }
    return GitHubEvent(repo=repo, event_type="release", ref=tag, payload=data)


def _blog(slug: str = "the-500th-run", title: str = "The 500th run") -> GitHubEvent:
    return GitHubEvent(
        repo="OpenAdaptAI/openadapt-blog",
        event_type="blog_post",
        ref=slug,
        payload={
            "slug": slug,
            "title": title,
            "url": f"https://blog.openadapt.ai/posts/{slug}/",
        },
    )


class TestAllowlist:
    def test_openadapt_tagged_release(self):
        event = _release("OpenAdaptAI/OpenAdapt", "v1.7.0")
        assert is_allowlisted(event)
        assert score_event(event) == ALLOWLIST_SCORE

    def test_flow_tagged_release(self):
        event = _release("OpenAdaptAI/openadapt-flow", "v1.20.0")
        assert is_allowlisted(event)
        assert score_event(event) == ALLOWLIST_SCORE

    def test_blog_post(self):
        event = _blog()
        assert is_allowlisted(event)
        assert score_event(event) == ALLOWLIST_SCORE

    def test_release_repo_case_insensitive(self):
        event = _release("openadaptai/openadapt-flow", "v1.0.0")
        assert is_allowlisted(event)

    def test_push_scores_zero(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="push",
            ref="abc123",
            payload={"commits": [{"sha": "abc123", "message": "feat: add login"}]},
        )
        assert not is_allowlisted(event)
        assert score_event(event) == REJECT_SCORE

    def test_chore_pr_scores_zero(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="pr_merged",
            ref="12",
            payload={"number": 12, "title": "chore: bump deps", "author": "alice"},
        )
        assert score_event(event) == REJECT_SCORE

    def test_docs_only_commit_scores_zero(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/OpenAdapt",
            event_type="push",
            ref="def456",
            payload={"commits": [{"sha": "def456", "message": "docs: fix typo"}]},
        )
        assert score_event(event) == REJECT_SCORE

    def test_research_repo_release_scores_zero(self):
        event = _release("OpenAdaptAI/openadapt-evals", "v0.4.0")
        assert not is_allowlisted(event)
        assert score_event(event) == REJECT_SCORE

    def test_prerelease_scores_zero(self):
        event = _release("OpenAdaptAI/openadapt-flow", "v2.0.0-rc.1", prerelease=True)
        assert not is_allowlisted(event)
        assert score_event(event) == REJECT_SCORE

    def test_draft_release_scores_zero(self):
        event = _release("OpenAdaptAI/OpenAdapt", "v9.0.0", draft=True)
        assert score_event(event) == REJECT_SCORE

    def test_untagged_release_scores_zero(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="release",
            ref="",
            payload={"tag": "", "name": "untagged"},
        )
        assert score_event(event) == REJECT_SCORE

    def test_blog_on_wrong_repo_scores_zero(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="blog_post",
            ref="not-a-post",
            payload={"slug": "not-a-post", "title": "Nope"},
        )
        assert score_event(event) == REJECT_SCORE


class TestFailClosedNoApiKey:
    def test_allowlisted_release_is_nine_without_key(self):
        event = _release("OpenAdaptAI/openadapt-flow")
        assert score_event(event) == 9.0

    def test_noise_is_zero_without_key(self):
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-flow",
            event_type="push",
            ref="abc",
            payload={"commits": [{"message": "feat: noise"}]},
        )
        assert score_event(event) == 0.0

    def test_scorer_does_not_import_or_call_anthropic(self):
        event = _release("OpenAdaptAI/OpenAdapt")
        with patch("crier.drafter.anthropic.Anthropic") as mock_cls:
            assert score_event(event) == 9.0
            mock_cls.assert_not_called()
        event = GitHubEvent(
            repo="OpenAdaptAI/openadapt-evals",
            event_type="push",
            ref="abc",
            payload={"commits": [{"message": "docs: only"}]},
        )
        with patch("crier.drafter.anthropic.Anthropic") as mock_cls:
            assert score_event(event) == 0.0
            mock_cls.assert_not_called()
