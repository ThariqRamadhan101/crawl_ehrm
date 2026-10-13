import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config
import db
from ehrm_client import EhrmApiError, EhrmAuthError, EhrmClient

MENU = """
=== EHRM Pegawai Crawler ===
1. Crawl pegawai with filter
2. Crawl by jabatan
3. Crawl by NIP
q. Quit
"""

REFERENCE_FILTERS = [
    ("status_pegawai", "Status Pegawai"),
    ("status_asal", "Status Asal"),
    ("status_akhir", "Status Akhir"),
]


def flatten_pegawai(pegawai):
    return {
        "id": pegawai.get("id", ""),
        "nip": pegawai.get("nip", ""),
        "nama": pegawai.get("nama", ""),
    }


def fetch_profile(client, employee, dataset):
    return client.get_pegawai_by_id(employee["id"])


def fetch_history(client, employee, dataset):
    all_items = []
    total_count = 0
    for items, page_total, _ in client.iter_history(
        dataset["path"],
        employee["id"],
    ):
        all_items.extend(items)
        total_count = max(total_count, page_total)
    return {dataset["list_key"]: all_items, "total_count": total_count}


def history_entry(key, label, path, list_key, column_base=None):
    return {
        "key": key,
        "label": f"{label} ({path})",
        "path": path,
        "list_key": list_key,
        "json_column": f"{column_base or key}_json",
        "fetch": fetch_history,
    }


DETAIL_DATASETS = [
    {
        "key": "profil",
        "label": "Profil lengkap (pegawai/get-by-id)",
        "json_column": "profile_json",
        "fetch": fetch_profile,
    },
    history_entry(
        "pendidikan",
        "Riwayat pendidikan",
        config.GET_PENDIDIKAN_PATH,
        "history_pendidikan",
    ),
    history_entry(
        "jabatan",
        "Riwayat jabatan",
        config.GET_JABATAN_PATH,
        "history_jabatan",
    ),
    history_entry(
        "pangkat",
        "Riwayat pangkat",
        config.GET_PANGKAT_PATH,
        "history_pangkat",
    ),
    history_entry(
        "gaji-pokok",
        "Riwayat gaji pokok",
        config.GET_GAJI_POKOK_PATH,
        "history_gaji_pokok",
        column_base="gaji_pokok",
    ),
    history_entry(
        "ak",
        "Angka kredit",
        config.GET_AK_PATH,
        "angka_kredit",
    ),
    history_entry(
        "ortu",
        "Data orang tua",
        config.GET_ORTU_PATH,
        "history_ortu",
    ),
    history_entry(
        "keluarga",
        "Data pasangan/keluarga",
        config.GET_KELUARGA_PATH,
        "keluarga",
    ),
    history_entry(
        "anak",
        "Data anak",
        config.GET_ANAK_PATH,
        "anak",
    ),
    history_entry(
        "hukuman",
        "Hukuman disiplin",
        config.GET_HUKUMAN_PATH,
        "history_hukuman",
    ),
    history_entry(
        "skp",
        "Riwayat SKP",
        config.GET_SKP_PATH,
        "history_skp",
    ),
]


def store_search_row(conn, row):
    flat = flatten_pegawai(row)
    db.upsert_search(conn, flat, row)
    return flat


def crawl(conn, client, params=None, scope_label=None):
    params = dict(params or {})
    employees = []
    started = time.time()
    scope = scope_label or (
        params.get("nip")
        or params.get("nama")
        or params.get("jabatan")
        or "all"
    )
    try:
        rows, pagination = client.search_pegawai_page(on_page=1, **params)
        try:
            total_pages = int(pagination.get("total_pages") or 1)
        except (TypeError, ValueError):
            total_pages = 1
        if rows:
            db.save_raw(
                conn, "search", f"{scope}:page1", {"list_pegawai": rows}
            )
        for row in rows:
            employees.append(store_search_row(conn, row))
        print(f"  Page 1/{total_pages} fetched ({len(employees)} records)")

        remaining = list(range(2, total_pages + 1))
        if remaining:
            executor = ThreadPoolExecutor(
                max_workers=min(config.MAX_WORKERS, len(remaining))
            )
            futures = {
                executor.submit(
                    client.search_pegawai_page, on_page=on_page, **params
                ): on_page
                for on_page in remaining
            }
            try:
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        page_rows, _ = future.result()
                    except Exception as exc:
                        print(f"  Page {page}/{total_pages} FAILED: {exc}")
                        continue
                    if page_rows:
                        db.save_raw(
                            conn,
                            "search",
                            f"{scope}:page{page}",
                            {"list_pegawai": page_rows},
                        )
                    for row in page_rows:
                        employees.append(store_search_row(conn, row))
                    print(
                        f"  Page {page}/{total_pages} fetched "
                        f"(+{len(page_rows)} records)"
                    )
            except KeyboardInterrupt:
                print("\n  Interrupted - keeping pages saved so far.")
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\n  Interrupted - keeping pages saved so far.")
    elapsed = time.time() - started
    return employees, elapsed


def store_dataset_result(conn, employee, dataset, payload):
    db.save_raw(conn, dataset["key"], employee["id"], payload)
    db.set_dataset_json(conn, employee["id"], dataset["json_column"], payload)
    list_key = dataset.get("list_key")
    return len(payload[list_key]) if list_key else 1


def fetch_details_concurrent(conn, client, employees, chosen_datasets):
    tasks = [
        (employee, dataset)
        for employee in employees
        for dataset in chosen_datasets
    ]
    total = len(tasks)
    collected = {dataset["key"]: 0 for dataset in chosen_datasets}
    failed = {dataset["key"]: 0 for dataset in chosen_datasets}
    started = time.time()
    done = 0
    executor = ThreadPoolExecutor(max_workers=config.MAX_WORKERS)
    interrupted = False
    try:
        futures = {
            executor.submit(dataset["fetch"], client, employee, dataset): (
                employee,
                dataset,
            )
            for employee, dataset in tasks
        }
        for future in as_completed(futures):
            employee, dataset = futures[future]
            label = f"nip={employee.get('nip', '')}"
            done += 1
            try:
                payload = future.result()
            except Exception as exc:
                failed[dataset["key"]] += 1
                print(f"  [{done}/{total}] {label} {dataset['key']} FAILED: {exc}")
                continue
            collected[dataset["key"]] += store_dataset_result(
                conn, employee, dataset, payload
            )
            print(f"  [{done}/{total}] {label} {dataset['key']} ok")
    except KeyboardInterrupt:
        interrupted = True
        print("\n  Interrupted - keeping data saved so far.")
    finally:
        executor.shutdown(wait=not interrupted, cancel_futures=interrupted)
    elapsed = time.time() - started
    return collected, failed, elapsed


def load_nips_from_file(path="nip.txt"):
    file_path = Path(path)
    if not file_path.exists():
        print(f"  File not found: {file_path}")
        return []
    nips = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        nip = line.strip()
        if nip:
            nips.append(nip)
    return nips


def ask_nonempty(prompt):
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  Input cannot be empty.")


def ask_status_filter(client, query, label):
    items = []
    try:
        items = client.get_reference(query)
    except (EhrmAuthError, EhrmApiError) as exc:
        print(f"  (cannot load '{query}' reference: {exc})")
        return ""
    print(f"\n{label} options:")
    for index, (code, name) in enumerate(items, start=1):
        print(f"  {index}. [{code}] {name}")
    print("  (Enter to skip / no filter)")
    while True:
        raw = input(
            f"Select {label} (comma-separated numbers, e.g. 1,2): "
        ).strip()
        if not raw:
            return ""
        codes = []
        valid = True
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                number = int(token)
            except ValueError:
                valid = False
                break
            if not (1 <= number <= len(items)):
                valid = False
                break
            codes.append(str(items[number - 1][0]))
        if not valid or not codes:
            print("  Invalid input.")
            continue
        return ",".join(codes)


def ask_search_filters(client):
    params = {"nip": "", "nama": "", "jabatan": ""}
    for prompt, key in (("NIP", "nip"), ("Nama", "nama"), ("Jabatan", "jabatan")):
        value = input(f"{prompt} (optional, Enter to skip): ").strip()
        params[key] = value
    for query, label in REFERENCE_FILTERS:
        params[query] = ask_status_filter(client, query, label)
    return params


def ask_detail_selection():
    print("\nDetail datasets to fetch per pegawai (applied right after the crawl):")
    for index, dataset in enumerate(DETAIL_DATASETS, start=1):
        print(f"  {index}. {dataset['label']}")
    print("  0. Skip")
    valid_options = set(range(1, len(DETAIL_DATASETS) + 1)) | {0}
    while True:
        raw = input("Select (e.g. 1 or 1,2,...; 0 to skip): ").strip()
        if raw in ("", "0"):
            return []
        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        try:
            numbers = {int(token) for token in tokens}
        except ValueError:
            print("  Invalid input.")
            continue
        if not numbers or not numbers <= valid_options:
            print("  Invalid option.")
            continue
        return [DETAIL_DATASETS[n - 1] for n in sorted(numbers)]


def run_menu_choice(choice, conn, client):
    if choice == "1":
        print("[1] Crawling pegawai with filter ...")
        params = ask_search_filters(client)
        print(f"  Filter: {params}")
        selection = ask_detail_selection()
        employees, elapsed = crawl(conn, client, params, scope_label="filter")
        return employees, selection, elapsed
    if choice == "2":
        jabatan = ask_nonempty("Enter jabatan: ")
        print(f"[2] Crawling pegawai with jabatan '{jabatan}' ...")
        selection = ask_detail_selection()
        employees, elapsed = crawl(conn, client, {"jabatan": jabatan})
        return employees, selection, elapsed
    if choice == "3":
        print("  1. Input NIP manually")
        print("  2. Read NIPs from nip.txt")
        source = input("Select source: ").strip()
        selection = ask_detail_selection()
        if source == "2":
            nips = load_nips_from_file()
            if not nips:
                print("  No NIPs loaded.")
                return [], selection, 0.0
            print(f"  Loaded {len(nips)} NIP(s) from nip.txt")
            employees = []
            total_elapsed = 0.0
            for i, nip in enumerate(nips, 1):
                print(f"\n  [{i}/{len(nips)}] Crawling NIP {nip} ...")
                emp, elapsed = crawl(conn, client, {"nip": nip})
                employees.extend(emp)
                total_elapsed += elapsed
            return employees, selection, total_elapsed
        else:
            nip = ask_nonempty("Enter NIP: ")
            print(f"[3] Crawling pegawai with NIP '{nip}' ...")
            employees, elapsed = crawl(conn, client, {"nip": nip})
            return employees, selection, elapsed
    return None, [], 0.0


def main():
    conn = db.connect()
    for dataset in DETAIL_DATASETS:
        db.ensure_json_column(conn, dataset["json_column"])
    client = EhrmClient()
    while True:
        print(MENU)
        choice = input("Select option: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            counts = db.table_counts(conn)
            filled = db.filled_counts(
                conn, [d["json_column"] for d in DETAIL_DATASETS]
            )
            print(
                f"DB totals: pegawai={counts[db.TABLE]}, "
                f"raw_response={counts['raw_response']}, "
                + ", ".join(f"{col}={n}" for col, n in filled.items())
            )
            print("Bye.")
            break
        if choice not in ("1", "2", "3"):
            print("Invalid option, choose 1, 2, 3 or q.")
            continue
        try:
            employees, selection, elapsed = run_menu_choice(choice, conn, client)
        except EhrmAuthError as exc:
            print(f"AUTH ERROR: {exc}")
            continue
        except EhrmApiError as exc:
            print(f"API ERROR: {exc}")
            continue
        if not employees:
            print("No records found.")
            continue
        print(f"\nSaved {len(employees)} pegawai records to database ({elapsed:.1f}s)")
        if not selection:
            continue
        print(
            f"\nFetching {len(selection)} dataset(s) for {len(employees)} pegawai "
            f"with up to {config.MAX_WORKERS} concurrent workers ..."
        )
        collected, failed, elapsed_details = fetch_details_concurrent(
            conn, client, employees, selection
        )
        print(f"\nDetail fetching done in {elapsed_details:.1f}s")
        for dataset in selection:
            key = dataset["key"]
            message = f"{key}: {collected[key]} records saved"
            if failed[key]:
                message += f" (failed for {failed[key]} pegawai)"
            print(message)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
