import threading
import time

import requests

import config


class EhrmApiError(Exception):
    pass


class EhrmAuthError(EhrmApiError):
    pass


class EhrmClient:
    def __init__(
        self,
        token=None,
        base_url=None,
        timeout=None,
        max_retries=None,
        backoff_factor=None,
        page_delay=None,
    ):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.timeout = timeout or config.TIMEOUT_SECONDS
        self.max_retries = max_retries or config.MAX_RETRIES
        self.backoff_factor = (
            config.BACKOFF_FACTOR if backoff_factor is None else backoff_factor
        )
        self.page_delay = (
            config.PAGE_DELAY_SECONDS if page_delay is None else page_delay
        )
        self.status_asal = config.STATUS_ASAL
        self.status_akhir = config.STATUS_AKHIR
        self._local = threading.local()
        self._headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,id;q=0.8",
            "authorization": token or config.TOKEN,
            "content-type": "application/json",
            "priority": "u=1, i",
            "referer": config.REFERER,
            "user-agent": config.USER_AGENT,
        }

    @property
    def session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers)
            self._local.session = session
        return session

    def _request_json(self, path, payload):
        return self._request("POST", path, json=payload)

    def _request_json_get(self, path, params):
        return self._request("GET", path, params=params)

    def _request(self, method, path, json=None, params=None):
        url = f"{self.base_url}{path}"
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method, url, json=json, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_backoff(attempt)
                continue
            if response.status_code in (401, 403):
                raise EhrmAuthError(
                    f"HTTP {response.status_code}: token invalid or expired. "
                    "Update TOKEN in config.py."
                )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = EhrmApiError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                self._sleep_backoff(attempt)
                continue
            if response.status_code >= 400:
                raise EhrmApiError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise EhrmApiError("Response is not valid JSON") from exc
        raise EhrmApiError(
            f"Request failed after {self.max_retries} attempts: {last_error}"
        )

    def _sleep_backoff(self, attempt):
        time.sleep(self.backoff_factor * (2 ** (attempt - 1)))

    def search_pegawai_page(
        self,
        nip="",
        nama="",
        jabatan="",
        status_pegawai=None,
        status_asal=None,
        status_akhir=None,
        on_page=1,
        page_size=None,
    ):
        payload = {
            "nip": nip or "",
            "nama": nama or "",
            "jabatan": jabatan or "",
            "status_pegawai": "" if status_pegawai is None else status_pegawai,
            "status_asal": self.status_asal if status_asal is None else status_asal,
            "status_akhir": self.status_akhir if status_akhir is None else status_akhir,
            "on_page": on_page,
            "page_size": page_size or config.SEARCH_PAGE_SIZE,
        }
        data = self._request_json(config.SEARCH_PEGAWAI_PATH, payload)
        rows = data.get("list_pegawai") or []
        pagination = data.get("pagination") or {}
        return rows, pagination

    def iter_pages(
        self,
        nip="",
        nama="",
        jabatan="",
        status_pegawai=None,
        status_asal=None,
        status_akhir=None,
    ):
        page = 1
        total_pages = 1
        while page <= total_pages:
            rows, pagination = self.search_pegawai_page(
                nip=nip,
                nama=nama,
                jabatan=jabatan,
                status_pegawai=status_pegawai,
                status_asal=status_asal,
                status_akhir=status_akhir,
                on_page=page,
            )
            try:
                total_pages = int(pagination.get("total_pages") or 1)
            except (TypeError, ValueError):
                total_pages = 1
            yield page, total_pages, rows
            if not rows:
                break
            page += 1
            if page <= total_pages and self.page_delay > 0:
                time.sleep(self.page_delay)

    def search_pegawai(
        self,
        nip="",
        nama="",
        jabatan="",
        status_pegawai=None,
        status_asal=None,
        status_akhir=None,
    ):
        for _, _, rows in self.iter_pages(
            nip=nip,
            nama=nama,
            jabatan=jabatan,
            status_pegawai=status_pegawai,
            status_asal=status_asal,
            status_akhir=status_akhir,
        ):
            for row in rows:
                yield row

    def get_reference(self, query):
        data = self._request_json_get(
            config.REFERENCE_PATH, params={"q": query}
        )
        return self._extract_ref_items(data)

    @staticmethod
    def _extract_ref_items(data):
        if not isinstance(data, dict):
            return []
        ref_list = data.get("refList")
        if not isinstance(ref_list, list):
            return []
        items = []
        for item in ref_list:
            if not isinstance(item, dict):
                continue
            kd_key = next((k for k in item if k.startswith("kd_")), None)
            nama_key = next((k for k in item if k.startswith("nama_")), None)
            if not kd_key or not nama_key:
                continue
            items.append((item[kd_key], item[nama_key]))
        return items

    def get_pegawai_by_id(self, id_pegawai):
        return self._request_json(
            config.GET_PEGAWAI_BY_ID_PATH, {"id_pegawai": int(id_pegawai)}
        )

    def get_history_page(
        self, path, id_pegawai, on_page=1, page_size=None, id_as_int=True
    ):
        payload = {
            "on_page": on_page,
            "page_size": page_size or config.HISTORY_PAGE_SIZE,
            "id_pegawai": int(id_pegawai) if id_as_int else str(id_pegawai),
        }
        data = self._request_json(path, payload)
        return self._extract_list(data)

    @staticmethod
    def _extract_list(data):
        if not isinstance(data, dict):
            return [], 0
        items = next((v for v in data.values() if isinstance(v, list)), [])
        try:
            total_count = int(data.get("total_count") or 0)
        except (TypeError, ValueError):
            total_count = 0
        return items, total_count

    def iter_history(self, path, id_pegawai, page_size=None, id_as_int=True):
        size = page_size or config.HISTORY_PAGE_SIZE
        on_page = 1
        while True:
            items, total_count = self.get_history_page(
                path,
                id_pegawai,
                on_page=on_page,
                page_size=size,
                id_as_int=id_as_int,
            )
            yield items, total_count, on_page
            if not items or total_count <= 0 or on_page * size >= total_count:
                break
            on_page += 1
            if self.page_delay > 0:
                time.sleep(self.page_delay)
