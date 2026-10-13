import json

import ehrm_client

client = ehrm_client.EhrmClient()
items, total = client.get_history_page(
    ehrm_client.config.GET_SKP_PATH,
    63233,
    id_as_int=True,
)
print(f"total_count: {total}")
print(json.dumps(items, indent=2, ensure_ascii=False))
