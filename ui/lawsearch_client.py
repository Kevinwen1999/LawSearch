"""HTTP calls to the LawSearch API, shared by the UI pages.

The API holds the embedding models, so the UI only talks to it over HTTP.
"""

import os

import httpx

API = os.environ.get("LAWSEARCH_API", "http://localhost:8000")
# Briefing a long decision can take minutes.
BRIEF_TIMEOUT = 1200


def error_detail(response: httpx.Response) -> str:
    if response.headers.get("content-type", "").startswith("application/json"):
        detail = response.json().get("detail", response.text[:300])
        return detail if isinstance(detail, str) else str(detail)[:300]
    return response.text[:300]


def get_json(path: str, *, params: dict | None = None, timeout: float = 30) -> dict | list | None:
    """The JSON body, or None on a 404."""
    response = httpx.get(f"{API}{path}", params=params, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def get_text(path: str, *, params: dict | None = None) -> str:
    response = httpx.get(f"{API}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.text


def post_json(path: str, payload: dict, empty: dict) -> dict:
    """POST a JSON body; on failure, `empty` with status "error" and a message."""
    try:
        response = httpx.post(f"{API}{path}", json=payload, timeout=300)
    except httpx.HTTPError as exc:
        return {**empty, "status": "error", "message": type(exc).__name__}
    if response.status_code != 200:
        return {**empty, "status": "error", "message": f"HTTP {response.status_code}"}
    return response.json()


def fetch_cached_brief(case_id: str) -> dict | None:
    return get_json(f"/cases/{case_id}/filac")


def generate_brief(case_id: str, *, force: bool = False) -> tuple[dict | None, str | None]:
    """(brief, error message)."""
    try:
        response = httpx.post(f"{API}/cases/{case_id}/filac", params={"force": force}, timeout=BRIEF_TIMEOUT)
    except httpx.HTTPError as exc:
        return None, f"Brief failed ({type(exc).__name__})"
    if response.status_code != 200:
        return None, error_detail(response)
    return response.json(), None


def brief_user_text(
    *, text: str | None = None, file: tuple[str, bytes] | None = None, input_kind: str | None = None,
) -> tuple[dict | None, str | None]:
    """POST /briefs with pasted text or an uploaded file: (response, error message)."""
    data = {"input_kind": input_kind} if input_kind else {}
    files = None
    if file is not None:
        files = {"file": file}
    else:
        data["text"] = text
    try:
        response = httpx.post(f"{API}/briefs", data=data, files=files, timeout=BRIEF_TIMEOUT)
    except httpx.HTTPError as exc:
        return None, f"Brief failed ({type(exc).__name__})"
    if response.status_code != 200:
        return None, error_detail(response)
    return response.json(), None
