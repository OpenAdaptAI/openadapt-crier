"""Score GitHub events and persist drafts. Telegram and Claude are optional."""

from __future__ import annotations

import json
import logging

from crier.allowlist import score_event
from crier.config import CrierSettings
from crier.db import create_draft, create_event
from crier.drafter import generate_drafts
from crier.watcher import GitHubEvent

logger = logging.getLogger(__name__)


async def ingest_event(
    event: GitHubEvent,
    settings: CrierSettings,
    bot=None,
) -> str | None:
    """Store drafts for an allowlisted event. Returns the event id, or None.

    Non-allowlisted events score 0 and are dropped with no LLM call.
    Telegram delivery is skipped when the bot is missing or unconfigured.
    """
    score = score_event(
        event,
        release_repos=settings.release_repo_list,
        blog_repos=settings.blog_repo_list,
    )
    if score < settings.interest_threshold:
        logger.info(
            "Skipping %s event on %s (score %.1f < threshold %.1f)",
            event.event_type,
            event.repo,
            score,
            settings.interest_threshold,
        )
        return None

    event_id = await create_event(
        settings.db_path,
        repo=event.repo,
        event_type=event.event_type,
        ref=event.ref,
        payload=json.dumps(event.payload),
    )

    drafts = generate_drafts(
        event,
        api_key=settings.anthropic_api_key,
        use_claude=settings.use_claude,
    )

    for platform, content in drafts.items():
        if platform == "summary":
            continue
        await create_draft(
            settings.db_path,
            event_id=event_id,
            platform=platform,
            content=content,
            interestingness_score=score,
        )

    if bot is not None and settings.has_telegram:
        event_info = {
            "repo": event.repo,
            "event_type": event.event_type,
            "ref": event.ref,
            **event.payload,
        }
        await bot.send_draft_for_approval(
            chat_id=settings.telegram_owner_id,
            event_id=event_id,
            drafts={k: v for k, v in drafts.items() if k != "summary"},
            event_info=event_info,
        )

    return event_id
