"""Bird CLI wrapper.

Calls `bird` (https://www.npmjs.com/package/@steipete/bird) and normalizes
its JSON output into the common item shape used by x-search.

Phase 2 (Initial implementation):
- diagnose: `bird check` and `bird whoami`
- search: `bird search <query> -n N --json-full`
- expand: `bird thread <id> --json-full` + `bird replies <id> --all --max-pages N --json-full`
- account: `bird user-tweets <handle> -n N --json-full` (+ optional about / mentions)
- lookup: `bird read <id> --json-full`
- trend: `bird news --with-tweets --json` + `bird trending --json`

Tweet commands use `--json-full` so normalize_tweet can lift views, bookmarks,
lang, source and author profile metrics from `_raw`; `_raw` is not kept.

Auth: AUTH_TOKEN / CT0 from the environment, or bird's own cookie store
(browser profile / ~/.config/bird) when `bird check` passes. We never log
secret values.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _unescape(text: Any) -> Any:
    # X returns post text HTML-escaped (&gt; &amp; &lt;); store it as written.
    return html.unescape(text) if isinstance(text, str) else text


BIRD_BIN = os.environ.get("BIRD_BIN", "bird")
DEFAULT_TIMEOUT_SEC = 60


@dataclass
class BirdError:
    code: str
    message: str
    recoverable: bool
    scope: str = "global"


@dataclass
class BirdCallResult:
    ok: bool
    data: Any = None
    error: Optional[BirdError] = None
    elapsed_ms: int = 0
    raw_stderr: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_installed() -> bool:
    return shutil.which(BIRD_BIN) is not None


def _has_env_auth() -> bool:
    return bool(os.environ.get("AUTH_TOKEN")) and bool(os.environ.get("CT0"))


_BROWSER_AUTH: Optional[bool] = None


def _has_browser_auth() -> bool:
    """bird can resolve cookies itself (browser profile / ~/.config/bird).

    `bird check` exits 0 and reports auth_token/ct0 when that works. Cached per
    process so each subcommand pays the check once.
    """
    global _BROWSER_AUTH
    if _BROWSER_AUTH is None:
        try:
            proc = subprocess.run([BIRD_BIN, "--plain", "check"], capture_output=True, text=True,
                                  timeout=20, check=False)
            out = (proc.stdout + proc.stderr).lower()
            _BROWSER_AUTH = proc.returncode == 0 and "auth_token" in out and "ct0" in out and "missing" not in out
        except Exception:
            _BROWSER_AUTH = False
    return _BROWSER_AUTH


def _has_auth() -> bool:
    return _has_env_auth() or (_is_installed() and _has_browser_auth())


def auth_source() -> Optional[str]:
    if _has_env_auth():
        return "env"
    if _is_installed() and _has_browser_auth():
        return "bird_cookie_store"
    return None


def _run(args: List[str], scope: str = "global", timeout: int = DEFAULT_TIMEOUT_SEC) -> BirdCallResult:
    """Run a bird subcommand and return BirdCallResult with structured error."""
    if not _is_installed():
        return BirdCallResult(
            ok=False,
            error=BirdError(
                code="BIRD_NOT_INSTALLED",
                message=f"`{BIRD_BIN}` is not installed or not on PATH. Install with `npm i -g @steipete/bird` and ensure PATH includes it.",
                recoverable=False,
                scope=scope,
            ),
        )
    if not _has_auth():
        return BirdCallResult(
            ok=False,
            error=BirdError(
                code="BIRD_AUTH_MISSING",
                message="AUTH_TOKEN / CT0 are not set and bird could not read browser cookies. Log in to x.com in Chrome, or configure AUTH_TOKEN / CT0 in the aachat env provider, then re-run.",
                recoverable=True,
                scope=scope,
            ),
        )
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            [BIRD_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return BirdCallResult(
            ok=False,
            error=BirdError(
                code="BIRD_UNEXPECTED_ERROR",
                message=f"bird timed out after {timeout}s for args={args[:2]}",
                recoverable=True,
                scope=scope,
            ),
        )
    except Exception as exc:
        return BirdCallResult(
            ok=False,
            error=BirdError(
                code="BIRD_UNEXPECTED_ERROR",
                message=f"bird invocation failed: {type(exc).__name__}",
                recoverable=False,
                scope=scope,
            ),
        )
    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()

    if proc.returncode != 0:
        lowered = stderr.lower()
        if "401" in lowered or "unauthorized" in lowered or "auth" in lowered and "expire" in lowered:
            err = BirdError(
                code="BIRD_AUTH_EXPIRED",
                message="bird returned an auth error. The Cookie (AUTH_TOKEN / CT0) is likely expired. Refresh the Cookie in the aachat env provider, run `aachat up`, then re-run.",
                recoverable=False,
                scope=scope,
            )
        elif "query id" in lowered or "queryid" in lowered or "query-id" in lowered:
            err = BirdError(
                code="BIRD_QUERY_ID_STALE",
                message="bird GraphQL query ID is stale. Run `bird query-ids --fresh` to refresh.",
                recoverable=True,
                scope=scope,
            )
        elif "rate" in lowered and ("limit" in lowered or "limited" in lowered):
            err = BirdError(
                code="BIRD_RATE_LIMITED",
                message="bird hit a rate limit. Back off for several minutes and retry.",
                recoverable=True,
                scope=scope,
            )
        else:
            err = BirdError(
                code="BIRD_UNEXPECTED_ERROR",
                message=f"bird exit={proc.returncode}. stderr head: {stderr[:200] if stderr else '<empty>'}",
                recoverable=True,
                scope=scope,
            )
        return BirdCallResult(ok=False, error=err, elapsed_ms=elapsed_ms, raw_stderr=stderr)

    if not stdout:
        return BirdCallResult(ok=True, data=None, elapsed_ms=elapsed_ms, raw_stderr=stderr)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return BirdCallResult(
            ok=False,
            error=BirdError(
                code="BIRD_UNEXPECTED_ERROR",
                message="bird stdout was not valid JSON (was --json forgotten?)",
                recoverable=False,
                scope=scope,
            ),
            elapsed_ms=elapsed_ms,
            raw_stderr=stderr,
        )

    return BirdCallResult(ok=True, data=data, elapsed_ms=elapsed_ms, raw_stderr=stderr)


# ----- Normalization -----


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _first(*values: Any) -> Any:
    """First value that is not None (unlike `a or b`, keeps 0 and "")."""
    for v in values:
        if v is not None:
            return v
    return None


def _to_iso(v: Any) -> Optional[str]:
    """Normalize bird/GraphQL dates ("Tue Sep 29 03:11:47 +0000 2026") to ISO 8601 UTC."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip()
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return s


_SOURCE_RE = re.compile(r">([^<]+)<")


def _raw_extras(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Pull metrics / author profile that only exist in `--json-full` `_raw`."""
    r = raw.get("_raw")
    if not isinstance(r, dict):
        return {}
    if r.get("__typename") == "TweetWithVisibilityResults" and isinstance(r.get("tweet"), dict):
        r = r["tweet"]
    legacy = r.get("legacy") or {}
    user = ((r.get("core") or {}).get("user_results") or {}).get("result") or {}
    ulegacy = user.get("legacy") or {}
    ucore = user.get("core") or {}
    source = r.get("source") or ""
    m = _SOURCE_RE.search(source)
    return {
        "views": _to_int((r.get("views") or {}).get("count")),
        "quotes": _to_int(legacy.get("quote_count")),
        "bookmarks": _to_int(legacy.get("bookmark_count")),
        "lang": legacy.get("lang"),
        "source": m.group(1) if m else (source or None),
        "is_reply": bool(legacy.get("in_reply_to_status_id_str")),
        "links": _expanded_links(r, legacy),
        "user": {
            "followers_count": _to_int(ulegacy.get("followers_count")),
            "following_count": _to_int(ulegacy.get("friends_count")),
            "listed_count": _to_int(ulegacy.get("listed_count")),
            "description": ulegacy.get("description")
                           or ((user.get("profile_bio") or {}).get("description")),
            "created_at": _to_iso(ucore.get("created_at") or ulegacy.get("created_at")),
            "default_profile": ulegacy.get("default_profile"),
            "blue_verified": user.get("is_blue_verified"),
        } if user else {},
    }


def _expanded_links(r: Dict[str, Any], legacy: Dict[str, Any]) -> List[str]:
    """t.co hides where a post points (GitHub, articles); lift expanded URLs."""
    note = (((r.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {})
    urls = ((note.get("entity_set") or {}).get("urls") or []) + ((legacy.get("entities") or {}).get("urls") or [])
    out: List[str] = []
    for u in urls:
        e = u.get("expanded_url") if isinstance(u, dict) else None
        if e and e not in out:
            out.append(e)
    return out


def _author_quality(user: Dict[str, Any]) -> Optional[float]:
    if not user or user.get("followers_count") is None:
        return None
    import _noise  # type: ignore[import-not-found]  # local import avoids a cycle at module load
    return _noise.author_quality({
        "public_metrics": {
            "followers_count": user.get("followers_count"),
            "following_count": user.get("following_count"),
            "listed_count": user.get("listed_count"),
        },
        "created_at": user.get("created_at"),
        "description": user.get("description"),
        "default_profile": user.get("default_profile"),
    })


def _quoted_summary(q: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(q, dict) or not q.get("id"):
        return None
    a = q.get("author") or {}
    handle = _normalize_handle_str(a.get("username") if isinstance(a, dict) else a)
    return {
        "source_id": str(q["id"]),
        "url": f"https://x.com/{handle}/status/{q['id']}" if handle else None,
        "author_handle": f"@{handle}" if handle else None,
        "text": _unescape(q.get("text")),
        "likes": _to_int(q.get("likeCount")),
    }


def normalize_tweet(raw: Dict[str, Any], stage: str = "discovery") -> Dict[str, Any]:
    """Map a bird tweet JSON into the common item shape.

    Bird `--json` tweets carry: id, text, author{username,name}, authorId,
    conversationId, createdAt (Twitter date format), likeCount, replyCount,
    retweetCount, optional quotedTweet / media. With `--json-full` the `_raw`
    GraphQL payload adds views, quotes, bookmarks, lang, source and the
    author's profile metrics, which we lift here and then discard.
    """
    if not isinstance(raw, dict):
        return {}
    source_id = str(raw.get("id") or raw.get("tweetId") or raw.get("rest_id") or "")
    if not source_id:
        return {}
    extras = _raw_extras(raw)
    user = extras.get("user") or {}

    author_field = raw.get("author") or {}
    if isinstance(author_field, str):
        author_field = {"username": author_field}
    author_handle = _normalize_handle_str(author_field.get("username") or author_field.get("screen_name") or author_field.get("handle"))
    author_name = author_field.get("name") or author_field.get("displayName")
    author_followers = _to_int(_first(author_field.get("followersCount"), author_field.get("followers_count"),
                                      user.get("followers_count")))
    author_verified = _first(author_field.get("verified"), author_field.get("isVerified"), user.get("blue_verified"))

    url = raw.get("url")
    if not url and author_handle and source_id:
        url = f"https://x.com/{author_handle.lstrip('@')}/status/{source_id}"

    item = {
        "url": url,
        "source_id": source_id,
        "author": {
            "name": author_name,
            "handle": f"@{author_handle.lstrip('@')}" if author_handle else None,
            "url": f"https://x.com/{author_handle.lstrip('@')}" if author_handle else None,
            "source": extras.get("source"),
            "followers": author_followers,
            "following": user.get("following_count"),
            "verified": bool(author_verified) if author_verified is not None else None,
            "bio": user.get("description"),
            "quality": _author_quality(user),
        },
        "published_at": _to_iso(raw.get("createdAt") or raw.get("created_at")),
        "text": _unescape(_first(raw.get("text"), raw.get("fullText"), raw.get("full_text"))),
        "metrics": {
            "likes": _to_int(_first(raw.get("likeCount"), raw.get("favorite_count"))),
            "reposts": _to_int(_first(raw.get("retweetCount"), raw.get("retweet_count"))),
            "replies": _to_int(_first(raw.get("replyCount"), raw.get("reply_count"))),
            "quotes": _to_int(_first(raw.get("quoteCount"), raw.get("quote_count"), extras.get("quotes"))),
            "views": _to_int(_first(raw.get("viewCount"), raw.get("view_count"), extras.get("views"))),
            "bookmarks": extras.get("bookmarks"),
        },
        "conversation_id": raw.get("conversationId"),
        "matched_terms": [],
        "why_selected": None,
        "provenance": {
            "tool": "bird",
            "stage": stage,
            "fetched_at": _now(),
        },
        "limitations": [],
    }
    if extras.get("links"):
        item["links"] = extras["links"]
    if extras.get("lang"):
        item["lang"] = extras["lang"]
    quoted = _quoted_summary(raw.get("quotedTweet"))
    if quoted:
        item["quoted"] = quoted
    media = raw.get("media")
    if isinstance(media, list) and media:
        item["media"] = [m.get("type") for m in media if isinstance(m, dict) and m.get("type")]
    return item


def _normalize_handle_str(h: Any) -> str:
    if not h:
        return ""
    return str(h).lstrip("@").strip()


# ----- Public API for each subcommand -----


def diagnose() -> Tuple[Dict[str, Any], List[BirdError]]:
    """Return bird credential status. Does not consume API quota."""
    errors: List[BirdError] = []
    status: Dict[str, Any] = {
        "available": False,
        "checked_at": _now(),
        "user": None,
        "reason": None,
    }
    if not _is_installed():
        status["reason"] = "bird_not_installed"
        errors.append(BirdError(
            code="BIRD_NOT_INSTALLED",
            message=f"`{BIRD_BIN}` is not installed or not on PATH.",
            recoverable=False,
            scope="diagnose",
        ))
        return status, errors
    if not _has_auth():
        status["reason"] = "missing_cookie"
        errors.append(BirdError(
            code="BIRD_AUTH_MISSING",
            message="AUTH_TOKEN / CT0 are not set and bird could not read browser cookies.",
            recoverable=True,
            scope="diagnose",
        ))
        return status, errors

    # `bird check` does not support --json in bird 0.8.x; it prints a human
    # text report to stdout and exits 0 on success. _run() would treat the
    # non-JSON stdout as BIRD_UNEXPECTED_ERROR, so call subprocess directly
    # and judge by returncode + text content.
    import subprocess
    try:
        proc = subprocess.run(
            [BIRD_BIN, "check"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        status["reason"] = "bird_unexpected_error"
        errors.append(BirdError(
            code="BIRD_UNEXPECTED_ERROR",
            message="bird check timed out after 20s.",
            recoverable=True,
            scope="diagnose",
        ))
        return status, errors
    except Exception as exc:
        status["reason"] = "bird_unexpected_error"
        errors.append(BirdError(
            code="BIRD_UNEXPECTED_ERROR",
            message=f"bird check invocation failed: {type(exc).__name__}",
            recoverable=False,
            scope="diagnose",
        ))
        return status, errors

    stdout = (proc.stdout or "")
    stderr = (proc.stderr or "")
    combined_lower = (stdout + stderr).lower()

    if proc.returncode != 0:
        if "401" in combined_lower or "unauthorized" in combined_lower or "expired" in combined_lower:
            status["reason"] = "bird_auth_expired"
            errors.append(BirdError(
                code="BIRD_AUTH_EXPIRED",
                message="bird check reports auth failure. Refresh AUTH_TOKEN / CT0 cookies, run `aachat up`, then re-run.",
                recoverable=False,
                scope="diagnose",
            ))
        else:
            status["reason"] = "bird_unexpected_error"
            errors.append(BirdError(
                code="BIRD_UNEXPECTED_ERROR",
                message=f"bird check exit={proc.returncode}. stderr head: {stderr[:200] if stderr else '<empty>'}",
                recoverable=True,
                scope="diagnose",
            ))
        return status, errors

    # `bird check` success indicators (handles both --plain and emoji output).
    if "ready" not in combined_lower and "auth_token" not in combined_lower:
        status["reason"] = "bird_unexpected_error"
        errors.append(BirdError(
            code="BIRD_UNEXPECTED_ERROR",
            message=f"bird check exited 0 but output is unrecognized. stdout head: {stdout[:200]}",
            recoverable=True,
            scope="diagnose",
        ))
        return status, errors

    status["available"] = True
    return status, errors


def search(query: str, limit: int, max_fetch: int) -> BirdCallResult:
    """Run `bird search <query> -n N --json`. Returns raw list of tweets."""
    n = max(1, min(int(max_fetch or limit), 500))
    args = ["search", query, "-n", str(n), "--json-full"]
    res = _run(args, scope="discovery")
    if not res.ok:
        return res
    tweets = _extract_tweets(res.data)
    res.data = tweets
    return res


def thread(post_id: str) -> BirdCallResult:
    res = _run(["thread", post_id, "--json-full"], scope="expand")
    if not res.ok:
        return res
    tweets = _extract_tweets(res.data)
    res.data = tweets
    return res


def replies(post_id: str, max_pages: int = 2) -> BirdCallResult:
    args = ["replies", post_id, "--all", "--max-pages", str(max(1, int(max_pages))), "--json-full"]
    res = _run(args, scope="expand")
    if not res.ok:
        return res
    tweets = _extract_tweets(res.data)
    res.data = tweets
    return res


def quotes(post_id: str, n: int = 40) -> BirdCallResult:
    """Quote posts of one post via the `quoted_tweet_id:<id>` search operator.

    `bird replies` never returns quotes, yet on announcements the quotes are
    where most of the commentary happens.
    """
    if not str(post_id).isdigit():
        return BirdCallResult(ok=False, error=BirdError(
            code="INVALID_INPUT", message=f"quotes needs a numeric post id, got {post_id!r}",
            recoverable=False, scope="expand"))
    return search(f"quoted_tweet_id:{post_id}", limit=n, max_fetch=n)


def user_tweets(handle: str, n: int = 50) -> BirdCallResult:
    handle = handle if handle.startswith("@") else f"@{handle}"
    args = ["user-tweets", handle, "-n", str(max(1, int(n))), "--json-full"]
    res = _run(args, scope="account")
    if not res.ok:
        return res
    tweets = _extract_tweets(res.data)
    res.data = tweets
    return res


def user_mentions(handle: str, n: int = 30) -> BirdCallResult:
    handle = handle if handle.startswith("@") else f"@{handle}"
    args = ["mentions", "--user", handle, "-n", str(max(1, int(n))), "--json-full"]
    res = _run(args, scope="account")
    if not res.ok:
        return res
    tweets = _extract_tweets(res.data)
    res.data = tweets
    return res


def about(handle: str) -> BirdCallResult:
    handle = handle if handle.startswith("@") else f"@{handle}"
    return _run(["about", handle, "--json"], scope="account")


def read_post(post_id: str) -> BirdCallResult:
    res = _run(["read", post_id, "--json-full"], scope="lookup")
    if not res.ok:
        return res
    if isinstance(res.data, dict):
        res.data = [res.data]
    else:
        res.data = _extract_tweets(res.data)
    return res


def news_with_tweets() -> BirdCallResult:
    return _run(["news", "--with-tweets", "--json"], scope="trend")


def trending() -> BirdCallResult:
    return _run(["trending", "--json"], scope="trend")


# ----- Social graph (graph subcommand) -----

# Following/followers pagination over `--all` can span many pages with built-in
# delays, so allow a longer ceiling than the default per-call timeout.
GRAPH_TIMEOUT_SEC = 180


def resolve_user_id(handle: str) -> BirdCallResult:
    """Resolve a @handle to its numeric user id.

    `bird about` does not return the id, but `user-tweets <handle> -n 1`
    returns tweets carrying `authorId`. We use that as the resolution path.
    `data` is set to the id string, or None when it cannot be resolved.
    """
    handle = handle if handle.startswith("@") else f"@{handle}"
    res = _run(["user-tweets", handle, "-n", "1", "--json"], scope="account")
    if not res.ok:
        return res
    user_id: Optional[str] = None
    for t in _extract_tweets(res.data):
        candidate = t.get("authorId") or (t.get("author") or {}).get("id")
        if candidate:
            user_id = str(candidate)
            break
    res.data = user_id
    return res


def following(user_id: str, max_pages: int = 10, page_size: int = 100) -> BirdCallResult:
    """Run `bird following --user <id> --all --max-pages N`. Returns user dicts.

    `--user` takes a numeric user id (not a @handle). Resolve handles with
    `resolve_user_id` first.
    """
    n = max(1, min(int(page_size), 100))
    args = ["following", "--user", str(user_id), "-n", str(n),
            "--all", "--max-pages", str(max(1, int(max_pages))), "--json"]
    res = _run(args, scope="account", timeout=GRAPH_TIMEOUT_SEC)
    if not res.ok:
        return res
    res.data = _extract_users(res.data)
    return res


def followers(user_id: str, max_pages: int = 10, page_size: int = 100) -> BirdCallResult:
    """Run `bird followers --user <id> --all --max-pages N`. Returns user dicts."""
    n = max(1, min(int(page_size), 100))
    args = ["followers", "--user", str(user_id), "-n", str(n),
            "--all", "--max-pages", str(max(1, int(max_pages))), "--json"]
    res = _run(args, scope="account", timeout=GRAPH_TIMEOUT_SEC)
    if not res.ok:
        return res
    res.data = _extract_users(res.data)
    return res


def normalize_user(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a bird social-graph user JSON into the common candidate shape.

    Observed fields (bird 0.8.x following/followers): id, username, name,
    description, followersCount, followingCount, isBlueVerified, createdAt.
    """
    if not isinstance(raw, dict):
        return {}
    uid = str(raw.get("id") or raw.get("rest_id") or "")
    handle = _normalize_handle_str(raw.get("username") or raw.get("screen_name") or raw.get("handle"))
    if not uid and not handle:
        return {}
    blue = raw.get("isBlueVerified")
    if blue is None:
        blue = raw.get("verified") or raw.get("isVerified")
    return {
        "id": uid or None,
        "handle": f"@{handle}" if handle else None,
        "name": raw.get("name") or raw.get("displayName"),
        "url": f"https://x.com/{handle}" if handle else None,
        "bio": raw.get("description") or raw.get("bio"),
        "followers_count": _to_int(raw.get("followersCount") or raw.get("followers_count")),
        "following_count": _to_int(raw.get("followingCount") or raw.get("following_count")),
        "blue_verified": bool(blue) if blue is not None else None,
        "created_at": _to_iso(raw.get("createdAt") or raw.get("created_at")),
    }


def _extract_users(data: Any) -> List[Dict[str, Any]]:
    """Extract a list of user dicts from bird following/followers output variants."""
    if data is None:
        return []
    if isinstance(data, list):
        return [u for u in data if isinstance(u, dict)]
    if isinstance(data, dict):
        for key in ("users", "following", "followers", "items", "results", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [u for u in v if isinstance(u, dict)]
        if "id" in data and ("username" in data or "screen_name" in data):
            return [data]
    return []


def _extract_tweets(data: Any) -> List[Dict[str, Any]]:
    """Extract a list of tweet dicts from bird JSON output variants."""
    if data is None:
        return []
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        for key in ("tweets", "items", "results", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [t for t in v if isinstance(t, dict)]
        if "id" in data and ("text" in data or "fullText" in data or "full_text" in data):
            return [data]
    return []
