# PRD — EHRM Employee Data Crawler

## 1. Overview

A command-line Python tool that crawls employee (pegawai) data from the EHRM
system of Kementerian Pekerjaan Umum (`https://ehrm.pu.go.id`) via its internal
JSON API and stores the results in a local SQLite database.

The tool is designed as a reusable library (`ehrm_client.py`) plus an
interactive entry point (`main.py`), so additional EHRM endpoints can be added
later without changing the core architecture.

Crawling is **two-phase**: first the employee search API produces a list of
employees; then the user may select one or more **detail datasets** (profile,
education history, more later) which are fetched per employee using
`id_pegawai` from phase 1.

## 2. Goals

- Provide a single command to download EHRM employee data into a queryable local database.
- Support three crawl scopes: **all employees**, **a specific jabatan** (position),
  and **a specific NIP** (employee ID number).
- Handle pagination transparently so every matching record is retrieved.
- Be resilient: retry transient failures, fail clearly on auth problems.
- Be extensible for future EHRM APIs (other data sets beyond `pegawai/search`).

## 3. Non-Goals

- No GUI/web interface; CLI only.
- No server-grade database (PostgreSQL/MongoDB) — embedded SQLite is sufficient
  at this scale (~46k employees).
- No automatic SSO login / token refresh — the JWT is supplied by the operator
  in `config.py` (see §9 Limitations).

## 4. Users & Use Cases

| User | Use case |
|------|----------|
| HR/data analyst | Export the full employee directory for reporting |
| Unit admin | Export employees filtered by a position name (jabatan) |
| Operator | Look up one person quickly by NIP |

## 5. Functional Requirements

### FR-1 Crawl modes and flow

For every run the program first asks for the **scope**, then which **detail
datasets** to fetch (before any crawling starts), so the whole job runs
unattended once confirmed:

1. **All** — sends a body with `nip`, `nama`, `jabatan` left empty plus the
   configured status filters (`status_asal`, `status_akhir` from `config.py`,
   defaults `"1,5,6"` / `"1"`), then pages through every result.
   *Measured reality:* the directory holds ~46k employees; with
   `SEARCH_PAGE_SIZE = 100` that is ~460 pages, fetched 8-at-a-time.
2. **Specific jabatan** — fills only the `jabatan` field with user input
   (partial, case-insensitive match is performed server-side).
   *Measured:* `jenderal prasarana strategis` → 1,307 employees over 14 pages
   of 100, completed in ~48 s.
3. **Specific NIP** — fills only the `nip` field with user input.

### FR-2 API contract

- `POST https://ehrm.pu.go.id/api/pegawai/search`
- Headers: browser-like `accept`, `accept-language`, `authorization`
  (raw JWT, no `Bearer` prefix), `content-type: application/json`,
  `referer`, `user-agent`.
- Body (JSON): `{"nip": "...", "nama": "...", "jabatan": "...", "status_asal": "...", "status_akhir": "...", "on_page": int, "page_size": int}`
  (`status_asal`/`status_akhir` defaults configurable in `config.py`).
- **Paging quirk (discovered by testing):** the page number parameter is
  `on_page` — a `page` key is silently ignored and always yields page 1.
  `page_size` is honored (`config.SEARCH_PAGE_SIZE`, default 100).
- Response:
  ```json
  {
    "list_pegawai": [ { "...employee fields...": "", "jabatan": [ {} ] } ],
    "pagination": { "total_count": 1, "current_page": 1, "total_pages": 1, "limit_page": 100 }
  }
  ```

### FR-3 Pagination

The client reads `pagination.total_pages` and iterates all pages. History
endpoints page identically via `on_page`/`page_size`; those without
`total_count` are treated as single-page.

### FR-4 Database storage (SQLite, single-table document style)

All results are stored in a single embedded database file `ehrm.db`
(`config.DB_PATH`), created automatically on first run. CSV output has been
removed — the database is the single source of truth.

**`pegawai` table — one row per employee**, with one JSON column per API:

| Column | Content |
|--------|---------|
| `id` | employee id (`id_pegawai`, PK) |
| `nama` | employee name |
| `nip` | employee NIP |
| `search_json` | verbatim phase-1 search record incl. nested `jabatan[]` |
| `profile_json` | verbatim get-by-id response (filled by dataset 1) |
| `pendidikan_json` | verbatim pendidikan/get response (filled by dataset 2) |
| `<new>_json …` | one column per future API, added automatically |
| `updated_at` | last fetch timestamp |

- **Extensible by design:** each `DETAIL_DATASETS` entry declares its
  `<name>_json` column; `db.ensure_json_column()` creates missing columns via
  `ALTER TABLE` on startup, so new APIs need no schema migration.
- Query nested data directly:
  ```sql
  SELECT nama,
         json_extract(profile_json, '$.gol_pangkat') AS golongan,
         json_extract(pendidikan_json, '$.total_count') AS jml_pendidikan
  FROM pegawai;
  ```
- **Upserts:** re-crawling the same scope updates existing rows instead of
  duplicating them.
- **Raw archive:** every API response is *also* appended to the `raw_response`
  audit table (`source`, `ref_id`, verbatim `payload`) as a safety net.
- Inspect with any SQLite client (e.g. DB Browser for SQLite), or query from
  pandas: `pd.read_sql("SELECT ... FROM pegawai", sqlite3.connect("ehrm.db"))`.
- Legacy normalized tables from earlier versions are dropped automatically on
  first run of this version.

### FR-5 Error handling

- `401/403` → clear message that the token is invalid/expired and where to
  update it (`config.py`); no retry.
- `429/5xx` and network errors → retry with exponential backoff
  (default max 3 attempts).
- Other `4xx` → immediate failure with status + response excerpt.
- Malformed JSON → explicit error.
- Detail-phase failures are per-record: logged, counted, never abort the run.
- `Ctrl+C` during a crawl → stop cleanly, keep rows already collected,
  already committed to the database.

### FR-6 Extensibility

`EhrmClient` centralizes session/auth/retry/pagination helpers. New endpoints
are added as new methods on the client plus a new entry in `DETAIL_DATASETS`
(see FR-7); existing code paths are untouched.

### FR-7 Detail datasets (phase 2)

After a successful phase-1 crawl, the program lists the available detail
datasets from a registry (`DETAIL_DATASETS` in `main.py`) and lets the user
select any combination, or skip:

| # | Key | Endpoint | Body | Response shape |
|---|-----|----------|------|----------------|
| 1 | `profil` | `POST /api/pegawai/get-by-id` | `{"id_pegawai": int}` | flat object (~110 fields) with nested lookup arrays |
| 2 | `pendidikan` | `POST /api/pendidikan/get` | `{"on_page", "page_size", "id_pegawai"}` | `{history_pendidikan[], total_count}` |
| 3 | `jabatan` | `POST /api/jabatan/get` | same, **int** id required | `{current_jabatan{...}, history_jabatan[], total_count}` |
| 4 | `pangkat` | `POST /api/pangkat/get` | same | `{history_pangkat[], total_count}` |
| 5 | `gaji-pokok` | `POST /api/gaji-pokok/get` | same | `{history_gaji_pokok[], total_count, total_pages, limit_page}` |
| 6 | `ak` | `POST /api/ak/get` | same | `{angka_kredit[], total_count}` |
| 7 | `ortu` | `POST /api/ortu/get` | same | `{history_ortu[], total_count}` |
| 8 | `keluarga` | `POST /api/keluarga/get` | same | `{keluarga[]}` (no total_count) |
| 9 | `anak` | `POST /api/anak/get` | same | `{anak[]}` (no total_count) |

All history endpoints share one generic paginated client
(`get_history_page()` / `iter_history()`): it auto-detects the list key and
stops after page 1 when no `total_count` is present. `jabatan` is the only
endpoint requiring an integer `id_pegawai` (string causes HTTP 500) — handled
by the registry's `int_id` flag.

- Each dataset is fetched for every employee found in phase 1, keyed by
  `id_pegawai`, with a politeness delay between calls.
- A failure on one employee/dataset is reported and counted; the run continues.
- `Ctrl+C` stops detail fetching cleanly and keeps what was collected.

**Adding a new dataset** = add one client method + one `collect_*` function +
one `DETAIL_DATASETS` entry. No other code changes.

### FR-8 Detail datasets storage

Each selected dataset writes its verbatim response into the employee's JSON
column during the crawl (`UPDATE pegawai SET <column> = ? WHERE id = ?`),
reconstructed as `{<list_key>: [...], "total_count": n}`:

- dataset 1 → `profile_json`
- datasets 2–9 → their declared `<name>_json` columns
- dataset *n* → its declared `<name>_json`

Adding a future dataset = add one client method (only if the shape differs) +
one `DETAIL_DATASETS` entry via `history_entry(...)` (with its
`json_column`). The column and storage path come for free.

### FR-9 Concurrent execution

Both phases run on a bounded thread pool (`config.MAX_WORKERS`, default 8):

- **Phase 1:** page 1 determines `total_pages`; pages 2..N are fetched in
  parallel and upserted as they complete (completion order is non-deterministic,
  which is safe because rows are keyed by `id`).
- **Phase 2:** every (employee × dataset) pair is an independent task.
- **Thread safety:** each worker thread gets its own `requests.Session`
  (thread-local in `EhrmClient`); all SQLite access stays on the main thread —
  workers return payloads, the main loop writes them.
- **Upsert semantics:** search re-crawls use `ON CONFLICT DO UPDATE`, so
  refreshing employees never wipes already-fetched detail columns.
- **Ctrl+C:** cancels queued futures immediately; results already stored are kept.

## 6. Non-Functional Requirements

- **Reliability:** exponential-backoff retries; bounded concurrency instead of
  blind request floods; politeness delay inside sequential history paging.
- **Portability:** pure Python 3 + `requests`; runs on Windows PowerShell.
- **Security:** token lives in `config.py`, which must never be committed/shared;
  no secrets are written to logs or the database. Note that `profile_json` and
  `raw_response` contain sensitive PII (KTP/NPWP numbers, phone numbers,
  addresses) — treat `ehrm.db` as confidential.
- **Usability:** interactive menu with input validation and progress output
  (`page x/y, n records`, `[i/total] nip=...` per employee in phase 2).

## 7. Architecture

```
crawl_ehrm/
├── PRD.md           this document
├── requirements.txt dependencies (requests)
├── config.py        TOKEN, BASE_URL, endpoints, delays, DB_PATH
├── ehrm_client.py   library: EhrmClient (session, auth, retry, paging)
├── db.py            storage: schema, upserts, raw archive, counts
├── main.py          CLI menu, orchestration, dataset registry, flattening
└── ehrm.db          SQLite database (generated)
```

Module responsibilities:

- `config.py` — all tunables/constants in one place.
- `ehrm_client.py` — `EhrmApiError`, `EhrmAuthError`, `EhrmClient` with
  `search_page()`, `iter_pages()`, `search_pegawai()` (phase 1) and
  `get_pegawai_by_id()`, `get_pendidikan_page()`, `iter_pendidikan()`
  (phase 2).
- `db.py` — single-table SQLite schema + auto column creation
  (`ensure_json_column`), `upsert_search()`, `set_dataset_json()`,
  `save_raw()`, legacy-table migration, counts.
- `main.py` — menu loop, prompt/validate user input, `DETAIL_DATASETS`
  registry + `collect_*` functions, search-result flattening, orchestration,
  summary printing.

## 8. Acceptance Criteria

1. Running `python main.py` shows the 3-option menu (+ quit).
2. Mode 3 with NIP `198108112025212018` returns the sample employee and stores
   one `pegawai` row with `search_json` populated (incl. nested `jabatan[]`).
3. Mode 2 with `jenderal prasarana strategis` reproduces the documented search.
4. Mode 1 crawls all pages until `pagination.total_pages` is exhausted.
5. Each employee occupies exactly one row regardless of how many jabatan or
   education records they have.
6. Expired/invalid token produces a friendly auth-error message instead of a traceback.
7. Selecting all datasets fills every `<name>_json` column; each is queryable
   via `json_extract` without any JOIN (e.g.
   `json_extract(gaji_pokok_json, '$.history_gaji_pokok[0].gaji_pokok')`).
8. Selecting `0` at the dataset prompt skips phase 2 entirely.
9. A failure on one employee/dataset logs a FAILED line and the run continues.
10. Re-running the same crawl leaves row count unchanged (upsert);
    only `raw_response` grows.

## 9. Limitations & Future Work

- **Token expiry:** the hardcoded JWT has a limited lifetime (the captured one
  expires around Aug 2026). When expired, obtain a fresh one from a logged-in
  browser session (DevTools → request headers → `authorization`) and paste it
  into `config.TOKEN`. A future version could read the token from a file/env var.
- Future phases will crawl additional EHRM endpoints (per prompt.txt); add a
  client method + registry entry with a `<name>_json` column and they plug
  straight in.
- Viewing data: install [DB Browser for SQLite](https://sqlitebrowser.org/) or
  query from Python/pandas/Power BI (SQLite ODBC driver).
- Possible enhancements: resumable downloads, non-interactive CLI flags
  (`--nip`, `--jabatan`, `--all`), optional CSV/Excel export on demand.
