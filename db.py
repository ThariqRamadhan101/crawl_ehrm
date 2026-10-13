import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

TABLE = "pegawai"
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY,
    nama TEXT,
    nip TEXT,
    search_json TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS raw_response (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ref_id TEXT,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_source_ref ON raw_response(source, ref_id);
CREATE INDEX IF NOT EXISTS idx_pegawai_nip ON {TABLE}(nip);
"""


def connect():
    conn = sqlite3.connect(Path(__file__).parent / config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_legacy(conn)
    conn.executescript(SCHEMA)
    return conn


def _migrate_legacy(conn):
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for legacy_table in ("profil", "pendidikan"):
        if legacy_table in existing:
            conn.execute(f"DROP TABLE {legacy_table}")
    if "pegawai" in existing:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        if "search_json" not in columns:
            conn.execute(f"DROP TABLE {TABLE}")
    conn.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_json_column(conn, column):
    if not IDENTIFIER_PATTERN.match(column) or not column.endswith("_json"):
        raise ValueError(f"Invalid dataset column name: {column!r}")
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} TEXT")
        conn.commit()


def upsert_search(conn, row, raw_record):
    conn.execute(
        f"""INSERT INTO {TABLE} (id, nama, nip, search_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nama = excluded.nama,
                nip = excluded.nip,
                search_json = excluded.search_json,
                updated_at = excluded.updated_at""",
        (
            row["id"],
            row["nama"],
            row["nip"],
            json.dumps(raw_record, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()


def set_dataset_json(conn, id_pegawai, column, payload):
    ensure_json_column(conn, column)
    cursor = conn.execute(
        f"UPDATE {TABLE} SET {column} = ?, updated_at = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), _now(), id_pegawai),
    )
    if cursor.rowcount == 0:
        raise ValueError(
            f"id_pegawai={id_pegawai} not found in '{TABLE}'; "
            "run a search crawl first"
        )
    conn.commit()


def save_raw(conn, source, ref_id, payload):
    conn.execute(
        "INSERT INTO raw_response (source, ref_id, fetched_at, payload) "
        "VALUES (?, ?, ?, ?)",
        (source, str(ref_id), _now(), json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()


def table_counts(conn):
    counts = {}
    for table in (TABLE, "raw_response"):
        counts[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
    return counts


def filled_counts(conn, dataset_columns):
    counts = {}
    for column in dataset_columns:
        counts[column] = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE {column} IS NOT NULL"
        ).fetchone()[0]
    return counts
