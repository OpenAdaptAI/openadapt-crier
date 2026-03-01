"""Tests for commit filtering logic."""

from __future__ import annotations

from crier.filters import should_skip_commit


class TestShouldSkipCommit:
    # --- Merge commits ---

    def test_skip_merge_branch(self):
        assert should_skip_commit("Merge branch 'feature' into main", "alice")

    def test_skip_merge_pull_request(self):
        assert should_skip_commit(
            "Merge pull request #42 from org/feature", "alice"
        )

    def test_skip_merge_remote_tracking(self):
        assert should_skip_commit(
            "Merge remote-tracking branch 'origin/main'", "alice"
        )

    def test_skip_merge_case_insensitive(self):
        assert should_skip_commit("merge branch 'dev' into main", "alice")

    # --- Bot authors ---

    def test_skip_dependabot(self):
        assert should_skip_commit("Bump requests from 2.28 to 2.31", "dependabot")

    def test_skip_dependabot_bot(self):
        assert should_skip_commit("Update deps", "dependabot[bot]")

    def test_skip_renovate(self):
        assert should_skip_commit("chore(deps): update", "renovate[bot]")

    def test_skip_github_actions(self):
        assert should_skip_commit("Update version", "github-actions[bot]")

    # --- CI skip markers ---

    def test_skip_ci_skip(self):
        assert should_skip_commit("docs: update readme [ci skip]", "alice")

    def test_skip_skip_ci(self):
        assert should_skip_commit("fix typo [skip ci]", "alice")

    def test_skip_ci_skip_case_insensitive(self):
        assert should_skip_commit("update [CI SKIP]", "alice")

    # --- Version bumps ---

    def test_skip_chore_release(self):
        assert should_skip_commit("chore(release): v1.2.3", "alice")

    def test_skip_bump_version(self):
        assert should_skip_commit("bump version to 2.0.0", "alice")

    def test_skip_chore_release_prefix(self):
        assert should_skip_commit("chore: release v1.0.0", "alice")

    def test_skip_chore_bump(self):
        assert should_skip_commit("chore: bump version", "alice")

    def test_skip_bare_version_number(self):
        assert should_skip_commit("1.2.3", "alice")

    # --- Should NOT skip ---

    def test_keep_normal_commit(self):
        assert not should_skip_commit("feat: add login flow", "alice")

    def test_keep_bug_fix(self):
        assert not should_skip_commit("fix: resolve OAuth callback error", "bob")

    def test_keep_refactor(self):
        assert not should_skip_commit("refactor: extract auth module", "carol")

    def test_keep_docs(self):
        assert not should_skip_commit("docs: update API reference", "dave")

    def test_keep_message_mentioning_merge(self):
        """Messages that mention 'merge' but aren't merge commits."""
        assert not should_skip_commit(
            "fix: resolve merge conflict in config", "alice"
        )

    def test_keep_message_with_version_in_body(self):
        """Version numbers in the body, not matching the bump patterns."""
        assert not should_skip_commit("feat: support API v2.0", "alice")

    def test_keep_normal_author(self):
        assert not should_skip_commit("update deps manually", "regular-user")

    # --- Edge cases ---

    def test_empty_message(self):
        assert not should_skip_commit("", "alice")

    def test_empty_author(self):
        assert not should_skip_commit("feat: something", "")

    def test_whitespace_handling(self):
        assert should_skip_commit("  Merge branch 'main'  ", "alice")

    def test_author_case_insensitive(self):
        assert should_skip_commit("update", "Dependabot")
        assert should_skip_commit("update", "DEPENDABOT[BOT]")
