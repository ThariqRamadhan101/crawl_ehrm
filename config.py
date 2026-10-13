import os
from pathlib import Path

_ENV_PATH = Path(__file__).with_name(".env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

TOKEN = os.getenv("EHRM_TOKEN", "")

BASE_URL = "https://ehrm.pu.go.id"

SEARCH_PEGAWAI_PATH = "/api/pegawai/direktori"

REFERENCE_PATH = "/api/reference"

GET_PEGAWAI_BY_ID_PATH = "/api/pegawai/get-by-id"

GET_PENDIDIKAN_PATH = "/api/pendidikan/get"

GET_JABATAN_PATH = "/api/jabatan/get"

GET_PANGKAT_PATH = "/api/pangkat/get"

GET_GAJI_POKOK_PATH = "/api/gaji-pokok/get"

GET_AK_PATH = "/api/ak/get"

GET_ORTU_PATH = "/api/ortu/get"

GET_KELUARGA_PATH = "/api/keluarga/get"

GET_ANAK_PATH = "/api/anak/get"

GET_HUKUMAN_PATH = "/api/hukuman/get"

GET_SKP_PATH = "/api/skp/get"

HISTORY_PAGE_SIZE = 25

SEARCH_PAGE_SIZE = 100

REFERER = "https://ehrm.pu.go.id/layanan-kepegawaian/daftar-pegawai"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

TIMEOUT_SECONDS = 90

MAX_WORKERS = 8

STATUS_ASAL = "1,5,6"
STATUS_AKHIR = "1"
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.0
PAGE_DELAY_SECONDS = 0.5

DB_PATH = "ehrm.db"
