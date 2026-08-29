"""Announcement allowlist: only tagged product releases and blog posts score.

Commits, PRs, docs-only changes, and research-repo noise are never
allowlisted. Scoring those as zero does not need an API call.
"""

from __future__ import annotations

from collections.abc import Iterable

# Tagged GitHub Releases of the product launcher and the compiler.
DEFAULT_RELEASE_REPOS: tuple[str, ...] = (
    "OpenAdaptAI/OpenAdapt",
    "OpenAdaptAI/openadapt-flow",
)

# New posts under content/posts/ on the public blog.
DEFAULT_BLOG_REPOS: tuple[str, ...] = ("OpenAdaptAI/openadapt-blog",)

BLOG_POSTS_PATH = "content/posts"
BLOG_PUBLIC_BASE = "https://blog.openadapt.ai/posts"

ALLOWLIST_SCORE = 9.0
REJECT_SCORE = 0.0

# OpenAdapt compiles a demonstrated GUI task into a program that reports
# VERIFIED only if an independent check agrees. Crier is not the product.
PROGRAM_NOUN = (
    "OpenAdapt compiles a demonstrated GUI task into a program that reports "
    "VERIFIED only if an independent check agrees."
)
PIP_INSTALL = "pip install openadapt"


def normalize_repo(repo: str) -> str:
    """GitHub owner/repo, trimmed and case-folded."""
    return repo.strip().lower()


def _repo_set(repos: Iterable[str] | None, default: tuple[str, ...]) -> set[str]:
    if repos is None:
        return {normalize_repo(r) for r in default}
    return {normalize_repo(r) for r in repos if r and r.strip()}


def is_release_repo(repo: str, release_repos: Iterable[str] | None = None) -> bool:
    """True if repo is on the tagged-release allowlist."""
    return normalize_repo(repo) in _repo_set(release_repos, DEFAULT_RELEASE_REPOS)


def is_blog_repo(repo: str, blog_repos: Iterable[str] | None = None) -> bool:
    """True if repo is the public blog."""
    return normalize_repo(repo) in _repo_set(blog_repos, DEFAULT_BLOG_REPOS)


def is_allowlisted_event(
    event_type: str,
    repo: str,
    payload: dict | None = None,
    ref: str = "",
    release_repos: Iterable[str] | None = None,
    blog_repos: Iterable[str] | None = None,
) -> bool:
    """True only for a tagged product release or a new blog post.

    Draft and prerelease GitHub Releases are rejected. Untagged events
    (empty tag and empty ref) are rejected. Everything else, including
    commits and merged PRs, is rejected.
    """
    payload = payload or {}
    if event_type == "release" and is_release_repo(repo, release_repos):
        tag = str(payload.get("tag") or ref or "").strip()
        if not tag:
            return False
        if payload.get("draft") or payload.get("prerelease"):
            return False
        return True
    if event_type == "blog_post" and is_blog_repo(repo, blog_repos):
        return True
    return False


def is_allowlisted(
    event,
    release_repos: Iterable[str] | None = None,
    blog_repos: Iterable[str] | None = None,
) -> bool:
    """Duck-typed wrapper around is_allowlisted_event."""
    return is_allowlisted_event(
        event_type=event.event_type,
        repo=event.repo,
        payload=event.payload,
        ref=event.ref,
        release_repos=release_repos,
        blog_repos=blog_repos,
    )


def score_event(
    event,
    release_repos: Iterable[str] | None = None,
    blog_repos: Iterable[str] | None = None,
) -> float:
    """Deterministic scorer: allowlisted release or blog post is 9, else 0.

    Never calls an LLM. Missing Anthropic credentials cannot raise the score.
    """
    if is_allowlisted(event, release_repos=release_repos, blog_repos=blog_repos):
        return ALLOWLIST_SCORE
    return REJECT_SCORE


def blog_public_url(slug: str) -> str:
    """Canonical public URL for a Hugo post slug."""
    cleaned = slug.strip().removesuffix(".md").strip("/")
    return f"{BLOG_PUBLIC_BASE}/{cleaned}/"
