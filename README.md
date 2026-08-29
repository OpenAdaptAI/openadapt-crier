# openadapt-crier

> [!IMPORTANT]
> **Status: Internal tooling — not the product.** Crier is release-announcement
> automation used by the OpenAdapt team. It is not part of the OpenAdapt product
> and is not required by any OpenAdapt package.
>
> The OpenAdapt product is the demonstration compiler,
> [`openadapt-flow`](https://github.com/OpenAdaptAI/openadapt-flow), installed
> via the [`OpenAdapt`](https://github.com/OpenAdaptAI/OpenAdapt) launcher
> (`pip install openadapt`): it compiles a demonstrated GUI workflow into a
> deterministic, locally executable program. Healthy runs make no model calls,
> and it halts instead of guessing when verification fails. Lifecycle labels for
> every repository are in the
> [repository lifecycle registry](https://github.com/OpenAdaptAI/.github/blob/main/REPOSITORY_LIFECYCLE.md).

Event-driven social media approval bot. Telegram is optional. Anthropic is
optional.

## What it announces

By default Crier drafts posts for three event types and nothing else:

- Tagged GitHub Releases of `OpenAdaptAI/OpenAdapt`
- Tagged GitHub Releases of `OpenAdaptAI/openadapt-flow`
- New posts on `OpenAdaptAI/openadapt-blog`

Commits, chore PRs, docs-only changes, prereleases, and research-repo noise
score 0 with no API call. If `ANTHROPIC_API_KEY` is missing, the scorer does
not fall back to 5.0. Allowlisted events score 9; everything else scores 0.

Draft text is a template: the program noun, `pip install openadapt`, and a
link to the release notes or blog URL. Claude is off unless you pass
`--use-claude` (or set `CRIER_USE_CLAUDE=true`) and provide an Anthropic key.

## How it works

```
GitHub Events ──> Watcher ──> Allowlist scorer ──> Template drafts ──> sqlite
  (releases,        (polls       (9 or 0,            (program noun,       (Telegram
   blog posts)       GitHub       no LLM)             pip install,         optional)
                     API)                             link)
                                                          │
                                                          └── Dispatcher ──> Herald
                                                              (on approve)    (needs
                                                                              Herald
                                                                              credentials)
```

Approving a draft still posts through
[openadapt-herald](https://github.com/OpenAdaptAI/openadapt-herald). Auto-post
needs Herald credentials (Discord webhook, Twitter/X tokens, and/or a LinkedIn
token). Without those, drafts stay in sqlite.

## Quick start (no Anthropic, no Telegram)

```bash
pip install .

# Optional: higher GitHub rate limits
export CRIER_GITHUB_TOKEN="ghp_..."   # or GH_TOKEN

# Defaults watch OpenAdapt, openadapt-flow, and openadapt-blog.
# Override with CRIER_REPOS if you need a different list.

# One-shot draft of the latest tagged release. Writes to sqlite.
crier draft OpenAdaptAI/openadapt-flow

# Same for a new blog post, if the latest post is what you want to inspect.
crier draft OpenAdaptAI/openadapt-blog

# List what was stored
crier drafts
```

`crier draft` does not post. It does not need `ANTHROPIC_API_KEY`. It does not
need Telegram.

## Optional: poll loop without Telegram

```bash
# Drafts still write to sqlite. Do not pass a Telegram token if you
# only want local drafts.
crier run
```

`crier run` no longer exits when Telegram is unset. Press Ctrl+C to stop.

## Optional: Telegram review

```bash
export CRIER_TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
export CRIER_TELEGRAM_OWNER_ID="your-telegram-user-id"
crier run
```

Draft messages include inline buttons to **Approve**, **Edit**, or **Reject**,
plus per-platform toggles (Twitter, Discord, LinkedIn). Approve still needs
Herald credentials to publish.

## Optional: Claude drafts

Templates are the default. To send allowlisted events through Claude:

```bash
export ANTHROPIC_API_KEY="your-anthropic-key"
crier draft OpenAdaptAI/openadapt-flow --use-claude
```

Without a key, `--use-claude` falls back to the template. Non-allowlisted
events still score 0 and never call the API.

## CLI commands

| Command | Description |
|---------|-------------|
| `crier run` | Poll GitHub and write drafts to sqlite (Telegram optional) |
| `crier status` | Show configuration and pending draft count |
| `crier draft <owner/repo>` | One-shot draft for the latest allowlisted event |
| `crier drafts` | List recent drafts, with optional `--status` and `--platform` filters |
| `crier history` | Show posting history |

## Configuration

All settings can be set via `CRIER_`-prefixed environment variables or a `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `CRIER_REPOS` | Comma-separated `owner/repo` list | OpenAdapt + openadapt-flow + openadapt-blog |
| `CRIER_ALLOWLIST_RELEASE_REPOS` | Tagged releases that score 9 | `OpenAdaptAI/OpenAdapt,OpenAdaptAI/openadapt-flow` |
| `CRIER_ALLOWLIST_BLOG_REPOS` | Blog repos whose new posts score 9 | `OpenAdaptAI/openadapt-blog` |
| `CRIER_GITHUB_TOKEN` | GitHub API token (higher rate limits) | `$GH_TOKEN` |
| `CRIER_TELEGRAM_BOT_TOKEN` | Telegram bot token | (optional) |
| `CRIER_TELEGRAM_OWNER_ID` | Telegram user ID for approvals | (optional) |
| `CRIER_ANTHROPIC_API_KEY` | Anthropic API key | `$ANTHROPIC_API_KEY` (unused unless Claude is on) |
| `CRIER_USE_CLAUDE` | Use Claude for draft text | `false` |
| `CRIER_POLL_INTERVAL` | Seconds between polls | `60` |
| `CRIER_INTEREST_THRESHOLD` | Minimum score (0-10) to generate drafts | `5.0` |
| `CRIER_DRY_RUN` | Skip actual posting | `false` |

Herald credentials (`DISCORD_WEBHOOK_URL`, Twitter/X keys, `LINKEDIN_ACCESS_TOKEN`)
are required only to auto-post after approval.

## Telegram bot commands

Once the bot is running, these commands are available in Telegram:

- `/start` -- Show help
- `/status` -- Bot status and pending draft count
- `/history` -- Recent posting history
- `/draft <owner/repo>` -- Manually trigger a draft

## Tests

```bash
pip install ".[dev]"
pytest
```

## License

MIT -- see [LICENSE](LICENSE).
