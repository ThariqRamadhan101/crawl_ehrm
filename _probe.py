import requests
import config

headers = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9,id;q=0.8",
    "authorization": config.TOKEN,
    "content-type": "application/json",
    "priority": "u=1, i",
    "referer": config.REFERER,
    "user-agent": config.USER_AGENT,
}

url = f"{config.BASE_URL}{config.SEARCH_PEGAWAI_PATH}"
NIP = "198508212009121001"
ID_EMP = 63813

for label, payload in [
    ("by_nip", {"nip": NIP, "nama": "", "jabatan": "", "status_asal": config.STATUS_ASAL, "status_akhir": config.STATUS_AKHIR, "on_page": 1, "page_size": 25}),
    ("by_id", {"nip": "", "nama": "", "jabatan": "", "status_asal": config.STATUS_ASAL, "status_akhir": config.STATUS_AKHIR, "on_page": 1, "page_size": 25, "id_pegawai": ID_EMP}),
    ("by_string_id", {"nip": "", "nama": "", "jabatan": "", "status_asal": config.STATUS_ASAL, "status_akhir": config.STATUS_AKHIR, "on_page": 1, "page_size": 25, "id_pegawai": str(ID_EMP)}),
]:
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    if r.status_code == 200:
        data = r.json()
        lst = data.get("list_pegawai") or []
        print(f"{label}: {r.status_code} n={len(lst)}")
        if lst:
            print("  ", lst[0].get("id"), lst[0].get("id_pegawai"), lst[0].get("nip"), lst[0].get("nama"))
    else:
        print(f"{label}: {r.status_code} {r.text[:120]}")