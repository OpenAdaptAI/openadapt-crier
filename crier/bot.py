"""Telegram approval bot for reviewing and posting social media drafts."""

from __future__ import annotations

import json
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters as tg_filters,
)

from crier.config import CrierSettings
from crier.db import (
    create_feedback,
    get_draft_by_id,
    get_drafts_by_event,
    get_pending_drafts,
    get_recent_drafts,
    update_draft_content,
    update_draft_status,
)
from crier.dispatcher import post_approved_draft

logger = logging.getLogger(__name__)

# Conversation states for the edit flow
WAITING_FOR_EDIT = 0

# Platform labels for toggle buttons
_PLATFORM_LABELS = {
    "twitter": "Twitter",
    "discord": "Discord",
    "linkedin": "LinkedIn",
}


class CrierBot:
    """Telegram bot that sends drafts for approval and handles user decisions."""

    def __init__(self, settings: CrierSettings) -> None:
        self._settings = settings
        self._app: Application | None = None
        # Track which platforms are enabled per event_id
        self._platform_toggles: dict[str, set[str]] = {}

    def setup(self) -> Application:
        """Build and configure the Telegram bot application with all handlers."""
        builder = Application.builder().token(self._settings.telegram_bot_token)
        self._app = builder.build()

        # Store settings in bot_data for access in handlers
        self._app.bot_data["settings"] = self._settings
        self._app.bot_data["bot_instance"] = self

        # Command handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("history", self._cmd_history))
        self._app.add_handler(CommandHandler("draft", self._cmd_draft))

        # Edit conversation handler
        edit_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self._callback_edit, pattern=r"^edit:"),
            ],
            states={
                WAITING_FOR_EDIT: [
                    MessageHandler(
                        tg_filters.TEXT & ~tg_filters.COMMAND,
                        self._receive_edit_text,
                    ),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", self._cancel_edit),
            ],
            per_message=False,
        )
        self._app.add_handler(edit_conv)

        # Callback query handlers (must be after conversation handler)
        self._app.add_handler(
            CallbackQueryHandler(self._callback_approve, pattern=r"^approve:")
        )
        self._app.add_handler(
            CallbackQueryHandler(self._callback_reject, pattern=r"^reject:")
        )
        self._app.add_handler(
            CallbackQueryHandler(self._callback_toggle_platform, pattern=r"^toggle:")
        )

        return self._app

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a Telegram user ID matches the configured owner."""
        return user_id == self._settings.telegram_owner_id

    async def send_draft_for_approval(
        self,
        chat_id: int,
        event_id: str,
        drafts: dict[str, str],
        event_info: dict[str, Any],
    ) -> None:
        """Send a formatted draft message with approval buttons to the owner.

        Args:
            chat_id: Telegram chat ID to send to.
            event_id: The event ID these drafts belong to.
            drafts: Dict mapping platform names to draft content.
            event_info: Dict with event metadata (repo, event_type, ref, commits, etc.).
        """
        if not self._app:
            logger.error("Bot not set up; call setup() first")
            return

        # Initialize platform toggles — all platforms enabled by default
        self._platform_toggles[event_id] = set(drafts.keys())

        # Build the message text
        text = self._format_draft_message(event_info, drafts)

        # Build the inline keyboard
        keyboard = self._build_keyboard(event_id, self._platform_toggles[event_id])

        sent_message = await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # Store telegram message ID for each draft
        db_path = self._settings.db_path
        drafts_in_db = await get_drafts_by_event(db_path, event_id)
        for draft in drafts_in_db:
            await update_draft_status(
                db_path,
                draft["id"],
                status="pending",
                telegram_message_id=sent_message.message_id,
                telegram_chat_id=chat_id,
            )

    def _format_draft_message(
        self, event_info: dict[str, Any], drafts: dict[str, str]
    ) -> str:
        """Format event info and drafts into an HTML message for Telegram."""
        repo = event_info.get("repo", "unknown")
        event_type = event_info.get("event_type", "push")
        ref = event_info.get("ref", "")

        # Header
        parts = [f"<b>New {event_type} on {repo}</b>"]

        if event_type == "push":
            commits = event_info.get("commits", [])
            for commit in commits[:5]:
                sha = commit.get("sha", "")[:8]
                msg = _escape_html(commit.get("message", ""))
                author = _escape_html(commit.get("author", ""))
                parts.append(f"  <code>{sha}</code> by @{author}: {msg}")
            if len(commits) > 5:
                parts.append(f"  ... and {len(commits) - 5} more commits")
        elif event_type == "release":
            tag = _escape_html(event_info.get("tag", ref))
            name = _escape_html(event_info.get("name", ""))
            parts.append(f"  Tag: <code>{tag}</code> {name}")
        elif event_type == "pr_merged":
            title = _escape_html(event_info.get("title", ""))
            author = _escape_html(event_info.get("author", ""))
            parts.append(f"  PR #{ref} by @{author}: {title}")

        parts.append("")

        # Draft content per platform
        for platform, content in drafts.items():
            label = _PLATFORM_LABELS.get(platform, platform)
            escaped_content = _escape_html(content)
            parts.append(f"<b>--- {label} ---</b>")
            parts.append(escaped_content)
            parts.append("")

        return "\n".join(parts)

    def _build_keyboard(
        self, event_id: str, enabled_platforms: set[str]
    ) -> InlineKeyboardMarkup:
        """Build the inline keyboard with approve/edit/reject and platform toggles."""
        # Row 1: Action buttons
        row1 = [
            InlineKeyboardButton("Approve", callback_data=f"approve:{event_id}"),
            InlineKeyboardButton("Edit", callback_data=f"edit:{event_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject:{event_id}"),
        ]

        # Row 2: Platform toggles
        row2 = []
        for platform, label in _PLATFORM_LABELS.items():
            check = " [on]" if platform in enabled_platforms else " [off]"
            row2.append(
                InlineKeyboardButton(
                    f"{label}{check}",
                    callback_data=f"toggle:{event_id}:{platform}",
                )
            )

        return InlineKeyboardMarkup([row1, row2])

    # --- Command handlers ---

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /start command."""
        if not update.effective_user or not self._is_authorized(update.effective_user.id):
            return
        await update.message.reply_text(
            "Crier bot is running. I'll send you drafts for approval when new "
            "events are detected on your watched repos.\n\n"
            "Commands:\n"
            "/status - Show bot status\n"
            "/history - Show recent posting history\n"
            "/draft <repo> - Manually trigger a draft"
        )

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /status command — show bot status and pending drafts."""
        if not update.effective_user or not self._is_authorized(update.effective_user.id):
            return

        settings = self._settings
        pending = await get_pending_drafts(settings.db_path)
        recent = await get_recent_drafts(settings.db_path, status="posted", days=7)

        repos = ", ".join(settings.repo_list) if settings.repo_list else "none configured"
        text = (
            f"<b>Crier Status</b>\n\n"
            f"Repos: {_escape_html(repos)}\n"
            f"Poll interval: {settings.poll_interval}s\n"
            f"Pending drafts: {len(pending)}\n"
            f"Posted (7d): {len(recent)}\n"
            f"Dry run: {'yes' if settings.dry_run else 'no'}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /history command — show recent posting history."""
        if not update.effective_user or not self._is_authorized(update.effective_user.id):
            return

        settings = self._settings
        recent = await get_recent_drafts(settings.db_path, status="posted", days=30)

        if not recent:
            await update.message.reply_text("No posts in the last 30 days.")
            return

        lines = ["<b>Recent Posts (30d)</b>\n"]
        for draft in recent[:20]:
            platform = draft.get("platform", "?")
            repo = draft.get("repo", "?")
            url = draft.get("post_url", "")
            posted_at = draft.get("posted_at", "?")
            content_preview = _escape_html(
                (draft.get("content", "")[:80] + "...")
                if len(draft.get("content", "")) > 80
                else draft.get("content", "")
            )
            line = f"[{platform}] {repo} ({posted_at[:10]})"
            if url and url != "[DRY RUN]":
                line += f" <a href='{url}'>link</a>"
            line += f"\n  {content_preview}"
            lines.append(line)

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_draft(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /draft <repo> command — manually trigger a draft for a repo."""
        if not update.effective_user or not self._is_authorized(update.effective_user.id):
            return

        if not context.args:
            await update.message.reply_text("Usage: /draft <owner/repo>")
            return

        repo = context.args[0]
        await update.message.reply_text(
            f"Manual draft requested for {_escape_html(repo)}. "
            f"Polling for recent activity...",
            parse_mode="HTML",
        )

        # The actual draft generation will be triggered by the run loop
        # Store the request in bot_data for the main loop to pick up
        if "manual_draft_requests" not in context.bot_data:
            context.bot_data["manual_draft_requests"] = []
        context.bot_data["manual_draft_requests"].append(repo)

    # --- Callback query handlers ---

    async def _callback_approve(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the Approve button press — post drafts to selected platforms."""
        query = update.callback_query
        if not query or not query.from_user or not self._is_authorized(query.from_user.id):
            if query:
                await query.answer("Unauthorized.")
            return

        await query.answer("Posting...")

        event_id = query.data.split(":")[1]
        enabled_platforms = self._platform_toggles.get(event_id, set())

        if not enabled_platforms:
            await query.edit_message_text(
                text=query.message.text + "\n\nNo platforms selected.",
                parse_mode="HTML",
            )
            return

        # Record feedback
        drafts = await get_drafts_by_event(self._settings.db_path, event_id)
        for draft in drafts:
            if draft["platform"] in enabled_platforms:
                await create_feedback(self._settings.db_path, draft["id"], "approve")
                await update_draft_status(
                    self._settings.db_path, draft["id"], status="approved"
                )

        # Post via dispatcher
        results = await post_approved_draft(
            self._settings, event_id, enabled_platforms
        )

        # Build result summary
        result_lines = []
        for platform, result in results.items():
            label = _PLATFORM_LABELS.get(platform, platform)
            if result["success"] == "true":
                url = result.get("url", "")
                if url and url != "[DRY RUN]":
                    result_lines.append(f"  {label}: Posted - {url}")
                else:
                    result_lines.append(f"  {label}: Posted")
            else:
                result_lines.append(f"  {label}: Failed - {result.get('message', '')}")

        status_text = "\n".join(result_lines) if result_lines else "  No results"

        # Edit the original message to show results
        new_text = query.message.text + f"\n\n<b>POSTED</b>\n{_escape_html(status_text)}"
        await query.edit_message_text(text=new_text, parse_mode="HTML")

        # Clean up toggles
        self._platform_toggles.pop(event_id, None)

    async def _callback_edit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle the Edit button press — enter edit conversation state."""
        query = update.callback_query
        if not query or not query.from_user or not self._is_authorized(query.from_user.id):
            if query:
                await query.answer("Unauthorized.")
            return ConversationHandler.END

        await query.answer()

        event_id = query.data.split(":")[1]
        context.user_data["editing_event_id"] = event_id

        await query.edit_message_text(
            text=(
                query.message.text
                + "\n\n<b>EDITING</b>\n"
                "Send me the revised text. Format: <code>platform: content</code>\n"
                "Example: <code>twitter: New tweet text here</code>\n\n"
                "Or /cancel to abort."
            ),
            parse_mode="HTML",
        )

        return WAITING_FOR_EDIT

    async def _receive_edit_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Receive edited text from the user and update the draft."""
        if not update.effective_user or not self._is_authorized(update.effective_user.id):
            return ConversationHandler.END

        event_id = context.user_data.get("editing_event_id")
        if not event_id:
            await update.message.reply_text("No active edit session.")
            return ConversationHandler.END

        text = update.message.text.strip()

        # Parse "platform: content" format
        platform = None
        content = text
        if ":" in text:
            candidate_platform = text.split(":", 1)[0].strip().lower()
            if candidate_platform in _PLATFORM_LABELS:
                platform = candidate_platform
                content = text.split(":", 1)[1].strip()

        # Update the draft(s)
        db_path = self._settings.db_path
        drafts = await get_drafts_by_event(db_path, event_id)

        updated_count = 0
        for draft in drafts:
            if platform and draft["platform"] != platform:
                continue
            await update_draft_content(db_path, draft["id"], content)
            await create_feedback(db_path, draft["id"], "edit", user_text=content)
            updated_count += 1

        if updated_count == 0:
            await update.message.reply_text(
                f"No drafts found for platform '{platform}'. Try again."
            )
            return WAITING_FOR_EDIT

        # Re-fetch drafts and show updated message with buttons
        drafts = await get_drafts_by_event(db_path, event_id)
        updated_drafts = {d["platform"]: d["content"] for d in drafts}

        # Get event info from first draft
        first_draft = await get_draft_by_id(db_path, drafts[0]["id"])
        event_info = {
            "repo": first_draft.get("repo", "unknown") if first_draft else "unknown",
            "event_type": first_draft.get("event_type", "push") if first_draft else "push",
            "ref": first_draft.get("ref", "") if first_draft else "",
        }

        enabled_platforms = self._platform_toggles.get(event_id, set(updated_drafts.keys()))
        self._platform_toggles[event_id] = enabled_platforms

        message_text = self._format_draft_message(event_info, updated_drafts)
        keyboard = self._build_keyboard(event_id, enabled_platforms)

        await update.message.reply_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        context.user_data.pop("editing_event_id", None)
        return ConversationHandler.END

    async def _cancel_edit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Cancel the edit conversation."""
        context.user_data.pop("editing_event_id", None)
        await update.message.reply_text("Edit cancelled.")
        return ConversationHandler.END

    async def _callback_reject(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the Reject button press — mark drafts as rejected."""
        query = update.callback_query
        if not query or not query.from_user or not self._is_authorized(query.from_user.id):
            if query:
                await query.answer("Unauthorized.")
            return

        await query.answer("Rejected.")

        event_id = query.data.split(":")[1]

        drafts = await get_drafts_by_event(self._settings.db_path, event_id)
        for draft in drafts:
            await update_draft_status(
                self._settings.db_path, draft["id"], status="rejected"
            )
            await create_feedback(self._settings.db_path, draft["id"], "reject")

        new_text = query.message.text + "\n\n<b>REJECTED</b>"
        await query.edit_message_text(text=new_text, parse_mode="HTML")

        self._platform_toggles.pop(event_id, None)

    async def _callback_toggle_platform(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle platform toggle button press — enable/disable a platform."""
        query = update.callback_query
        if not query or not query.from_user or not self._is_authorized(query.from_user.id):
            if query:
                await query.answer("Unauthorized.")
            return

        parts = query.data.split(":")
        event_id = parts[1]
        platform = parts[2]

        enabled = self._platform_toggles.get(event_id, set())
        if platform in enabled:
            enabled.discard(platform)
            await query.answer(f"{_PLATFORM_LABELS.get(platform, platform)} disabled")
        else:
            enabled.add(platform)
            await query.answer(f"{_PLATFORM_LABELS.get(platform, platform)} enabled")
        self._platform_toggles[event_id] = enabled

        # Update the keyboard to reflect the new state
        keyboard = self._build_keyboard(event_id, enabled)
        await query.edit_message_reply_markup(reply_markup=keyboard)


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram messages."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
