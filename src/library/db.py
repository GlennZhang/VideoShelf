"""SQLite storage for the video library.

Design (design.md D2/D3):
- content rows are keyed by full-file SHA-256; file rows keep paths and link
  to content, so one content can have many file locations;
- ``videos`` holds paired ``ai_*`` / ``user_*`` columns — analysis writes only
  ``ai_*``, user edits write only ``user_*``; reads prefer ``user_*``;
- FTS5 contentless index with trigram tokenizer when available, LIKE fallback
  otherwise (D4);
- versioned migrations applied on connect, tracked in ``meta``.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

PENDING_VIDEO_SQL = (
    "(v.analysis_status!='done' OR EXISTS (SELECT 1 FROM video_people review"
    " WHERE review.content_id=v.content_id AND review.confirmed_status='pending'))"
)

# Shared by sidebar counts and artist-filtered lists (aliases: vp, v).
ARTIST_MEMBERSHIP_SQL = (
    "(vp.role='author' OR (vp.role='featured_painter' AND"
    " COALESCE(v.user_category,v.ai_category) IN ('绘画','书法')))"
    " AND (vp.source='user' OR NOT EXISTS ("
    " SELECT 1 FROM video_people manual JOIN people mp ON mp.id=manual.person_id"
    " WHERE manual.content_id=vp.content_id AND manual.source='user'"
    " AND mp.kind='painter'))"
)

MIGRATIONS: Dict[int, str] = {
    1: """
CREATE TABLE folders (    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_scan_at TEXT,
    last_scan_result TEXT
);

CREATE TABLE contents (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    duration_s REAL,
    width INTEGER,
    height INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    container TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    transcript_status TEXT NOT NULL DEFAULT 'none'
);

CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES contents(id),
    path TEXT NOT NULL UNIQUE,
    folder_id INTEGER REFERENCES folders(id),
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'available'
);
CREATE INDEX idx_files_content ON files(content_id);

CREATE TABLE videos (
    content_id INTEGER PRIMARY KEY REFERENCES contents(id),
    ai_title TEXT,
    user_title TEXT,
    ai_summary TEXT,
    user_summary TEXT,
    ai_category TEXT,
    user_category TEXT,
    ai_topic TEXT,
    user_topic TEXT,
    ai_form TEXT,
    user_form TEXT,
    ai_tags TEXT,
    user_tags TEXT,
    user_notes TEXT,
    user_favorite INTEGER NOT NULL DEFAULT 0,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT,
    analysis_run_at TEXT,
    pipeline_version TEXT,
    ai_evidence TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE transcripts (
    content_id INTEGER NOT NULL REFERENCES contents(id),
    idx INTEGER NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (content_id, idx)
);

CREATE TABLE screenshots (
    id INTEGER PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES contents(id),
    path TEXT NOT NULL,
    timestamp_s REAL NOT NULL,
    is_representative INTEGER NOT NULL DEFAULT 0,
    chosen_by TEXT NOT NULL DEFAULT 'ai'
);
CREATE INDEX idx_screenshots_content ON screenshots(content_id);

CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('painter', 'source_account')),
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    confirmed_status TEXT NOT NULL DEFAULT 'pending',
    evidence_json TEXT
);

CREATE TABLE video_people (
    content_id INTEGER NOT NULL REFERENCES contents(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    role TEXT NOT NULL CHECK (role IN
        ('author', 'presenter', 'featured_painter', 'reposting_account')),
    confirmed_status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'ai',
    PRIMARY KEY (content_id, person_id, role)
);

CREATE TABLE collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE collection_items (
    collection_id INTEGER NOT NULL REFERENCES collections(id),
    content_id INTEGER NOT NULL REFERENCES contents(id),
    PRIMARY KEY (collection_id, content_id)
);

CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0.0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

"""
}

#: Fields with ai_/user_ column pairs; effective value = user if set else ai.
DUAL_FIELDS = ("title", "summary", "category", "topic", "form", "tags")


def _detect_trigram(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE _fts_probe USING fts5(x, tokenize='trigram')"
        )
        conn.execute("DROP TABLE _fts_probe")
        conn.commit()
        return True
    except sqlite3.OperationalError:
        conn.rollback()
        return False


def _fts_init(conn: sqlite3.Connection) -> None:
    # A self-content FTS5 table (rowid = content_id) avoids the value-matching
    # delete semantics of contentless tables while keeping trigram MATCH.
    tokenizer = "trigram" if _detect_trigram(conn) else "unicode61"
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5("
        f"text, content_id UNINDEXED, tokenize='{tokenizer}')"
    )
    mode = "trigram" if tokenizer == "trigram" else "like"
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('fts_mode', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=?",
        (mode, mode),
    )
    conn.commit()


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (and migrate) the library database.

    Returns a connection with WAL enabled, foreign keys on and check_same_thread
    disabled — access is serialized through the caller's lock (see Library).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    _fts_init(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations based on ``meta.schema_version``."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    for version in sorted(MIGRATIONS):
        if version > current:
            conn.executescript(MIGRATIONS[version])
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=?",
                (str(version), str(version)),
            )
    conn.commit()


class Library:
    """Thread-safe façade over the library database.

    One instance is shared by the web app and the background worker; every
    public method takes the lock because SQLite connections are not safe for
    concurrent use from multiple threads.
    """

    def __init__(self, db_path: Path | str):
        self._conn = connect(db_path)
        self._lock = threading.RLock()
        self._fts_mode: str = self._conn.execute(
            "SELECT value FROM meta WHERE key='fts_mode'"
        ).fetchone()["value"]

    @property
    def fts_mode(self) -> str:
        return self._fts_mode

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- folders ---------------------------------------------------------

    def add_folder(self, path: str) -> int:
        folder = str(Path(path).expanduser().resolve())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO folders(path) VALUES (?) " "ON CONFLICT(path) DO NOTHING",
                (folder,),
            )
            if cur.lastrowid and cur.rowcount:
                return int(cur.lastrowid)
            row = self._conn.execute(
                "SELECT id FROM folders WHERE path=?", (folder,)
            ).fetchone()
            return int(row["id"])

    def list_folders(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM folders ORDER BY path").fetchall()
            return [dict(r) for r in rows]

    def remove_folder(self, folder_id: int) -> None:
        """Remove a folder registration; files under it become folder-less."""
        with self._lock:
            self._conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
            self._conn.commit()

    def record_scan(self, folder_id: int, result: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE folders SET last_scan_at=datetime('now'), "
                "last_scan_result=? WHERE id=?",
                (result, folder_id),
            )
            self._conn.commit()

    # -- contents / files --------------------------------------------------

    def find_file_by_path(self, path: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM files WHERE path=?", (str(path),)
            ).fetchone()

    def find_content_by_hash(self, sha256: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM contents WHERE sha256=?", (sha256,)
            ).fetchone()

    def upsert_content(self, sha256: str, size_bytes: int) -> int:
        """Create content row if absent; returns its id.

        A videos row is created alongside so paired ai_/user_ columns exist
        from the start.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM contents WHERE sha256=?", (sha256,)
            ).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO contents(sha256, size_bytes) VALUES (?, ?)",
                (sha256, size_bytes),
            )
            content_id = int(cur.lastrowid)
            self._conn.execute(
                "INSERT INTO videos(content_id) VALUES (?)", (content_id,)
            )
            return content_id

    def upsert_file(
        self,
        content_id: int,
        path: str,
        folder_id: Optional[int],
        size_bytes: int,
        mtime_ns: int,
    ) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO files(content_id, path, folder_id, size_bytes,"
                " mtime_ns) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content_id=excluded.content_id,"
                " folder_id=excluded.folder_id, size_bytes=excluded.size_bytes,"
                " mtime_ns=excluded.mtime_ns, status='available'",
                (content_id, str(path), folder_id, size_bytes, mtime_ns),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM files WHERE path=?", (str(path),)
            ).fetchone()
            return int(row["id"])

    def set_media_info(
        self,
        content_id: int,
        duration_s: float,
        width: int,
        height: int,
        video_codec: str,
        audio_codec: Optional[str],
        container: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE contents SET duration_s=?, width=?, height=?,"
                " video_codec=?, audio_codec=?, container=? WHERE id=?",
                (
                    duration_s,
                    width,
                    height,
                    video_codec,
                    audio_codec,
                    container,
                    content_id,
                ),
            )
            self._conn.commit()

    def mark_missing_files(
        self, seen_paths: Sequence[str], folder_id: Optional[int] = None
    ) -> int:
        """Mark files not in ``seen_paths`` as missing; returns count.

        When ``folder_id`` is given, only that folder's files are checked so
        a per-folder rescan cannot flag other folders' files. Never deletes
        rows — manual data survives (spec §3.1).
        """
        with self._lock:
            if folder_id is None:
                rows = self._conn.execute(
                    "SELECT id, path, status FROM files"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, path, status FROM files WHERE folder_id=?",
                    (folder_id,),
                ).fetchall()
            seen = set(seen_paths)
            missing = [
                r["id"]
                for r in rows
                if r["path"] not in seen and r["status"] == "available"
            ]
            for file_id in missing:
                self._conn.execute(
                    "UPDATE files SET status='missing' WHERE id=?", (file_id,)
                )
            # paths seen again were previously missing → back to available
            for r in rows:
                if r["path"] in seen and r["status"] == "missing":
                    self._conn.execute(
                        "UPDATE files SET status='available' WHERE id=?",
                        (r["id"],),
                    )
            self._conn.commit()
            return len(missing)

    def list_file_paths(self) -> List[str]:
        with self._lock:
            return [
                r["path"]
                for r in self._conn.execute("SELECT path FROM files").fetchall()
            ]

    # -- videos -----------------------------------------------------------

    def get_video(self, content_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT v.*, c.sha256, c.duration_s, c.transcript_status,"
                " c.size_bytes AS content_size, c.container"
                " FROM videos v JOIN contents c ON c.id=v.content_id"
                " WHERE v.content_id=?",
                (content_id,),
            ).fetchone()
            if row is None:
                return None
            video = dict(row)
            for f in DUAL_FIELDS:
                video[f] = video.get(f"user_{f}") or video.get(f"ai_{f}")
            video["files"] = self._video_files(content_id)
            return video

    def _video_files(self, content_id: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, path, status, size_bytes, mtime_ns FROM files"
            " WHERE content_id=? ORDER BY path",
            (content_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _video_filters(
        self,
        category: Optional[str] = None,
        artist_id: Optional[int] = None,
        form: Optional[str] = None,
        folder_id: Optional[int] = None,
        favorite: Optional[bool] = None,
        collection_id: Optional[int] = None,
        pending: bool = False,
        unknown_artist: bool = False,
    ) -> Tuple[List[str], List[Any]]:
        where, params = ["1=1"], []
        if pending:
            where.append(PENDING_VIDEO_SQL)
        if unknown_artist:
            where.append(
                "NOT EXISTS (SELECT 1 FROM video_people vp"
                " JOIN people p ON p.id=vp.person_id"
                " WHERE vp.content_id=v.content_id AND p.kind='painter' AND "
                + ARTIST_MEMBERSHIP_SQL + ")"
            )
        if category is not None:
            where.append("COALESCE(v.user_category, v.ai_category)=?")
            params.append(category)
        if form is not None:
            where.append("COALESCE(v.user_form, v.ai_form)=?")
            params.append(form)
        if favorite is not None:
            where.append("v.user_favorite=?")
            params.append(1 if favorite else 0)
        if artist_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM video_people vp"
                " WHERE vp.content_id=v.content_id AND vp.person_id=? AND "
                + ARTIST_MEMBERSHIP_SQL + ")"
            )
            params.append(artist_id)
        if folder_id is not None:
            where.append(
                "v.content_id IN (SELECT content_id FROM files" " WHERE folder_id=?)"
            )
            params.append(folder_id)
        if collection_id is not None:
            where.append(
                "v.content_id IN (SELECT content_id FROM collection_items"
                " WHERE collection_id=?)"
            )
            params.append(collection_id)
        return where, params

    def count_videos(self, **filters: Any) -> int:
        where, params = self._video_filters(**filters)
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM videos v WHERE " + " AND ".join(where), params
            ).fetchone()[0]

    def list_videos(
        self,
        category: Optional[str] = None,
        artist_id: Optional[int] = None,
        form: Optional[str] = None,
        folder_id: Optional[int] = None,
        favorite: Optional[bool] = None,
        collection_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
        pending: bool = False,
        unknown_artist: bool = False,
    ) -> List[Dict[str, Any]]:
        """Filter before pagination; use the same predicate for counts."""
        where, params = self._video_filters(
            category, artist_id, form, folder_id, favorite, collection_id, pending, unknown_artist
        )
        sql = (
            "SELECT v.content_id FROM videos v WHERE "
            + " AND ".join(where)
            + " ORDER BY v.updated_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._lock:
            ids = [r["content_id"] for r in self._conn.execute(sql, params).fetchall()]
            return [v for i in ids if (v := self.get_video(i)) is not None]

    def update_user_fields(self, content_id: int, fields: Dict[str, Any]) -> None:
        """Write user_* columns only; never touches ai_* (spec §9)."""
        allowed_user = {
            "user_title",
            "user_summary",
            "user_category",
            "user_topic",
            "user_form",
            "user_tags",
            "user_notes",
        }
        allowed_plain = {"user_favorite"}
        updates, params = [], []
        for key, value in fields.items():
            if key in allowed_user and value is not None:
                updates.append(f"{key}=?")
                params.append(str(value))
            elif key in allowed_plain:
                updates.append(f"{key}=?")
                params.append(1 if value else 0)
            elif key in allowed_user and value is None:
                updates.append(f"{key}=NULL")
        if not updates:
            raise ValueError("no editable fields provided")
        updates.append("updated_at=datetime('now')")
        params.append(content_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE videos SET {', '.join(updates)} WHERE content_id=?",
                params,
            )
            self._conn.commit()
        self.reindex_content(content_id)

    def set_analysis_status(
        self, content_id: int, status: str, error: Optional[str] = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE videos SET analysis_status=?, analysis_error=?,"
                " updated_at=datetime('now') WHERE content_id=?",
                (status, error, content_id),
            )
            self._conn.commit()

    def write_ai_result(
        self, content_id: int, result: Dict[str, Any], pipeline_version: str
    ) -> None:
        """Persist AI analysis output; user_* columns are never written."""
        mapping = {
            "ai_title": result.get("title"),
            "ai_summary": result.get("summary"),
            "ai_category": result.get("category"),
            "ai_topic": result.get("topic"),
            "ai_form": result.get("form"),
            "ai_tags": json.dumps(result.get("tags") or [], ensure_ascii=False),
            "ai_evidence": json.dumps(result.get("evidence") or {}, ensure_ascii=False),
        }
        assignments = ", ".join(f"{k}=?" for k in mapping)
        with self._lock:
            self._conn.execute(
                f"UPDATE videos SET {assignments}, analysis_status='done',"
                " analysis_error=NULL, analysis_run_at=datetime('now'),"
                " pipeline_version=?, updated_at=datetime('now')"
                " WHERE content_id=?",
                (*mapping.values(), pipeline_version, content_id),
            )
            self._conn.commit()
        self.reindex_content(content_id)

    # -- transcripts / screenshots -----------------------------------------

    def replace_transcripts(
        self, content_id: int, segments: Sequence[Dict[str, Any]], status: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM transcripts WHERE content_id=?", (content_id,)
            )
            self._conn.executemany(
                "INSERT INTO transcripts(content_id, idx, start_s, end_s,"
                " text) VALUES (?, ?, ?, ?, ?)",
                [
                    (content_id, i, s["start_s"], s["end_s"], s["text"])
                    for i, s in enumerate(segments)
                ],
            )
            self._conn.execute(
                "UPDATE contents SET transcript_status=? WHERE id=?",
                (status, content_id),
            )
            self._conn.commit()
        self.reindex_content(content_id)

    def get_transcripts(self, content_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, start_s, end_s, text FROM transcripts"
                " WHERE content_id=? ORDER BY idx",
                (content_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_screenshot(
        self,
        content_id: int,
        path: str,
        timestamp_s: float,
        is_representative: bool = False,
        chosen_by: str = "ai",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO screenshots(content_id, path, timestamp_s,"
                " is_representative, chosen_by) VALUES (?, ?, ?, ?, ?)",
                (
                    content_id,
                    str(path),
                    timestamp_s,
                    1 if is_representative else 0,
                    chosen_by,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def replace_screenshots(
        self, content_id: int, items: Sequence[Dict[str, Any]]
    ) -> None:
        """AI-side replace (chosen_by='ai' rows only are removed)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM screenshots WHERE content_id=? AND chosen_by='ai'",
                (content_id,),
            )
            for it in items:
                self._conn.execute(
                    "INSERT INTO screenshots(content_id, path, timestamp_s,"
                    " is_representative, chosen_by) VALUES (?, ?, ?, ?, 'ai')",
                    (
                        content_id,
                        str(it["path"]),
                        it["timestamp_s"],
                        1 if it.get("is_representative") else 0,
                    ),
                )
            self._conn.commit()

    def get_screenshots(self, content_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM screenshots WHERE content_id=?" " ORDER BY timestamp_s",
                (content_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- people -------------------------------------------------------------

    def upsert_person(
        self,
        kind: str,
        name: str,
        aliases: Sequence[str] = (),
        confirmed_status: str = "pending",
        evidence: Any = None,
    ) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, aliases_json FROM people WHERE kind=? AND"
                " canonical_name=?",
                (kind, name),
            ).fetchone()
            if row:
                merged = sorted(set(json.loads(row["aliases_json"])) | set(aliases))
                self._conn.execute(
                    "UPDATE people SET aliases_json=? WHERE id=?",
                    (json.dumps(merged, ensure_ascii=False), row["id"]),
                )
                self._conn.commit()
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO people(kind, canonical_name, aliases_json,"
                " confirmed_status, evidence_json) VALUES (?, ?, ?, ?, ?)",
                (
                    kind,
                    name,
                    json.dumps(sorted(set(aliases)), ensure_ascii=False),
                    confirmed_status,
                    (
                        json.dumps(evidence, ensure_ascii=False)
                        if evidence is not None
                        else None
                    ),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def link_person(
        self,
        content_id: int,
        person_id: int,
        role: str,
        confirmed_status: str = "pending",
        source: str = "ai",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO video_people(content_id, person_id, role,"
                " confirmed_status, source) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(content_id, person_id, role) DO UPDATE SET"
                # an AI re-analysis must never clobber a user-made link
                " confirmed_status=CASE WHEN video_people.source='user'"
                " THEN video_people.confirmed_status"
                " ELSE excluded.confirmed_status END,"
                " source=CASE WHEN video_people.source='user' THEN 'user'"
                " ELSE excluded.source END",
                (content_id, person_id, role, confirmed_status, source),
            )
            self._conn.commit()

    def has_user_painter(self, content_id: int) -> bool:
        """True when the user explicitly set this video's painter."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM video_people vp JOIN people p ON"
                " p.id=vp.person_id WHERE vp.content_id=? AND"
                " p.kind='painter' AND vp.source='user'",
                (content_id,),
            ).fetchone()
            return row is not None

    def set_video_artist(self, content_id: int, person_id: int) -> None:
        """Replace ALL painter links of a video with one confirmed author.

        "Changing the painter" must not leave the old painter linked — the
        UI shows exactly one painter per video and groups by it.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM video_people WHERE content_id=? AND person_id IN"
                " (SELECT id FROM people WHERE kind='painter')",
                (content_id,),
            )
            self._conn.execute(
                "INSERT INTO video_people(content_id, person_id, role,"
                " confirmed_status, source) VALUES (?, ?, 'author',"
                " 'confirmed', 'user')",
                (content_id, person_id),
            )
            self._conn.commit()

    def replace_ai_painters(self, content_id: int, links: Sequence[Tuple[int, str]]) -> None:
        """Replace stale AI candidates, preserving any manual attribution."""
        with self._lock:
            if self.has_user_painter(content_id) or self._conn.execute(
                "SELECT 1 FROM video_people vp JOIN people p ON p.id=vp.person_id"
                " WHERE vp.content_id=? AND p.kind='painter'"
                " AND vp.confirmed_status='confirmed'", (content_id,)
            ).fetchone():
                return
            with self._conn:
                self._conn.execute(
                    "DELETE FROM video_people WHERE content_id=? AND source='ai'"
                    " AND person_id IN (SELECT id FROM people WHERE kind='painter')",
                    (content_id,),
                )
                self._conn.executemany(
                    "INSERT INTO video_people(content_id, person_id, role,"
                    " confirmed_status, source) VALUES (?, ?, ?, 'pending', 'ai')"
                    " ON CONFLICT(content_id, person_id, role) DO NOTHING",
                    [(content_id, person_id, role) for person_id, role in links],
                )

    def list_people(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            sql = (
                "SELECT people.*, (SELECT COUNT(DISTINCT vp.content_id)"
                " FROM video_people vp JOIN videos v ON v.content_id=vp.content_id"
                " WHERE vp.person_id=people.id AND (people.kind!='painter' OR ("
                + ARTIST_MEMBERSHIP_SQL + "))) AS video_count FROM people"
            )
            params: Tuple = ()
            if kind:
                sql += " WHERE kind=?"
                params = (kind,)
            rows = self._conn.execute(
                sql + " ORDER BY canonical_name", params
            ).fetchall()
            result = []
            for r in rows:
                person = dict(r)
                person["aliases"] = json.loads(r["aliases_json"])
                person["evidence"] = (
                    json.loads(r["evidence_json"]) if r["evidence_json"] else None
                )
                result.append(person)
            return result

    def get_person(self, person_id: int) -> Optional[Dict[str, Any]]:
        people = [p for p in self.list_people() if p["id"] == person_id]
        return people[0] if people else None

    def merge_people(self, keep_id: int, merge_id: int) -> None:
        """Merge ``merge_id`` into ``keep_id``: union aliases, move links."""
        with self._lock:
            keep = self._conn.execute(
                "SELECT * FROM people WHERE id=?", (keep_id,)
            ).fetchone()
            gone = self._conn.execute(
                "SELECT * FROM people WHERE id=?", (merge_id,)
            ).fetchone()
            if keep is None or gone is None:
                raise ValueError("person not found")
            merged = sorted(
                set(json.loads(keep["aliases_json"]))
                | set(json.loads(gone["aliases_json"]))
                | {gone["canonical_name"]}
            )
            self._conn.execute(
                "UPDATE people SET aliases_json=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), keep_id),
            )
            self._conn.execute(
                "UPDATE video_people SET person_id=? WHERE person_id=?",
                (keep_id, merge_id),
            )
            self._conn.execute(
                "DELETE FROM video_people WHERE person_id=? AND"
                " (content_id, role) IN (SELECT content_id, role FROM"
                " video_people WHERE person_id=?)",
                (merge_id, keep_id),
            )
            self._conn.execute("DELETE FROM people WHERE id=?", (merge_id,))
            self._conn.commit()

    def update_person(
        self,
        person_id: int,
        canonical_name: Optional[str] = None,
        aliases: Optional[Sequence[str]] = None,
        confirmed_status: Optional[str] = None,
    ) -> None:
        with self._lock:
            if canonical_name is not None:
                self._conn.execute(
                    "UPDATE people SET canonical_name=? WHERE id=?",
                    (canonical_name, person_id),
                )
            if aliases is not None:
                self._conn.execute(
                    "UPDATE people SET aliases_json=? WHERE id=?",
                    (json.dumps(sorted(set(aliases)), ensure_ascii=False), person_id),
                )
            if confirmed_status is not None:
                self._conn.execute(
                    "UPDATE people SET confirmed_status=? WHERE id=?",
                    (confirmed_status, person_id),
                )
            self._conn.commit()

    def confirm_video_person(self, content_id: int, person_id: int, role: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE video_people SET confirmed_status='confirmed'"
                " WHERE content_id=? AND person_id=? AND role=?",
                (content_id, person_id, role),
            )
            self._conn.commit()

    def video_people(self, content_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT vp.role, vp.confirmed_status, vp.source, p.*,"
                " (p.kind='painter' AND " + ARTIST_MEMBERSHIP_SQL + ") AS is_attribution"
                " FROM video_people vp JOIN people p ON p.id=vp.person_id"
                " JOIN videos v ON v.content_id=vp.content_id"
                " WHERE vp.content_id=?",
                (content_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["aliases"] = json.loads(d.pop("aliases_json"))
                d["evidence"] = (
                    json.loads(d["evidence_json"]) if d["evidence_json"] else None
                )
                d.pop("evidence_json", None)
                out.append(d)
            return out

    # -- collections ---------------------------------------------------------

    def create_collection(self, name: str) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO collections(name) VALUES (?)"
                " ON CONFLICT(name) DO NOTHING",
                (name,),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM collections WHERE name=?", (name,)
            ).fetchone()
            return int(row["id"])

    def list_collections(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, COUNT(ci.content_id) AS video_count FROM"
                " collections c LEFT JOIN collection_items ci ON"
                " ci.collection_id=c.id GROUP BY c.id ORDER BY c.name"
            ).fetchall()
            return [dict(r) for r in rows]

    def collection_add(self, collection_id: int, content_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO collection_items(collection_id, content_id)"
                " VALUES (?, ?) ON CONFLICT DO NOTHING",
                (collection_id, content_id),
            )
            self._conn.commit()

    def collection_remove(self, collection_id: int, content_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM collection_items WHERE collection_id=? AND"
                " content_id=?",
                (collection_id, content_id),
            )
            self._conn.commit()

    def delete_collection(self, collection_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM collection_items WHERE collection_id=?",
                (collection_id,),
            )
            self._conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
            self._conn.commit()

    # -- tasks ---------------------------------------------------------------

    def enqueue_task(self, type_: str, payload: Dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(type, payload_json) VALUES (?, ?)",
                (type_, json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def next_task(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE status IN ('queued','retry')"
                " ORDER BY CASE type WHEN 'scan' THEN 0 ELSE 1 END, id"
                " LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE tasks SET status='running', updated_at=datetime('now')"
                " WHERE id=?",
                (row["id"],),
            )
            self._conn.commit()
            task = dict(row)
            task["payload"] = json.loads(row["payload_json"])
            return task

    def update_task(
        self,
        task_id: int,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            sets, params = ["updated_at=datetime('now')"], []
            if status is not None:
                sets.append("status=?")
                params.append(status)
            if stage is not None:
                sets.append("stage=?")
                params.append(stage)
            if progress is not None:
                sets.append("progress=?")
                params.append(progress)
            if error is not None:
                sets.append("error=?")
                params.append(error)
            params.append(task_id)
            self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params)
            self._conn.commit()

    def list_tasks(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Active tasks first (running, then queued by age), history after."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY"
                " CASE status WHEN 'running' THEN 0 WHEN 'retry' THEN 1"
                "     WHEN 'queued' THEN 2 ELSE 3 END,"
                " CASE WHEN status IN ('running','retry','queued')"
                "     THEN id ELSE -id END"
                " LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["payload"] = json.loads(d.pop("payload_json"))
                out.append(d)
            return out

    def requeue_stale_running(self) -> None:
        """On startup, tasks left 'running' by a previous process re-queue."""
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='queued', updated_at=datetime('now')"
                " WHERE status='running'"
            )
            self._conn.commit()

    # -- FTS -------------------------------------------------------------------

    def _fts_text(self, content_id: int) -> str:
        row = self._raw_video(content_id)
        if row is None:
            return ""
        video = dict(row)
        parts: List[str] = []
        for f in DUAL_FIELDS:
            val = video.get(f"user_{f}") or video.get(f"ai_{f}")
            if val:
                parts.append(str(val))
        people = self.video_people(content_id)
        for p in people:
            parts.append(p["canonical_name"])
            parts.extend(p["aliases"])
        for seg in self.get_transcripts(content_id):
            parts.append(seg["text"])
        return "\n".join(parts)

    def _raw_video(self, content_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM videos WHERE content_id=?", (content_id,)
        ).fetchone()

    def reindex_content(self, content_id: int) -> None:
        text = self._fts_text(content_id)
        with self._lock:
            self._conn.execute("DELETE FROM library_fts WHERE rowid=?", (content_id,))
            if text:
                self._conn.execute(
                    "INSERT INTO library_fts(rowid, text, content_id)"
                    " VALUES (?, ?, ?)",
                    (content_id, text, content_id),
                )
            self._conn.commit()

    def rebuild_fts(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM library_fts")
            self._conn.commit()
            ids = [
                r["content_id"]
                for r in self._conn.execute("SELECT content_id FROM videos").fetchall()
            ]
        for cid in ids:
            self.reindex_content(cid)

    def search_videos(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Full-text search over title/artist+aliases/summary/tags/transcript."""
        query = query.strip()
        if not query:
            return []
        with self._lock:
            if self._fts_mode == "trigram" and len(query) >= 3:
                try:
                    rows = self._conn.execute(
                        "SELECT rowid FROM library_fts WHERE library_fts"
                        " MATCH ? LIMIT ?",
                        (f'"{query}"', limit),
                    ).fetchall()
                    ids = [r["rowid"] for r in rows]
                except sqlite3.OperationalError:
                    ids = self._like_search(query, limit)
            else:
                ids = self._like_search(query, limit)
        return [v for i in ids if (v := self.get_video(i)) is not None]

    def _like_search(self, query: str, limit: int) -> List[int]:
        rows = self._conn.execute(
            "SELECT rowid FROM library_fts WHERE text LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [r["rowid"] for r in rows]

    # -- meta -----------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key)"
                " DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()
