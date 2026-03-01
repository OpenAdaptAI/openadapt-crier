"""CLI interface for crier."""

from __future__ import annotations

import asyncio
import json
import logging

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="crier",
    help="Event-driven social media approval bot with Telegram.",
    no_args_is_help=True,
)
console = Console()


def _get_settings(**overrides):
    from crier.config import get_settings

    return get_settings(**overrides)


@app.command()
def run(
    log_level: str = typer.Option("INFO", help="Logging level"),
) -> None:
    """Start the bot and watcher loop (runs forever)."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = _get_settings()

    if not settings.has_telegram:
        console.print(
            "[red]Telegram not configured. Set CRIER_TELEGRAM_BOT_TOKEN "
            "and CRIER_TELEGRAM_OWNER_ID.[/red]"
        )
        raise typer.Exit(1)

    if not settings.repo_list:
        console.print("[red]No repos configured. Set CRIER_REPOS.[/red]")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"Repos: {', '.join(settings.repo_list)}\n"
            f"Poll interval: {settings.poll_interval}s\n"
            f"Interest threshold: {settings.interest_threshold}\n"
            f"Dry run: {settings.dry_run}",
            title="[bold]Crier Starting[/bold]",
        )
    )

    asyncio.run(_run_bot_and_watcher(settings))


async def _run_bot_and_watcher(settings) -> None:
    """Run the Telegram bot and GitHub watcher concurrently."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from crier.bot import CrierBot
    from crier.db import init_db
    from crier.drafter import generate_drafts, score_interestingness
    from crier.watcher import GitHubWatcher

    # Initialize database
    await init_db(settings.db_path)

    # Set up the Telegram bot
    bot = CrierBot(settings)
    bot_app = bot.setup()

    # Set up the GitHub watcher
    watcher = GitHubWatcher(settings.github_token, settings.db_path)

    async def poll_and_draft() -> None:
        """Single poll cycle: check for events, score, draft, and send for approval."""
        from crier.db import create_draft, create_event

        try:
            events = await watcher.poll_repos(settings.repo_list)
            if not events:
                return

            for event in events:
                # Score interestingness for push events
                if event.event_type == "push":
                    commits = event.payload.get("commits", [])
                    score = score_interestingness(
                        commits, event.repo, settings.anthropic_api_key
                    )
                    if score < settings.interest_threshold:
                        logging.getLogger("crier").info(
                            "Skipping %s event on %s (score %.1f < threshold %.1f)",
                            event.event_type,
                            event.repo,
                            score,
                            settings.interest_threshold,
                        )
                        continue
                else:
                    # Releases and PR merges always pass
                    score = 10.0

                # Store the event
                event_id = await create_event(
                    settings.db_path,
                    repo=event.repo,
                    event_type=event.event_type,
                    ref=event.ref,
                    payload=json.dumps(event.payload),
                )

                # Generate drafts
                drafts = generate_drafts(
                    event, api_key=settings.anthropic_api_key
                )

                # Store drafts in DB
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

                # Send to Telegram for approval
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

        except Exception:
            logging.getLogger("crier").exception("Error in poll cycle")

    # Handle manual draft requests from /draft command
    async def check_manual_requests() -> None:
        """Check for manual draft requests submitted via the /draft command."""
        from crier.db import create_draft, create_event

        manual_requests = bot_app.bot_data.get("manual_draft_requests", [])
        if not manual_requests:
            return

        bot_app.bot_data["manual_draft_requests"] = []

        for repo in manual_requests:
            try:
                events = await watcher.poll_repos([repo])
                if not events:
                    # Create a synthetic event for manual drafts
                    event_id = await create_event(
                        settings.db_path,
                        repo=repo,
                        event_type="push",
                        ref="manual",
                        payload=json.dumps({"commits": [], "manual": True}),
                    )

                    from crier.watcher import GitHubEvent

                    synthetic_event = GitHubEvent(
                        repo=repo,
                        event_type="push",
                        ref="manual",
                        payload={"commits": [], "manual": True},
                    )
                    drafts = generate_drafts(
                        synthetic_event, api_key=settings.anthropic_api_key
                    )
                    for platform, content in drafts.items():
                        if platform == "summary":
                            continue
                        await create_draft(
                            settings.db_path,
                            event_id=event_id,
                            platform=platform,
                            content=content,
                        )
                    event_info = {
                        "repo": repo,
                        "event_type": "push",
                        "ref": "manual",
                    }
                    await bot.send_draft_for_approval(
                        chat_id=settings.telegram_owner_id,
                        event_id=event_id,
                        drafts={k: v for k, v in drafts.items() if k != "summary"},
                        event_info=event_info,
                    )
                else:
                    for event in events:
                        event_id = await create_event(
                            settings.db_path,
                            repo=event.repo,
                            event_type=event.event_type,
                            ref=event.ref,
                            payload=json.dumps(event.payload),
                        )
                        drafts = generate_drafts(
                            event, api_key=settings.anthropic_api_key
                        )
                        for platform, content in drafts.items():
                            if platform == "summary":
                                continue
                            await create_draft(
                                settings.db_path,
                                event_id=event_id,
                                platform=platform,
                                content=content,
                            )
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
            except Exception:
                logging.getLogger("crier").exception(
                    "Error processing manual draft for %s", repo
                )

    # Set up the scheduler for polling
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_and_draft, "interval", seconds=settings.poll_interval)
    scheduler.add_job(check_manual_requests, "interval", seconds=5)
    scheduler.start()

    # Run the bot (blocks until stopped)
    async with bot_app:
        await bot_app.start()
        await bot_app.updater.start_polling()

        console.print("[green]Crier is running. Press Ctrl+C to stop.[/green]")

        try:
            # Keep running until interrupted
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            scheduler.shutdown()
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()


@app.command()
def status() -> None:
    """Show bot status and configuration summary."""
    settings = _get_settings()
    console.print(
        Panel(
            f"Repos: {', '.join(settings.repo_list) or 'none'}\n"
            f"Poll interval: {settings.poll_interval}s\n"
            f"Interest threshold: {settings.interest_threshold}\n"
            f"Telegram configured: {settings.has_telegram}\n"
            f"Anthropic configured: {settings.has_anthropic}\n"
            f"Discord configured: {settings.has_discord}\n"
            f"Twitter configured: {settings.has_twitter}\n"
            f"LinkedIn configured: {settings.has_linkedin}\n"
            f"DB path: {settings.db_path}\n"
            f"Dry run: {settings.dry_run}",
            title="[bold]Crier Status[/bold]",
        )
    )

    # Show pending drafts if DB exists
    import os

    if os.path.exists(settings.db_path):
        pending = asyncio.run(_get_pending_count(settings.db_path))
        console.print(f"\nPending drafts: {pending}")


async def _get_pending_count(db_path: str) -> int:
    from crier.db import get_pending_drafts

    drafts = await get_pending_drafts(db_path)
    return len(drafts)


@app.command()
def drafts(
    draft_status: str = typer.Option("pending", "--status", help="Filter by status"),
    days: int = typer.Option(7, help="Look back this many days"),
    platform: str = typer.Option("", help="Filter by platform"),
) -> None:
    """List recent drafts, optionally filtered by status and platform."""
    settings = _get_settings()
    result = asyncio.run(
        _list_drafts(settings.db_path, draft_status, days, platform or None)
    )

    if not result:
        console.print(f"[yellow]No {draft_status} drafts in the last {days} days.[/yellow]")
        return

    table = Table(title=f"Drafts ({draft_status}, last {days}d)")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Platform")
    table.add_column("Repo")
    table.add_column("Content", max_width=60)
    table.add_column("Status")
    table.add_column("Created")

    for draft in result:
        table.add_row(
            draft["id"][:8],
            draft.get("platform", "?"),
            draft.get("repo", "?"),
            (draft.get("content", "")[:57] + "...")
            if len(draft.get("content", "")) > 57
            else draft.get("content", ""),
            draft.get("status", "?"),
            (draft.get("created_at", "?")[:16]),
        )

    console.print(table)


async def _list_drafts(
    db_path: str, status: str, days: int, platform: str | None
) -> list[dict]:
    from crier.db import get_recent_drafts

    return await get_recent_drafts(db_path, status=status, days=days, platform=platform)


@app.command()
def draft(
    repo: str = typer.Argument(..., help="Repository (owner/repo)"),
    content_type: str = typer.Option("digest", help="Content type: digest, release, spotlight"),
    days: int = typer.Option(7, help="Look back this many days"),
) -> None:
    """Manually generate a one-shot draft for a repo (no Telegram, no posting)."""
    settings = _get_settings()

    console.print(f"[dim]Generating {content_type} draft for {repo}...[/dim]")

    result = asyncio.run(_generate_one_shot_draft(settings, repo, content_type, days))

    if not result:
        console.print("[yellow]No content generated.[/yellow]")
        return

    for platform, text in result.items():
        console.print(Panel(str(text), title=f"[bold]{platform}[/bold]"))


async def _generate_one_shot_draft(
    settings, repo: str, content_type: str, days: int
) -> dict[str, str]:
    """Generate a draft without storing it or sending to Telegram."""
    from crier.watcher import GitHubEvent, GitHubWatcher
    from crier.drafter import generate_drafts

    watcher = GitHubWatcher(settings.github_token, settings.db_path)

    # We need a temporary db for the watcher
    from crier.db import init_db

    await init_db(settings.db_path)

    events = await watcher.poll_repos([repo])
    if not events:
        # Create a synthetic event
        event = GitHubEvent(
            repo=repo,
            event_type="push",
            ref="manual",
            payload={"commits": [], "manual": True},
        )
    else:
        event = events[0]

    return generate_drafts(event, api_key=settings.anthropic_api_key)


@app.command()
def history(
    platform: str = typer.Option("", help="Filter by platform"),
    days: int = typer.Option(30, help="Look back this many days"),
) -> None:
    """Show posting history."""
    settings = _get_settings()

    result = asyncio.run(
        _list_drafts(settings.db_path, "posted", days, platform or None)
    )

    if not result:
        console.print(f"[yellow]No posts in the last {days} days.[/yellow]")
        return

    table = Table(title=f"Posting History (last {days}d)")
    table.add_column("Platform")
    table.add_column("Repo")
    table.add_column("Content", max_width=50)
    table.add_column("Posted At")
    table.add_column("URL", max_width=40)

    for draft in result:
        table.add_row(
            draft.get("platform", "?"),
            draft.get("repo", "?"),
            (draft.get("content", "")[:47] + "...")
            if len(draft.get("content", "")) > 47
            else draft.get("content", ""),
            (draft.get("posted_at", "?") or "?")[:16],
            draft.get("post_url", "") or "",
        )

    console.print(table)


if __name__ == "__main__":
    app()
