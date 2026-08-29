"""CLI interface for crier."""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="crier",
    help="Allowlisted release and blog drafts. Telegram optional.",
    no_args_is_help=True,
)
console = Console()


def _get_settings(**overrides):
    from crier.config import get_settings

    return get_settings(**overrides)


@app.command()
def run(
    log_level: str = typer.Option("INFO", help="Logging level"),
    use_claude: bool = typer.Option(
        False,
        "--use-claude",
        help="Use Claude for draft text. Default is the free template.",
    ),
) -> None:
    """Poll GitHub and write drafts to sqlite. Telegram is optional."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = _get_settings()
    if use_claude:
        settings.use_claude = True

    if not settings.repo_list:
        console.print("[red]No repos configured. Set CRIER_REPOS.[/red]")
        raise typer.Exit(1)

    if not settings.has_telegram:
        console.print(
            "[yellow]Telegram not configured. Drafts will be written to sqlite "
            f"({settings.db_path}). Set CRIER_TELEGRAM_BOT_TOKEN and "
            "CRIER_TELEGRAM_OWNER_ID to review in Telegram.[/yellow]"
        )

    if settings.use_claude and not settings.has_anthropic:
        console.print(
            "[yellow]--use-claude set but no Anthropic key; using templates.[/yellow]"
        )

    console.print(
        Panel(
            f"Repos: {', '.join(settings.repo_list)}\n"
            f"Poll interval: {settings.poll_interval}s\n"
            f"Interest threshold: {settings.interest_threshold}\n"
            f"Claude drafts: {settings.use_claude}\n"
            f"Telegram: {settings.has_telegram}\n"
            f"Dry run: {settings.dry_run}",
            title="[bold]Crier Starting[/bold]",
        )
    )

    asyncio.run(_run_bot_and_watcher(settings))


async def _run_bot_and_watcher(settings) -> None:
    """Poll GitHub; optionally run the Telegram bot."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from crier.db import init_db
    from crier.pipeline import ingest_event
    from crier.watcher import GitHubWatcher

    await init_db(settings.db_path)

    bot = None
    bot_app = None
    if settings.has_telegram:
        from crier.bot import CrierBot

        bot = CrierBot(settings)
        bot_app = bot.setup()

    watcher = GitHubWatcher(
        settings.github_token,
        settings.db_path,
        release_repos=settings.release_repo_list,
        blog_repos=settings.blog_repo_list,
    )

    async def poll_and_draft() -> None:
        """Single poll cycle: allowlisted events become sqlite drafts."""
        try:
            events = await watcher.poll_repos(settings.repo_list)
            for event in events:
                await ingest_event(event, settings, bot=bot)
        except Exception:
            logging.getLogger("crier").exception("Error in poll cycle")

    async def check_manual_requests() -> None:
        """Handle /draft requests from Telegram, if the bot is running."""
        if bot_app is None:
            return

        manual_requests = bot_app.bot_data.get("manual_draft_requests", [])
        if not manual_requests:
            return

        bot_app.bot_data["manual_draft_requests"] = []

        for repo in manual_requests:
            try:
                event = await watcher.fetch_latest_announcement(repo)
                if event is None:
                    logging.getLogger("crier").info(
                        "No allowlisted announcement for manual draft of %s", repo
                    )
                    continue
                await ingest_event(event, settings, bot=bot)
            except Exception:
                logging.getLogger("crier").exception(
                    "Error processing manual draft for %s", repo
                )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_and_draft, "interval", seconds=settings.poll_interval)
    if bot_app is not None:
        scheduler.add_job(check_manual_requests, "interval", seconds=5)
    scheduler.start()

    try:
        if bot_app is not None:
            async with bot_app:
                await bot_app.start()
                await bot_app.updater.start_polling()
                console.print("[green]Crier is running. Press Ctrl+C to stop.[/green]")
                await _sleep_until_cancelled()
                scheduler.shutdown()
                await bot_app.updater.stop()
                await bot_app.stop()
                await bot_app.shutdown()
        else:
            console.print(
                "[green]Crier is polling without Telegram. "
                f"Drafts write to {settings.db_path}. Press Ctrl+C to stop.[/green]"
            )
            await _sleep_until_cancelled()
            scheduler.shutdown()
    except (KeyboardInterrupt, asyncio.CancelledError):
        scheduler.shutdown()


async def _sleep_until_cancelled() -> None:
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        return


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
            f"Claude drafts: {settings.use_claude}\n"
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
    content_type: str = typer.Option(
        "digest", help="Content type: digest, release, spotlight (unused by templates)"
    ),
    days: int = typer.Option(7, help="Look back this many days"),
    use_claude: bool = typer.Option(
        False,
        "--use-claude",
        help="Use Claude for draft text. Default is the free template.",
    ),
) -> None:
    """Generate a one-shot draft for an allowlisted repo (no posting).

    Writes the draft to sqlite. Telegram is not required. Anthropic is not
    required unless --use-claude is set.
    """
    settings = _get_settings()
    if use_claude:
        settings.use_claude = True

    console.print(f"[dim]Generating {content_type} draft for {repo}...[/dim]")

    result = asyncio.run(_generate_one_shot_draft(settings, repo))

    if not result:
        console.print(
            "[yellow]No allowlisted announcement for this repo. "
            "Crier drafts tagged releases of OpenAdaptAI/OpenAdapt and "
            "OpenAdaptAI/openadapt-flow, and new posts on "
            "OpenAdaptAI/openadapt-blog.[/yellow]"
        )
        return

    for platform, text in result.items():
        console.print(Panel(str(text), title=f"[bold]{platform}[/bold]"))


async def _generate_one_shot_draft(settings, repo: str) -> dict[str, str]:
    """Fetch the latest allowlisted event, draft it, and persist to sqlite."""
    from crier.db import init_db
    from crier.drafter import generate_drafts
    from crier.pipeline import ingest_event
    from crier.watcher import GitHubWatcher

    await init_db(settings.db_path)
    watcher = GitHubWatcher(
        settings.github_token,
        settings.db_path,
        release_repos=settings.release_repo_list,
        blog_repos=settings.blog_repo_list,
    )
    event = await watcher.fetch_latest_announcement(repo)
    if event is None:
        return {}

    await ingest_event(event, settings, bot=None)
    return generate_drafts(
        event,
        api_key=settings.anthropic_api_key,
        use_claude=settings.use_claude,
    )


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
