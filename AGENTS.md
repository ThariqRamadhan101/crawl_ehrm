# AGENTS.md — crawl_ehrm

## What this is

Python CLI that crawls employee data from EHRM (`ehrm.pu.go.id`) into a local SQLite database. Interactive menu, no tests, no lint, no CI.

## Run

```
python main.py
```

Requires: `pip install requests` (only dependency). Python 3.10+.

## Gotchas

- **JWT token expires.** Put it in `.env` as `EHRM_TOKEN` (gitignored). When 401/403, paste a fresh one from browser DevTools → request headers → `authorization` (raw, no `Bearer` prefix).
- **Pagination uses `on_page`, not `page`.** The EHRM API silently ignores a `page` key. This is already handled in `ehrm_client.py` — do not "fix" it.
- **All get endpoints look up by `id_pegawai`, passed as an integer.** `get_history_page()` / `iter_history()` default to `int` (not string) — a string `id_pegawai` causes HTTP 500 on `/api/jabatan/get`. Do not pass string IDs.
- **`ehrm.db` contains PII** (KTP/NPWP numbers, phone, addresses). Treat as confidential; never commit or share.
- **Database auto-migrates.** `_migrate_legacy()` drops old normalized tables on first run. New `*_json` columns are added via `ALTER TABLE` at startup — no manual migration needed.

## Architecture (4 files)

| File | Role |
|------|------|
| `config.py` | All constants: token, endpoints, delays, page sizes, DB path |
| `ehrm_client.py` | HTTP client: session (thread-local), auth, retry/backoff, pagination |
| `db.py` | SQLite schema, upserts, raw response archive, column auto-creation |
| `main.py` | CLI menu, `DETAIL_DATASETS` registry, concurrent orchestration |

## Conventions

- All API responses stored as-is in JSON columns (`search_json`, `profile_json`, `<name>_json`).
- Adding a new endpoint: add client method + `DETAIL_DATASETS` entry + `<name>_json` column. No other changes needed.
- Concurrency: bounded thread pool (`MAX_WORKERS=8`), each worker gets its own `requests.Session`. SQLite writes stay on main thread.
- `raw_response` table is an append-only audit log of every API call.
