from dotenv import load_dotenv
import base64
import json
import logging
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

load_dotenv()
logger = logging.getLogger(__name__)

JIRA_CACHE_TTL_SECONDS = 10
_jira_cache = {}
_jira_cache_lock = threading.Lock()

START_DATE_MAP = {
    "CART-43": "2025-10-31",
    "CART-38": "2025-10-15",
    "CART-34": "2025-09-16",
    "CART-32": "2025-08-22",
    "CART-31": "2025-08-19",
    "CART-25": "2025-07-16",
    "CART-23": "2025-07-15",
    "CART-19": "2025-07-11",
    "CART-17": "2025-07-11",
    "CART-14": "2025-07-11",
}

DONE_DATE_MAP = {
    "CART-60": "2026-06-26",
    "CART-43": "2025-12-29",
    "CART-38": "2025-12-29",
    "CART-34": "2025-10-24",
    "CART-32": "2025-11-19",
    "CART-31": "2025-09-17",
    "CART-25": "2025-11-21",
    "CART-23": "2025-12-29",
    "CART-19": "2025-10-24",
    "CART-17": "2025-12-29",
    "CART-14": "2025-12-29",
}


def get_all_issues(jql):

    cache_key = " ".join(jql.split())
    now = time.monotonic()
    with _jira_cache_lock:
        cached = _jira_cache.get(cache_key)
        if cached and now - cached[0] < JIRA_CACHE_TTL_SECONDS:
            return cached[1]

    JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
    JIRA_EMAIL = os.getenv("JIRA_EMAIL")
    JIRA_TOKEN = os.getenv("JIRA_TOKEN")

    if not all((JIRA_DOMAIN, JIRA_EMAIL, JIRA_TOKEN)):
        return []

    url = f"{JIRA_DOMAIN.rstrip('/')}/rest/api/3/search/jql"

    next_page_token = None
    max_results = 100
    all_issues = []

    while True:

        params = {
            "jql": jql.strip(),
            "maxResults": max_results,
            "fields": "status,created,updated,labels,summary,assignee,priority,duedate,resolutiondate,fixVersions,components,issuetype",
            "expand": "changelog",
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        query = urlencode(params, doseq=True)
        auth_token = base64.b64encode(
            f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            f"{url}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {auth_token}",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logger.warning("Jira 이슈 조회 실패: %s", error)

            with _jira_cache_lock:
                stale = _jira_cache.get(cache_key)
                fallback = stale[1] if stale else all_issues
                _jira_cache[cache_key] = (time.monotonic(), fallback)
            return fallback
        issues = data.get("issues", [])
        all_issues.extend(issues)

        next_page_token = data.get("nextPageToken")
        if data.get("isLast", not next_page_token) or not next_page_token:
            break

    with _jira_cache_lock:
        _jira_cache[cache_key] = (time.monotonic(), all_issues)
    return all_issues


def get_jira_status():

    jql = """
    project = CART
    AND issuetype not in (에픽, Meeting)
    """.strip()

    issues = get_all_issues(jql)

    result = {
        "개선 필요": 0,
        "개선 진행 중": 0,
        "검증 진행 중": 0,
        "배포 및 적용 예정": 0,
        "배포 및 적용 완료": 0,
        "개선 완료": 0,
        "After Job": 0,
    }

    for issue in issues:

        status = issue["fields"]["status"]["name"].strip()

        if status in result:
            result[status] += 1

    return result


def get_done_date(issue, product):

    key = issue["key"]

    if product == "cart_bp_pro" and key in DONE_DATE_MAP:
        return DONE_DATE_MAP[key]

    histories = issue.get("changelog", {}).get("histories", [])

    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "status" and item.get("toString") == "개선 완료":
                return h["created"][:10]

    return issue["fields"]["updated"][:10]


def get_deploy_date(issue):

    histories = issue.get("changelog", {}).get("histories", [])

    for h in histories:
        for item in h.get("items", []):

            if (
                item.get("field") == "status"
                and item.get("toString") == "배포 및 적용 완료"
            ):
                return h["created"][:10]

    return ""


def get_bp_issues():

    jql = """
    project = BP
    AND issuetype not in (에픽, Meeting)
    """.strip()

    return get_all_issues(jql)


def get_bp_status():

    issues = get_bp_issues()

    result = {
        "개선 필요": 0,
        "개선 진행 중": 0,
        "검증 진행 중": 0,
        "배포 및 적용 예정": 0,
        "배포 및 적용 완료": 0,
        "개선 완료": 0,
    }

    for issue in issues:
        status = issue["fields"]["status"]["name"].strip()

        if status in result:
            result[status] += 1

    return result