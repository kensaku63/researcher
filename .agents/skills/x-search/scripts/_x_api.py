"""X API v2 wrapper.

Calls X API (Recent Search / Counts / Post Lookup / User Lookup / Timelines)
and normalizes responses into the common item shape used by x-search.

Phase 3 of the initial implementation. Phase 2 only uses `diagnose` /
`recent_search` for the 401 detection. Other endpoints are wired here so
they can be enabled by `_normalize.py` once the agent has API access.

Secret is read from `X_BEARER_TOKEN`. We never log the token value.

Reference: x-research-methods.md L160-293, SPEC-script.md L759-781.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _unescape(text: Any) -> Any:
    # X returns post text HTML-escaped (&gt; &amp; &lt;); store it as written.
    return html.unescape(text) if isinstance(text, str) else text

try:
    import requests  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - keep script runnable without requests at import time
    requests = None  # type: ignore[assignment]


API_BASE = "https://api.x.com"
DEFAULT_TIMEOUT_SEC = 30
USER_AGENT = "x-research-expert/0.1"


@dataclass
class ApiError:
    code: str
    message: str
    recoverable: bool
    scope: str = "global"


@dataclass
class ApiCallResult:
    ok: bool
    data: Any = None
    error: Optional[ApiError] = None
    elapsed_ms: int = 0
    next_token: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _token() -> Optional[str]:
    return os.environ.get("X_BEARER_TOKEN")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def _request(method: str, path: str, params: Optional[Dict[str, Any]] = None, scope: str = "global") -> ApiCallResult:
    if requests is None:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_UNEXPECTED_ERROR",
                message="`requests` package is not installed. Add it to environment.yaml pip section.",
                recoverable=False,
                scope=scope,
            ),
        )
    if not _token():
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_TOKEN_MISSING",
                message="X_BEARER_TOKEN is not set. Configure it in the aachat env provider, run `aachat up`, then re-run the session.",
                recoverable=True,
                scope=scope,
            ),
        )
    url = f"{API_BASE}{path}"
    started = datetime.now(timezone.utc)
    try:
        r = requests.request(
            method=method,
            url=url,
            headers=_headers(),
            params=params or {},
            timeout=DEFAULT_TIMEOUT_SEC,
        )
    except Exception as exc:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_UNEXPECTED_ERROR",
                message=f"X API request failed: {type(exc).__name__}",
                recoverable=True,
                scope=scope,
            ),
        )
    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    if r.status_code == 401:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_UNAUTHORIZED",
                message="X API returned 401. Re-issue the Bearer Token in X Developer Portal, update the aachat env provider, run `aachat up`, then re-run.",
                recoverable=False,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
        )
    if r.status_code == 403:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_FORBIDDEN",
                message="X API returned 403. Likely access level / endpoint permission issue.",
                recoverable=False,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
        )
    if r.status_code == 429:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_RATE_LIMITED",
                message="X API returned 429. Wait until x-rate-limit-reset and retry.",
                recoverable=True,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
        )
    if r.status_code >= 500:
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code="API_UNEXPECTED_ERROR",
                message=f"X API returned {r.status_code}. Treat as transient.",
                recoverable=True,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
        )
    if r.status_code >= 400:
        # try to extract API error body
        try:
            body = r.json()
        except Exception:
            body = {}
        title = ""
        detail = ""
        per_error_messages: List[str] = []
        if isinstance(body, dict):
            title = body.get("title") or ""
            detail = body.get("detail") or ""
            errors_arr = body.get("errors")
            if isinstance(errors_arr, list):
                for e in errors_arr:
                    if isinstance(e, dict):
                        msg = e.get("message") or e.get("detail")
                        if msg:
                            per_error_messages.append(str(msg))
            combined = " ".join([title, detail, *per_error_messages]).lower()
            if "quota" in combined or "cap" in combined:
                code = "API_QUOTA_EXCEEDED"
                recoverable = False
            else:
                code = "API_UNEXPECTED_ERROR"
                recoverable = True
        else:
            code = "API_UNEXPECTED_ERROR"
            recoverable = True
        # Build a useful message: title is generic ("Invalid Request"); the
        # actionable detail is usually in errors[].message.
        primary = per_error_messages[0] if per_error_messages else (detail or title)
        message = f"X API returned {r.status_code}. {primary[:300] if primary else '<no detail>'}"
        return ApiCallResult(
            ok=False,
            error=ApiError(
                code=code,
                message=message,
                recoverable=recoverable,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
        )

    try:
        body = r.json()
    except Exception:
        body = None
    next_token = None
    if isinstance(body, dict):
        meta = body.get("meta") or {}
        if isinstance(meta, dict):
            next_token = meta.get("next_token")
    return ApiCallResult(ok=True, data=body, elapsed_ms=elapsed_ms, next_token=next_token)


# ----- Normalization -----


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_tweet(raw: Dict[str, Any], users_by_id: Dict[str, Dict[str, Any]], stage: str = "collection") -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    source_id = str(raw.get("id") or "")
    if not source_id:
        return {}
    author_id = str(raw.get("author_id") or "")
    user = users_by_id.get(author_id, {}) if author_id else {}
    handle = user.get("username")
    metrics = raw.get("public_metrics") or {}
    user_metrics = user.get("public_metrics") or {}
    return {
        "url": f"https://x.com/{handle}/status/{source_id}" if handle else None,
        "source_id": source_id,
        "author": {
            "name": user.get("name"),
            "handle": f"@{handle}" if handle else None,
            "url": f"https://x.com/{handle}" if handle else None,
            "source": raw.get("source"),
            "followers": _to_int(user_metrics.get("followers_count")),
            "verified": user.get("verified"),
            "quality": None,
        },
        "published_at": raw.get("created_at"),
        "text": _unescape(raw.get("text")),
        "metrics": {
            "likes": _to_int(metrics.get("like_count")),
            "reposts": _to_int(metrics.get("retweet_count")),
            "replies": _to_int(metrics.get("reply_count")),
            "quotes": _to_int(metrics.get("quote_count")),
            "views": _to_int(metrics.get("impression_count")),
        },
        "matched_terms": [],
        "why_selected": None,
        "provenance": {
            "tool": "x_api",
            "stage": stage,
            "fetched_at": _now(),
        },
        "limitations": [],
    }


def _build_users_index(includes: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not includes:
        return {}
    users = includes.get("users") or []
    return {str(u.get("id")): u for u in users if isinstance(u, dict) and u.get("id")}


# ----- Public API -----


def diagnose() -> Tuple[Dict[str, Any], List[ApiError]]:
    """Return X API credential status. Spends ~1 read (max_results=10 ping)."""
    errors: List[ApiError] = []
    status: Dict[str, Any] = {
        "available": False,
        "checked_at": _now(),
        "reason": None,
    }
    if not _token():
        status["reason"] = "missing_token"
        errors.append(ApiError(
            code="API_TOKEN_MISSING",
            message="X_BEARER_TOKEN is not set.",
            recoverable=True,
            scope="diagnose",
        ))
        return status, errors

    res = _request("GET", "/2/tweets/search/recent", params={
        "query": "hello",
        "max_results": 10,
    }, scope="diagnose")
    if not res.ok:
        if res.error:
            status["reason"] = res.error.code.lower()
            errors.append(res.error)
        return status, errors
    status["available"] = True
    return status, errors


def recent_search(query: str, max_results: int, start_time: Optional[str] = None,
                  end_time: Optional[str] = None, sort_order: Optional[str] = None,
                  next_token: Optional[str] = None) -> ApiCallResult:
    params: Dict[str, Any] = {
        "query": query,
        "max_results": max(10, min(int(max_results), 100)),
        "tweet.fields": "created_at,lang,public_metrics,source,author_id,conversation_id",
        "expansions": "author_id",
        "user.fields": "name,username,verified,public_metrics",
    }
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    if sort_order:
        params["sort_order"] = sort_order
    if next_token:
        params["next_token"] = next_token
    return _request("GET", "/2/tweets/search/recent", params=params, scope="collection")


def conversation_search(conversation_id: str, max_results: int = 100) -> ApiCallResult:
    return recent_search(
        query=f"conversation_id:{conversation_id}",
        max_results=max_results,
    )


def counts_recent(query: str, granularity: str = "hour",
                  start_time: Optional[str] = None,
                  end_time: Optional[str] = None) -> ApiCallResult:
    params: Dict[str, Any] = {
        "query": query,
        "granularity": granularity if granularity in {"minute", "hour", "day"} else "hour",
    }
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    return _request("GET", "/2/tweets/counts/recent", params=params, scope="counts")


def post_lookup(ids: List[str]) -> ApiCallResult:
    ids_clean = [i for i in (str(x).strip() for x in ids) if i]
    if not ids_clean:
        return ApiCallResult(ok=True, data={"data": []})
    params = {
        "ids": ",".join(ids_clean[:100]),
        "tweet.fields": "created_at,lang,public_metrics,source,author_id,conversation_id",
        "expansions": "author_id",
        "user.fields": "name,username,verified,public_metrics",
    }
    return _request("GET", "/2/tweets", params=params, scope="lookup")


def user_by_username(username: str) -> ApiCallResult:
    username = username.lstrip("@")
    params = {
        "user.fields": "name,username,verified,public_metrics,description,created_at",
    }
    return _request("GET", f"/2/users/by/username/{username}", params=params, scope="account")


def user_tweets(user_id: str, max_results: int = 50) -> ApiCallResult:
    params = {
        "max_results": max(5, min(int(max_results), 100)),
        "tweet.fields": "created_at,lang,public_metrics,source,author_id,conversation_id",
        "expansions": "author_id",
        "user.fields": "name,username,verified,public_metrics",
    }
    return _request("GET", f"/2/users/{user_id}/tweets", params=params, scope="account")


def user_mentions(user_id: str, max_results: int = 30) -> ApiCallResult:
    params = {
        "max_results": max(5, min(int(max_results), 100)),
        "tweet.fields": "created_at,lang,public_metrics,source,author_id,conversation_id",
        "expansions": "author_id",
        "user.fields": "name,username,verified,public_metrics",
    }
    return _request("GET", f"/2/users/{user_id}/mentions", params=params, scope="account")


def items_from_response(resp_data: Any, stage: str = "collection") -> List[Dict[str, Any]]:
    if not isinstance(resp_data, dict):
        return []
    tweets = resp_data.get("data") or []
    if isinstance(tweets, dict):
        tweets = [tweets]
    users_index = _build_users_index(resp_data.get("includes"))
    return [normalize_tweet(t, users_index, stage=stage) for t in tweets if isinstance(t, dict)]
