# Status and operator notes

This page is for people who run Crier or review what it does with their data
and accounts. For installation and usage, see the [README](../README.md).

## Repository status

openadapt-crier is support tooling that the OpenAdapt team uses to announce its
own releases. It isn't part of the OpenAdapt product, and no OpenAdapt package
depends on it.

The OpenAdapt product is
[openadapt-flow](https://github.com/OpenAdaptAI/openadapt-flow), installed with
`pip install openadapt` through the
[OpenAdapt launcher](https://github.com/OpenAdaptAI/OpenAdapt). It compiles a
demonstrated GUI workflow into a deterministic program that runs locally.

The [repository lifecycle registry](https://github.com/OpenAdaptAI/.github/blob/main/REPOSITORY_LIFECYCLE.md)
lists this repository under **Support**, along with the label for every other
OpenAdapt repository.

## What Crier sends, and where

| Data | Sent to | When |
| --- | --- | --- |
| Repository names, and your GitHub token if one is set | GitHub API | On every poll, and on each `crier draft` |
| The first line of each new commit message, commit SHAs and author logins, release tags and names with the first 1,000 characters of the release notes, and pull request numbers, titles, and authors with the first 500 characters of the description | Anthropic API | When Crier scores commits or writes drafts, and only if an Anthropic API key is set |
| Drafts and a summary of the activity they describe | Telegram, to the chat of `CRIER_TELEGRAM_OWNER_ID` only | For each release, merged pull request, and commit batch that passes the score |
| The text of approved drafts | Discord, X, and LinkedIn | Only after you press **Approve**, only to the platforms that are on, and never when `CRIER_DRY_RUN` is `true` |

`crier draft` sends to GitHub and, if a key is set, to Anthropic. It sends
nothing to Telegram or the social platforms.

## How approval is enforced

- Every command handler and button handler checks the Telegram user ID against
  `CRIER_TELEGRAM_OWNER_ID`. The bot ignores commands from anyone else and
  answers their button presses with "Unauthorized."
- The only code path that posts is the **Approve** button handler. The polling
  loop and `crier draft` only create drafts.

## Failure behavior

- If Crier can't score a batch of commits, it drops the batch, logs a warning,
  and makes no drafts.
- If Claude can't write drafts (an API error or a reply that isn't valid JSON),
  Crier sends a one-line description of the activity instead. You can edit or
  reject it like any other draft.
- If posting fails on a platform, that draft goes back to pending with the
  error saved, and the Telegram message shows the failure.
- In dry run, approved drafts aren't posted and aren't marked as posted, so
  they don't appear in `crier history`.

## Local data

Crier stores everything in the SQLite file at `CRIER_DB_PATH`:

- `events`: the repository, event type, reference, and the GitHub data used
  for drafting.
- `drafts`: draft text, status, Telegram message IDs, post URLs, and errors.
- `feedback`: each approve, edit, and reject action, including edited text.
- `tracking`: the last commit SHA, release tag, and pull request number seen
  for each repository.

Crier reads credentials from the environment or a `.env` file and doesn't write
them to the database.
