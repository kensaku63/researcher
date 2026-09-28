"""Structured field -> X search operator query string.

Builds bird and X API queries from the same structured input. Differences
between bird and X API are encoded in `differences[]` so the agent can see
why a field was handled differently.

Reference: docs/agent-designs/x-research-expert/SPEC-script.md (L685-714)
and x-research-methods.md (L294-388).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple


# Conjunction-required operators that need at least one standalone term
# to coexist with them in the query. See x-research-methods.md L330-346.
_CONJUNCTION_REQUIRED = {
    "is:retweet", "is:reply", "is:quote", "is:verified",
    "has:hashtags", "has:cashtags", "has:links", "has:mentions",
    "has:media", "has:images", "has:videos", "has:video_link", "has:geo",
}


@dataclass
class StructuredQuery:
    keywords: List[str] = field(default_factory=list)
    phrases: List[str] = field(default_factory=list)
    any_of_groups: List[List[str]] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    from_accounts: List[str] = field(default_factory=list)
    to_accounts: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    include_types: List[str] = field(default_factory=list)
    exclude_types: List[str] = field(default_factory=list)
    min_followers: Optional[int] = None
    max_followers: Optional[int] = None
    language: Optional[str] = None
    period: Optional[str] = None
    sort: Optional[str] = None
    raw_query: Optional[str] = None


def _normalize_handle(h: str) -> str:
    return h.lstrip("@").strip()


def _normalize_hashtag(t: str) -> str:
    t = t.strip()
    return t if t.startswith("#") else f"#{t}"


def _type_token(kind: str, t: str) -> str:
    t = t.strip().lower()
    aliases = {
        "media": "has:media",
        "links": "has:links",
        "images": "has:images",
        "videos": "has:videos",
        "video": "has:videos",
        "verified": "is:verified",
        "reply": "is:reply",
        "quote": "is:quote",
        "retweet": "is:retweet",
    }
    op = aliases.get(t)
    if op is None:
        return ""
    if kind == "include":
        return op
    # exclude
    if op.startswith("is:") or op.startswith("has:"):
        return f"-{op}"
    return ""


def _period_to_dates(period: str) -> Optional[Tuple[datetime, datetime]]:
    """Resolve relative period (24h / 7d / 30d / 90d) to absolute UTC range.

    Absolute range `YYYY-MM-DD..YYYY-MM-DD` is parsed directly.
    Returns None if period cannot be parsed.

    The end of relative ranges is shifted 30s into the past because X API
    Recent Search rejects end_time within 10s of the request time. 30s gives
    headroom for clock skew and request transit.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    period = (period or "").strip()
    if not period:
        return None

    m = re.fullmatch(r"(\d+)([hd])", period)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        end = now - timedelta(seconds=30)
        return now - delta, end

    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", period)
    if m:
        try:
            start = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(m.group(2), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return start, end
        except ValueError:
            return None

    return None


def _ensure_standalone(parts: List[str]) -> None:
    """If query consists only of conjunction-required operators, raise."""
    if not parts:
        return
    has_standalone = any(p not in _CONJUNCTION_REQUIRED and not p.startswith("-is:") and not p.startswith("-has:") for p in parts)
    if not has_standalone:
        raise ValueError(
            "Query consists only of conjunction-required operators; add at least one keyword, phrase, hashtag, mention, or account."
        )


def _build_base(q: StructuredQuery) -> List[str]:
    parts: List[str] = []
    parts.extend(q.keywords)
    parts.extend([f'"{p}"' for p in q.phrases])
    for group in q.any_of_groups:
        if not group:
            continue
        cleaned = [g.strip() for g in group if g.strip()]
        if not cleaned:
            continue
        parts.append("(" + " OR ".join(cleaned) + ")")
    if q.hashtags:
        tags = [_normalize_hashtag(t) for t in q.hashtags if t.strip()]
        if tags:
            parts.append("(" + " OR ".join(tags) + ")")
    if q.from_accounts:
        froms = [f"from:{_normalize_handle(h)}" for h in q.from_accounts if h.strip()]
        if froms:
            parts.append("(" + " OR ".join(froms) + ")" if len(froms) > 1 else froms[0])
    if q.to_accounts:
        tos = [f"to:{_normalize_handle(h)}" for h in q.to_accounts if h.strip()]
        if tos:
            parts.append("(" + " OR ".join(tos) + ")" if len(tos) > 1 else tos[0])
    for m in q.mentions:
        h = _normalize_handle(m)
        if h:
            parts.append(f"@{h}")
    for t in q.include_types:
        token = _type_token("include", t)
        if token:
            parts.append(token)
    for t in q.exclude_types:
        token = _type_token("exclude", t)
        if token:
            parts.append(token)
    for w in q.exclude:
        w = w.strip()
        if w:
            parts.append(f"-{w}")
    if q.min_followers is not None or q.max_followers is not None:
        lo = q.min_followers if q.min_followers is not None else ""
        hi = q.max_followers if q.max_followers is not None else ""
        parts.append(f"followers_count:{lo}..{hi}")
    if q.language:
        parts.append(f"lang:{q.language}")
    return parts


def build(q: StructuredQuery) -> Dict[str, object]:
    """Return {'bird': str, 'x_api': str, 'differences': list, 'x_api_params': dict}.

    `x_api_params` carries non-operator API parameters (start_time, end_time,
    sort_order) that are passed alongside the query string.
    """
    if q.raw_query:
        rq = q.raw_query.strip()
        return {
            "bird": rq,
            "x_api": rq,
            "differences": [],
            "x_api_params": {},
        }

    base_parts = _build_base(q)
    differences: List[Dict[str, str]] = []
    x_api_params: Dict[str, object] = {}

    bird_parts = list(base_parts)
    x_api_parts = list(base_parts)

    if q.period:
        absolute = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", q.period.strip())
        if absolute:
            bird_parts.extend([
                f"since:{absolute.group(1)}",
                f"until:{absolute.group(2)}",
            ])
            try:
                start = datetime.strptime(absolute.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                end = datetime.strptime(absolute.group(2), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                # X API rejects end_time within 10s of request time; clamp to
                # 30s ago if the date is today or in the future.
                now_minus_30 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=30)
                if end > now_minus_30:
                    end = now_minus_30
                x_api_params["start_time"] = start.isoformat()
                x_api_params["end_time"] = end.isoformat()
            except ValueError:
                pass
            differences.append({
                "field": "period",
                "bird": "since/until in query",
                "x_api": "start_time/end_time params",
            })
        else:
            range_ = _period_to_dates(q.period)
            if range_:
                start, end = range_
                bird_parts.extend([
                    f"since:{start.strftime('%Y-%m-%d')}",
                    f"until:{end.strftime('%Y-%m-%d')}",
                ])
                x_api_params["start_time"] = start.isoformat()
                x_api_params["end_time"] = end.isoformat()
                differences.append({
                    "field": "period",
                    "bird": "since/until in query",
                    "x_api": "start_time/end_time params",
                })

    if q.sort:
        s = q.sort.strip().lower()
        if s in {"recency", "relevancy"}:
            x_api_params["sort_order"] = s
            differences.append({
                "field": "sort",
                "bird": "post-fetch sort",
                "x_api": f"sort_order={s}",
            })
        elif s == "engagement":
            differences.append({
                "field": "sort",
                "bird": "post-fetch sort by engagement",
                "x_api": "post-fetch sort by engagement",
            })

    try:
        _ensure_standalone(bird_parts)
        _ensure_standalone(x_api_parts)
    except ValueError:
        raise

    return {
        "bird": " ".join(p for p in bird_parts if p).strip(),
        "x_api": " ".join(p for p in x_api_parts if p).strip(),
        "differences": differences,
        "x_api_params": x_api_params,
    }
