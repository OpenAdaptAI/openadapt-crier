"""Async SQLite database for event tracking, drafts, and feedback."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ref TEXT,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    event_id TEXT REFERENCES events(id),
    platform TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    telegram_message_id INTEGER,
    telegram_chat_id INTEGER,
    interestingness_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    posted_at TIMESTAMP,
    post_url TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    draft_id TEXT REFERENCES drafts(id),
    action TEXT NOT NULL,
    user_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracking (
    repo TEXT PRIMARY KEY,
    last_seen_sha TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db(db_path: str) -> None:
    """Initialize the database schema. Creates tables if they do not exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def create_event(
    db_path: str,
    repo: str,
    event_type: str,
    ref: str | None = None,
    payload: str | None = None,
) -> str:
    """Insert a new event and return its ID."""
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO events (id, repo, event_type, ref, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, repo, event_type, ref, payload, now),
        )
        await db.commit()
    return event_id


async def create_draft(
    db_path: str,
    event_id: str,
    platform: str,
    content: str,
    interestingness_score: float | None = None,
) -> str:
    """Insert a new draft and return its ID."""
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO drafts "
            "(id, event_id, platform, content, status, interestingness_score, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (draft_id, event_id, platform, content, interestingness_score, now),
        )
        await db.commit()
    return draft_id


async def update_draft_status(
    db_path: str,
    draft_id: str,
    status: str,
    post_url: str | None = None,
    error: str | None = None,
    telegram_message_id: int | None = None,
    telegram_chat_id: int | None = None,
) -> None:
    """Update a draft's status and optional metadata fields."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        updates = ["status = ?"]
        params: list = [status]

        if status == "approved":
            updates.append("approved_at = ?")
            params.append(now)
        elif status == "posted":
            updates.append("posted_at = ?")
            params.append(now)

        if post_url is not None:
            updates.append("post_url = ?")
            params.append(post_url)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if telegram_message_id is not None:
            updates.append("telegram_message_id = ?")
            params.append(telegram_message_id)
        if telegram_chat_id is not None:
            updates.append("telegram_chat_id = ?")
            params.append(telegram_chat_id)

        params.append(draft_id)
        await db.execute(
            f"UPDATE drafts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()


async def update_draft_content(db_path: str, draft_id: str, content: str) -> None:
    """Update a draft's content text (used during edit flow)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE drafts SET content = ?, status = 'pending' WHERE id = ?",
            (content, draft_id),
        )
        await db.commit()


async def get_pending_drafts(db_path: str) -> list[dict]:
    """Return all drafts with status 'pending', ordered by creation time."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT d.*, e.repo, e.event_type, e.ref, e.payload "
            "FROM drafts d LEFT JOIN events e ON d.event_id = e.id "
            "WHERE d.status = 'pending' ORDER BY d.created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_draft_by_id(db_path: str, draft_id: str) -> dict | None:
    """Return a single draft by ID, joined with its event."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT d.*, e.repo, e.event_type, e.ref, e.payload "
            "FROM drafts d LEFT JOIN events e ON d.event_id = e.id "
            "WHERE d.id = ?",
            (draft_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_drafts_by_event(db_path: str, event_id: str) -> list[dict]:
    """Return all drafts for a given event ID."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM drafts WHERE event_id = ? ORDER BY platform",
            (event_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def create_feedback(
    db_path: str,
    draft_id: str,
    action: str,
    user_text: str | None = None,
) -> str:
    """Record a feedback action (approve, reject, edit) and return its ID."""
    feedback_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO feedback (id, draft_id, action, user_text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (feedback_id, draft_id, action, user_text, now),
        )
        await db.commit()
    return feedback_id


async def get_recent_drafts(
    db_path: str,
    status: str | None = None,
    days: int = 7,
    platform: str | None = None,
) -> list[dict]:
    """Return drafts from the last N days, optionally filtered by status and platform."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = (
            "SELECT d.*, e.repo, e.event_type, e.ref "
            "FROM drafts d LEFT JOIN events e ON d.event_id = e.id "
            "WHERE d.created_at >= datetime('now', ?)"
        )
        params: list = [f"-{days} days"]

        if status:
            query += " AND d.status = ?"
            params.append(status)
        if platform:
            query += " AND d.platform = ?"
            params.append(platform)

        query += " ORDER BY d.created_at DESC"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_last_seen_sha(db_path: str, repo: str) -> str | None:
    """Return the last-seen commit SHA for a repo, or None if never tracked."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT last_seen_sha FROM tracking WHERE repo = ?",
            (repo,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def update_last_seen_sha(db_path: str, repo: str, sha: str) -> None:
    """Upsert the last-seen commit SHA for a repo."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO tracking (repo, last_seen_sha, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(repo) DO UPDATE SET last_seen_sha = ?, updated_at = ?",
            (repo, sha, now, sha, now),
        )
        await db.commit()
