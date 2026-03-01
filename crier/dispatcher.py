"""Post approved drafts to social media platforms via herald."""

from __future__ import annotations

import logging

from herald.config import HeraldSettings
from herald.publisher import get_publishers, publish_content

from crier.config import CrierSettings
from crier.db import get_draft_by_id, get_drafts_by_event, update_draft_status

logger = logging.getLogger(__name__)


def _build_herald_settings(settings: CrierSettings) -> HeraldSettings:
    """Create a HeraldSettings instance from CrierSettings.

    Maps crier's config values to herald's expected fields so that
    herald's publisher infrastructure can be reused directly.
    """
    return HeraldSettings(
        _env_file=None,
        anthropic_api_key=settings.anthropic_api_key,
        discord_webhook_url=settings.discord_webhook_url,
        twitter_consumer_key=settings.twitter_consumer_key,
        twitter_consumer_secret=settings.twitter_consumer_secret,
        twitter_access_token=settings.twitter_access_token,
        twitter_access_token_secret=settings.twitter_access_token_secret,
        linkedin_access_token=settings.linkedin_access_token,
        github_token=settings.github_token,
        repos=settings.repos,
        dry_run=settings.dry_run,
    )


async def post_approved_draft(
    settings: CrierSettings,
    event_id: str,
    platforms: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Post all drafts for an event to the selected platforms.

    Retrieves drafts from the database, builds herald publishers, and posts
    content to each platform. Updates draft status and post URLs in the database.

    Args:
        settings: Crier configuration.
        event_id: The event whose drafts to post.
        platforms: Set of platform names to post to. If None, posts to all platforms
                   that have drafts.

    Returns:
        A dict mapping platform names to result dicts with keys: success, message, url.
    """
    herald_settings = _build_herald_settings(settings)
    publishers = get_publishers(herald_settings)

    drafts = await get_drafts_by_event(settings.db_path, event_id)
    if not drafts:
        logger.warning("No drafts found for event %s", event_id)
        return {}

    # Build content dict from drafts
    content: dict[str, str] = {}
    draft_id_by_platform: dict[str, str] = {}
    for draft in drafts:
        platform = draft["platform"]
        if platforms and platform not in platforms:
            continue
        content[platform] = draft["content"]
        draft_id_by_platform[platform] = draft["id"]

    if not content:
        logger.warning("No content to post for event %s (platforms filtered)", event_id)
        return {}

    # Filter publishers to only the selected platforms
    if platforms:
        publishers = [p for p in publishers if p.platform_name in platforms]

    results: dict[str, dict[str, str]] = {}

    if settings.dry_run:
        for platform, text in content.items():
            draft_id = draft_id_by_platform[platform]
            await update_draft_status(
                settings.db_path,
                draft_id,
                status="posted",
                post_url="[DRY RUN]",
            )
            results[platform] = {
                "success": "true",
                "message": f"[DRY RUN] Would post: {text[:100]}...",
                "url": "",
            }
        return results

    report = publish_content(
        content,
        publishers,
        dry_run=False,
    )

    for result in report.results:
        platform = result.platform
        draft_id = draft_id_by_platform.get(platform)
        if not draft_id:
            continue

        if result.success:
            await update_draft_status(
                settings.db_path,
                draft_id,
                status="posted",
                post_url=result.url,
            )
            results[platform] = {
                "success": "true",
                "message": result.message,
                "url": result.url,
            }
        else:
            await update_draft_status(
                settings.db_path,
                draft_id,
                status="pending",
                error=result.message,
            )
            results[platform] = {
                "success": "false",
                "message": result.message,
                "url": "",
            }

    return results


async def post_single_draft(
    settings: CrierSettings,
    draft_id: str,
) -> dict[str, str]:
    """Post a single draft by its ID.

    Returns a dict with keys: success, message, url.
    """
    draft = await get_draft_by_id(settings.db_path, draft_id)
    if not draft:
        return {"success": "false", "message": f"Draft {draft_id} not found", "url": ""}

    platform = draft["platform"]
    herald_settings = _build_herald_settings(settings)
    publishers = get_publishers(herald_settings)
    publishers = [p for p in publishers if p.platform_name == platform]

    if not publishers:
        error_msg = f"No publisher configured for platform: {platform}"
        await update_draft_status(
            settings.db_path, draft_id, status="pending", error=error_msg
        )
        return {"success": "false", "message": error_msg, "url": ""}

    content = {platform: draft["content"]}

    if settings.dry_run:
        await update_draft_status(
            settings.db_path, draft_id, status="posted", post_url="[DRY RUN]"
        )
        return {
            "success": "true",
            "message": f"[DRY RUN] Would post to {platform}",
            "url": "",
        }

    report = publish_content(content, publishers, dry_run=False)

    if report.results:
        result = report.results[0]
        if result.success:
            await update_draft_status(
                settings.db_path,
                draft_id,
                status="posted",
                post_url=result.url,
            )
            return {
                "success": "true",
                "message": result.message,
                "url": result.url,
            }
        else:
            await update_draft_status(
                settings.db_path, draft_id, status="pending", error=result.message
            )
            return {"success": "false", "message": result.message, "url": ""}

    return {"success": "false", "message": "No results from publisher", "url": ""}
