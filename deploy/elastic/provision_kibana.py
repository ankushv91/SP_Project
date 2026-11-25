import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

KIBANA_URL = os.environ.get("KIBANA_URL", "http://localhost:5601").rstrip("/")
ELASTICSEARCH_URL = os.environ.get(
    "ELASTICSEARCH_URL", "http://localhost:9200"
).rstrip("/")
KIBANA_OBJECTS_FILE = Path(
    os.environ.get("KIBANA_OBJECTS_FILE", "kibana-objects.ndjson")
)

DATA_VIEW_ID = "effc485e-5adb-48c2-9b28-50911efa0437"
SAVED_SEARCH_ID = "7e0b1667-c5fd-480f-bf39-fe56a0ef6af6"
DASHBOARD_ID = "3d04e39c-2e41-4290-ab00-111601c91eb1"
RULE_NAME = "API error logs"


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"API returned HTTP {status}: {body}")
        self.status = status


def api_request(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict[str, object] | bytes | None = None,
    content_type: str = "application/json",
) -> object:
    headers = {"Accept": "application/json", "kbn-xsrf": "true"}
    body = None
    if isinstance(payload, bytes):
        body = payload
        headers["Content-Type"] = content_type
    elif payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type

    request = Request(
        f"{base_url}{path}", data=body, headers=headers, method=method
    )
    try:
        with urlopen(request, timeout=15) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise ApiError(error.code, error_body) from error

    return json.loads(response_body) if response_body else {}


def wait_for_kibana() -> None:
    for _ in range(60):
        try:
            status = api_request(KIBANA_URL, "/api/status")
        except ApiError as error:
            if error.status not in {500, 502, 503}:
                raise
        except URLError:
            pass
        else:
            if status["status"]["overall"]["level"] == "available":
                print("Kibana is ready.")
                return
        time.sleep(2)
    raise TimeoutError("Kibana did not become available within 120 seconds.")


def wait_for_api_logs() -> None:
    for _ in range(60):
        try:
            indices = api_request(
                ELASTICSEARCH_URL, "/_cat/indices/filebeat-*?format=json"
            )
        except ApiError as error:
            if error.status != 404:
                raise
        except URLError:
            pass
        else:
            if indices:
                print("API log indices are available.")
                return
        time.sleep(2)
    raise TimeoutError(
        "No Filebeat indices appeared within 120 seconds. "
        "Check the API and Filebeat service logs."
    )


def saved_object_exists(object_type: str, object_id: str) -> bool:
    try:
        api_request(
            KIBANA_URL, f"/api/saved_objects/{object_type}/{object_id}"
        )
    except ApiError as error:
        if error.status == 404:
            return False
        raise
    return True


def import_kibana_objects() -> None:
    boundary = uuid4().hex
    file_content = KIBANA_OBJECTS_FILE.read_bytes()
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        'filename="kibana-objects.ndjson"\r\n'
        "Content-Type: application/ndjson\r\n\r\n"
    ).encode("utf-8")
    body += file_content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    response = api_request(
        KIBANA_URL,
        "/api/saved_objects/_import?overwrite=true",
        method="POST",
        payload=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    if response.get("success") is not True:
        raise RuntimeError(
            "Kibana saved-object import failed: "
            + json.dumps(response, sort_keys=True)
        )
    print("Imported the API logs data view and dashboard.")


def find_api_logs_data_view() -> str:
    response = api_request(KIBANA_URL, "/api/data_views")
    data_views = response.get("data_view", [])
    for data_view in data_views:
        if (
            data_view.get("name") == "API logs"
            and data_view.get("title") == "filebeat-*"
        ):
            return data_view["id"]
    raise RuntimeError(
        "The API logs data view was not found after importing Kibana objects."
    )


def find_api_error_rule() -> dict[str, object] | None:
    response = api_request(
        KIBANA_URL,
        "/api/alerting/rules/_find?per_page=100&search_fields=name"
        "&search=API%20error%20logs",
    )
    for rule in response.get("data", []):
        if (
            rule.get("name") == RULE_NAME
            and rule.get("rule_type_id") == ".es-query"
        ):
            return rule
    return None


def create_api_error_rule(data_view_id: str) -> None:
    payload = {
        "name": RULE_NAME,
        "tags": ["api", "logs"],
        "rule_type_id": ".es-query",
        "consumer": "stackAlerts",
        "schedule": {"interval": "1m"},
        "params": {
            "searchType": "searchSource",
            "searchConfiguration": {
                "index": data_view_id,
                "query": {
                    "language": "kuery",
                    "query": 'message : "ERROR"',
                },
                "filter": [],
            },
            "timeField": "@timestamp",
            "aggType": "count",
            "groupBy": "all",
            "thresholdComparator": ">",
            "threshold": [0],
            "timeWindowSize": 5,
            "timeWindowUnit": "m",
            "size": 100,
            "excludeHitsFromPreviousRun": False,
        },
        "actions": [],
    }
    response = api_request(
        KIBANA_URL, "/api/alerting/rule", method="POST", payload=payload
    )
    print(f"Created alert rule: {response['name']}.")


def main() -> None:
    wait_for_kibana()
    wait_for_api_logs()

    required_objects = (
        ("index-pattern", DATA_VIEW_ID),
        ("search", SAVED_SEARCH_ID),
        ("dashboard", DASHBOARD_ID),
    )
    if not all(
        saved_object_exists(object_type, object_id)
        for object_type, object_id in required_objects
    ):
        import_kibana_objects()
    else:
        print("API logs data view and dashboard already exist.")

    data_view_id = find_api_logs_data_view()
    if find_api_error_rule() is None:
        create_api_error_rule(data_view_id)
    else:
        print(f"Alert rule '{RULE_NAME}' already exists.")


if __name__ == "__main__":
    main()
