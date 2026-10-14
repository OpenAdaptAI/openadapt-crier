# openadapt-crier

Crier watches GitHub repositories and uses Claude to draft social posts about
new releases, merged pull requests, and notable commits. It sends the drafts to
you in Telegram, where you approve, edit, or reject them. Approved posts go to
Discord, X (Twitter), and LinkedIn. Nothing is posted until you approve it.

Crier is for maintainers who want to announce their project's progress without
writing every post by hand or letting a bot post on its own. The OpenAdapt team
uses it for its own announcements. It isn't part of the
[OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) product, and no OpenAdapt
package depends on it.

## Quick start

You need Python 3.10 or later. To see drafts for any public repository, without
Telegram and without posting anything:

```bash
pip install git+https://github.com/OpenAdaptAI/openadapt-crier
export ANTHROPIC_API_KEY=sk-ant-...
crier draft OpenAdaptAI/OpenAdapt
```

Crier prints a draft for each platform and a one-line summary. `crier draft`
doesn't post, doesn't send anything to Telegram, and doesn't change Crier's
database, so it's safe to run next to a running bot. Without
`ANTHROPIC_API_KEY`, each draft is a one-line description of the activity, such
as `8 new commit(s) on owner/repo: <latest commit message>`.

To run the approval bot, create a Telegram bot with
[@BotFather](https://t.me/BotFather), look up your numeric Telegram user ID (not
your @username), and run:

```bash
export CRIER_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
export CRIER_TELEGRAM_OWNER_ID=123456789
export CRIER_REPOS=owner/repo1,owner/repo2
export CRIER_GITHUB_TOKEN=ghp_...   # needed for polling; see Configuration
export CRIER_DRY_RUN=true           # approve drafts without posting while you try it
crier run
```

Send `/start` to your bot. When a watched repository has new activity, the bot
sends you the drafts. To post for real, add the platform credentials listed
under [Configuration](#configuration) and set `CRIER_DRY_RUN=false`.

## How it works

```
GitHub API ──> watcher ──> Claude (score, draft) ──> Telegram (you approve) ──> Discord, X, LinkedIn
```

1. Every `CRIER_POLL_INTERVAL` seconds, Crier asks the GitHub API for each
   repository's new commits on the default branch, new releases, and newly
   merged pull requests. It skips merge commits, version bumps, commits marked
   `[skip ci]`, and activity from Dependabot, Renovate, and GitHub Actions.
2. Crier groups new commits into one batch, and Claude scores the batch from 0
   to 10. Batches below `CRIER_INTEREST_THRESHOLD` are dropped. If Crier can't
   get a score (no API key, an API error, or a reply without a number), it drops
   the batch and logs a warning. Releases and merged pull requests aren't
   scored; they always get drafts.
3. Claude writes one draft per platform: up to 280 characters for X, 2,000 for
   Discord, and 3,000 for LinkedIn.
4. The bot sends you the drafts in Telegram with **Approve**, **Edit**, and
   **Reject** buttons and an on/off toggle for each platform.
5. When you approve, Crier posts to the platforms that are on, through
   [openadapt-herald](https://github.com/OpenAdaptAI/openadapt-herald), and
   saves the post URLs.

Crier keeps events, drafts, your decisions, and the last commit, release, and
pull request it has seen in a local SQLite file (`CRIER_DB_PATH`).

## Commands

| Command | Description |
|---------|-------------|
| `crier draft <owner/repo>` | Print drafts for a repository's recent activity. Doesn't post or use Telegram. |
| `crier run` | Start the Telegram bot and the polling loop. Runs until you stop it. |
| `crier status` | Show the configuration and the number of pending drafts. |
| `crier drafts` | List recent drafts. Filter with `--status`, `--platform`, and `--days`. |
| `crier history` | List posted drafts and their URLs. |

In Telegram, the bot answers only the user set in `CRIER_TELEGRAM_OWNER_ID`:

- `/start` shows help.
- `/status` shows the watched repositories and the number of pending drafts.
- `/history` lists posts from the last 30 days.
- `/draft <owner/repo>` asks for drafts for a repository now.

To change a draft, press **Edit** and send the new text. Start it with a
platform name, such as `twitter: New text`, to change one platform's draft;
otherwise the text replaces every platform's draft.

## Configuration

Set these as environment variables, or put them in a `.env` file in the
directory where you run Crier. [.env.example](.env.example) lists them all.

| Variable | Description | Default |
|----------|-------------|---------|
| `CRIER_TELEGRAM_BOT_TOKEN` | Token for your bot, from @BotFather | Required for `crier run` |
| `CRIER_TELEGRAM_OWNER_ID` | Numeric Telegram user ID of the one person who can approve posts | Required for `crier run` |
| `CRIER_REPOS` | Comma-separated `owner/repo` list to watch | Required for `crier run` |
| `CRIER_ANTHROPIC_API_KEY` | Anthropic API key for scoring and drafting | `$ANTHROPIC_API_KEY` |
| `CRIER_GITHUB_TOKEN` | GitHub token. Without one, GitHub allows 60 requests an hour, and Crier makes 3 per repository on each poll. | `$GH_TOKEN` |
| `CRIER_POLL_INTERVAL` | Seconds between polls | `60` |
| `CRIER_INTEREST_THRESHOLD` | Minimum score, 0 to 10, that a batch of commits needs to get drafts | `5.0` |
| `CRIER_DRY_RUN` | Approve drafts without posting them or marking them as posted | `false` |
| `CRIER_DB_PATH` | SQLite file for events, drafts, and polling state | `./crier.db` |
| `CRIER_DISCORD_WEBHOOK_URL` | Discord webhook URL | `$DISCORD_WEBHOOK_URL` |
| `CRIER_TWITTER_CONSUMER_KEY`, `CRIER_TWITTER_CONSUMER_SECRET`, `CRIER_TWITTER_ACCESS_TOKEN`, `CRIER_TWITTER_ACCESS_TOKEN_SECRET` | X (Twitter) API credentials. All four are required. | The same names without `CRIER_` |
| `CRIER_LINKEDIN_ACCESS_TOKEN` | LinkedIn access token | `$LINKEDIN_ACCESS_TOKEN` |

When you approve a draft, Crier skips any platform that has no credentials.

## Development

```bash
git clone https://github.com/OpenAdaptAI/openadapt-crier
cd openadapt-crier
pip install -e ".[dev]"
pytest
```

Crier installs openadapt-herald from GitHub at the commit pinned in
`pyproject.toml`. To work on both at once, also run
`pip install -e ../openadapt-herald` from a herald checkout next to this one.

## Limits

- On the first poll of a repository, Crier has no record of what it has seen.
  It treats the latest 50 commits (as one batch), the latest 5 releases, and up
  to 10 recently merged pull requests as new, so expect a burst of drafts on
  first start. Reject the ones you don't want.
- Crier polls GitHub instead of using webhooks, so drafts arrive up to one poll
  interval after the activity.
- Approval happens only in Telegram, and only one person can approve.

Repository status, what data goes to which service, and how approval is
enforced are described in [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## License

MIT. See [LICENSE](LICENSE).
