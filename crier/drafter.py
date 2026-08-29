"""Interestingness scoring and draft generation.

Scoring is deterministic and fail-closed: allowlisted release or blog post
is 9, everything else is 0, and a missing Anthropic key never raises the
score. Draft text comes from templates unless --use-claude / CRIER_USE_CLAUDE
is set and an API key is present.
"""

from __future__ import annotations

import json
import logging
import re

from crier.allowlist import PIP_INSTALL, PROGRAM_NOUN, is_allowlisted
from crier.watcher import GitHubEvent

logger = logging.getLogger(__name__)

try:
    import anthropic
except ImportError:  # pragma: no cover - optional extra for --use-claude
    anthropic = None

# Constraints pasted into the Claude path. Templates follow them by construction.
WRITING_GUIDE_CONSTRAINTS = (
    "Banned: delve, leverage, robust, seamless, crucial, pivotal, landscape, "
    "testament, comprehensive, harness, foster, streamline, empower, "
    "transformative, game-changer, 'it's worth noting', 'in today's...', "
    "announcement register ('thrilled', 'excited to share', 'excited to announce'). "
    "No em dashes. No 'it's not X, it's Y' framing. No lists of exactly three "
    "parallel items. State the program noun exactly. Include `pip install openadapt`. "
    "Link the release notes or blog URL. Contractions are fine. Be direct."
)

_TWITTER_LIMIT = 280
_DISCORD_LIMIT = 2000
_LINKEDIN_LIMIT = 3000
_SUMMARY_LIMIT = 200


def score_interestingness(
    commits: list[dict],
    repo: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
) -> float:
    """Score how interesting a set of commits are for social media (0-10).

    The live poller does not use this. Commit events are not allowlisted, so
    they score 0 without an API call. When an API key is missing, this
    function returns 0 (fail closed) instead of 5.
    """
    if not api_key or anthropic is None:
        logger.warning("No Anthropic API key configured; scoring 0 (fail closed)")
        return 0.0

    commit_summary = "\n".join(
        f"- {c.get('sha', '')[:8]} by {c.get('author', 'unknown')}: {c.get('message', '')}"
        for c in commits
    )

    prompt = (
        f"Rate from 0 to 10 how interesting these commits to the {repo} repository "
        f"would be for a social media post about an open-source project.\n\n"
        f"Commits:\n{commit_summary}\n\n"
        f"Consider: Is there a user-facing feature, important bug fix, or notable "
        f"improvement? CI-only, docs-only, or minor refactors score low.\n\n"
        f"Respond with ONLY a single number between 0 and 10 (can be a decimal like 6.5)."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        match = re.search(r"-?\d+\.?\d*", text)
        if match:
            score = float(match.group())
            return min(max(score, 0.0), 10.0)
        logger.warning("Could not parse score from LLM response: %s", text)
        return 0.0
    except Exception:
        logger.exception("Error scoring interestingness")
        return 0.0


def generate_drafts(
    event: GitHubEvent,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
    project_context: str = "",
    use_claude: bool = False,
) -> dict[str, str]:
    """Generate platform-specific draft content for a GitHub event.

    Templates are the default. Claude runs only when use_claude is True and
    an API key is present. On any Claude failure, templates are used.
    """
    if not (use_claude and api_key and anthropic is not None):
        if use_claude and not api_key:
            logger.warning("CRIER_USE_CLAUDE set but no API key; using templates")
        return template_drafts(event)

    event_description = _format_event_for_prompt(event)

    system_prompt = (
        "You are writing social media copy for OpenAdapt. "
        f"The product noun is: {PROGRAM_NOUN} "
        "Crier is internal tooling, not the product. "
        f"{WRITING_GUIDE_CONSTRAINTS}"
    )

    if project_context:
        system_prompt += f"\n\nProject context: {project_context}"

    user_prompt = (
        f"Generate social media posts for this event:\n\n{event_description}\n\n"
        f"Return a JSON object with these keys:\n"
        f'- "twitter": A tweet (max 280 chars). Technical, concise.\n'
        f'- "discord": A Discord message (max 2000 chars). Can use markdown, '
        f"more detail than Twitter.\n"
        f'- "linkedin": A LinkedIn post (max 3000 chars). Professional tone, '
        f"slightly longer.\n"
        f'- "summary": A one-sentence summary (max 200 chars).\n\n'
        f"Every post must include the program noun, `{PIP_INSTALL}`, and the "
        f"release notes or blog URL.\n\n"
        f"Return ONLY valid JSON, no markdown code fences."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        return _parse_draft_response(text, event)
    except Exception:
        logger.exception("Error generating drafts")
        return template_drafts(event)


def template_drafts(event: GitHubEvent) -> dict[str, str]:
    """Deterministic drafts: program noun, pip install, and the event URL."""
    url = _event_url(event)
    headline = _event_headline(event)

    if is_allowlisted(event):
        twitter = _fit_twitter(headline, url)
        long_form = _long_form(headline, url)
        summary = _clip(f"{headline} {url}".strip(), _SUMMARY_LIMIT)
        return {
            "twitter": twitter,
            "discord": _clip(long_form, _DISCORD_LIMIT),
            "linkedin": _clip(long_form, _LINKEDIN_LIMIT),
            "summary": summary,
        }

    return _placeholder_drafts(event)


def _placeholder_drafts(event: GitHubEvent) -> dict[str, str]:
    """Minimal factual line for events that are not allowlisted."""
    if event.event_type == "push":
        commits = event.payload.get("commits", [])
        count = len(commits)
        msg = commits[0].get("message", "") if commits else ""
        summary = f"{count} new commit(s) on {event.repo}: {msg}"
    elif event.event_type == "release":
        tag = event.payload.get("tag", event.ref)
        summary = f"New release {tag} on {event.repo}"
    elif event.event_type == "pr_merged":
        title = event.payload.get("title", "")
        summary = f"PR merged on {event.repo}: {title}"
    elif event.event_type == "blog_post":
        title = event.payload.get("title") or event.ref
        summary = f"New post on {event.repo}: {title}"
    else:
        summary = f"New activity on {event.repo}"

    return {
        "twitter": summary[:_TWITTER_LIMIT],
        "discord": summary[:_DISCORD_LIMIT],
        "linkedin": summary[:_LINKEDIN_LIMIT],
        "summary": summary[:_SUMMARY_LIMIT],
    }


def _event_headline(event: GitHubEvent) -> str:
    if event.event_type == "release":
        tag = event.payload.get("tag") or event.ref
        name = (event.payload.get("name") or "").strip()
        repo_name = event.repo.split("/")[-1]
        if name and name != tag:
            return f"{repo_name} {name} ({tag}) is out."
        return f"{repo_name} {tag} is out."
    if event.event_type == "blog_post":
        title = event.payload.get("title") or event.ref
        return f"New post: {title}"
    return f"New activity on {event.repo}"


def _event_url(event: GitHubEvent) -> str:
    payload_url = (event.payload.get("html_url") or event.payload.get("url") or "").strip()
    if payload_url:
        return payload_url
    if event.event_type == "release":
        tag = event.payload.get("tag") or event.ref
        if tag:
            return f"https://github.com/{event.repo}/releases/tag/{tag}"
    if event.event_type == "blog_post":
        slug = event.payload.get("slug") or event.ref
        if slug:
            from crier.allowlist import blog_public_url

            return blog_public_url(str(slug))
    return ""


def _long_form(headline: str, url: str) -> str:
    parts = [headline, "", PROGRAM_NOUN, "", PIP_INSTALL]
    if url:
        parts.extend(["", url])
    return "\n".join(parts)


def _fit_twitter(headline: str, url: str) -> str:
    """Pack headline, program noun, pip install, and URL into 280 chars."""
    noun = PROGRAM_NOUN
    install = PIP_INSTALL
    url = url.strip()

    def compose(head: str, include_noun: bool = True) -> str:
        parts = [head]
        if include_noun:
            parts.extend(["", noun])
        parts.extend(["", install])
        if url:
            parts.extend(["", url])
        return "\n".join(parts)

    text = compose(headline)
    if len(text) <= _TWITTER_LIMIT:
        return text

    reserved = len(compose("x", include_noun=True)) - 1
    budget = _TWITTER_LIMIT - reserved
    if budget >= 12:
        return compose(_clip(headline, budget))

    reserved_no_noun = len(compose("x", include_noun=False)) - 1
    budget = _TWITTER_LIMIT - reserved_no_noun
    if budget >= 12:
        # Keep the noun by dropping it from the tweet only if the URL and
        # install line already fill the limit. Discord/LinkedIn still have it.
        return compose(_clip(headline, budget), include_noun=False)

    return _clip(compose(headline, include_noun=False), _TWITTER_LIMIT)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _format_event_for_prompt(event: GitHubEvent) -> str:
    """Format a GitHubEvent into a human-readable description for the LLM."""
    parts = [f"Repository: {event.repo}", f"Event type: {event.event_type}"]

    if event.event_type == "push":
        commits = event.payload.get("commits", [])
        parts.append(f"Number of new commits: {len(commits)}")
        for commit in commits[:10]:
            parts.append(
                f"  - {commit.get('sha', '')[:8]} by {commit.get('author', 'unknown')}: "
                f"{commit.get('message', '')}"
            )
    elif event.event_type == "release":
        parts.append(f"Tag: {event.payload.get('tag', '')}")
        parts.append(f"Release name: {event.payload.get('name', '')}")
        body = event.payload.get("body", "")
        if body:
            parts.append(f"Release notes:\n{body[:1000]}")
        url = _event_url(event)
        if url:
            parts.append(f"URL: {url}")
    elif event.event_type == "pr_merged":
        parts.append(f"PR #{event.payload.get('number', '')}: {event.payload.get('title', '')}")
        parts.append(f"Author: {event.payload.get('author', '')}")
        body = event.payload.get("body", "")
        if body:
            parts.append(f"Description:\n{body[:500]}")
    elif event.event_type == "blog_post":
        parts.append(f"Title: {event.payload.get('title', event.ref)}")
        parts.append(f"Slug: {event.payload.get('slug', event.ref)}")
        url = _event_url(event)
        if url:
            parts.append(f"URL: {url}")

    parts.append(f"Program noun: {PROGRAM_NOUN}")
    parts.append(f"Install: {PIP_INSTALL}")
    return "\n".join(parts)


def _parse_draft_response(text: str, event: GitHubEvent) -> dict[str, str]:
    """Parse the LLM response into a drafts dictionary.

    Handles both clean JSON and JSON wrapped in markdown code fences.
    """
    cleaned = text
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(cleaned)
        return {
            "twitter": str(result.get("twitter", ""))[:_TWITTER_LIMIT],
            "discord": str(result.get("discord", ""))[:_DISCORD_LIMIT],
            "linkedin": str(result.get("linkedin", ""))[:_LINKEDIN_LIMIT],
            "summary": str(result.get("summary", ""))[:_SUMMARY_LIMIT],
        }
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse drafts JSON, using templates")
        return template_drafts(event)



