"""LLM-powered interestingness scoring and draft generation."""

from __future__ import annotations

import json
import logging
import re

import anthropic

from crier.watcher import GitHubEvent

logger = logging.getLogger(__name__)


def score_interestingness(
    commits: list[dict],
    repo: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
) -> float:
    """Score how interesting a set of commits are for social media (0-10).

    Uses a quick Claude API call to evaluate whether the commits
    are worth announcing publicly.
    """
    if not api_key:
        logger.warning("No Anthropic API key configured; defaulting score to 5.0")
        return 5.0

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
        # Extract the first number from the response
        match = re.search(r"-?\d+\.?\d*", text)
        if match:
            score = float(match.group())
            return min(max(score, 0.0), 10.0)
        logger.warning("Could not parse score from LLM response: %s", text)
        return 5.0
    except Exception:
        logger.exception("Error scoring interestingness")
        return 5.0


def generate_drafts(
    event: GitHubEvent,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
    project_context: str = "",
) -> dict[str, str]:
    """Generate platform-specific draft content for a GitHub event.

    Uses herald's compose function when available, falling back to a direct
    Claude API call for content generation.

    Returns a dict with keys: twitter, discord, linkedin, summary.
    """
    # Build the event description for the LLM
    event_description = _format_event_for_prompt(event)

    system_prompt = (
        "You are a developer advocate writing social media content for an open-source project. "
        "Write concise, authentic posts that highlight what matters to developers. "
        "Avoid AI-sounding language: no 'excited to announce', no 'game-changer', no 'delve'. "
        "Be direct and technical. Use the project's actual impact."
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
        f"Return ONLY valid JSON, no markdown code fences."
    )

    if not api_key:
        logger.warning("No Anthropic API key; generating placeholder drafts")
        return _placeholder_drafts(event)

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
        return _placeholder_drafts(event)


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
    elif event.event_type == "pr_merged":
        parts.append(f"PR #{event.payload.get('number', '')}: {event.payload.get('title', '')}")
        parts.append(f"Author: {event.payload.get('author', '')}")
        body = event.payload.get("body", "")
        if body:
            parts.append(f"Description:\n{body[:500]}")

    return "\n".join(parts)


def _parse_draft_response(text: str, event: GitHubEvent) -> dict[str, str]:
    """Parse the LLM response into a drafts dictionary.

    Handles both clean JSON and JSON wrapped in markdown code fences.
    """
    # Strip markdown code fences if present
    cleaned = text
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(cleaned)
        # Ensure all expected keys exist
        return {
            "twitter": str(result.get("twitter", ""))[:280],
            "discord": str(result.get("discord", ""))[:2000],
            "linkedin": str(result.get("linkedin", ""))[:3000],
            "summary": str(result.get("summary", ""))[:200],
        }
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse drafts JSON, using raw text")
        return _placeholder_drafts(event)


def _placeholder_drafts(event: GitHubEvent) -> dict[str, str]:
    """Generate minimal placeholder drafts when LLM is unavailable."""
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
    else:
        summary = f"New activity on {event.repo}"

    return {
        "twitter": summary[:280],
        "discord": summary[:2000],
        "linkedin": summary[:3000],
        "summary": summary[:200],
    }
