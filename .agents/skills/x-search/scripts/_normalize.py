"""Scoring, sorting, representative pick, and why_selected enrichment.

Pipeline order (after noise filters):
1. matched_terms detection per item (against built query terms)
2. score per purpose
3. final sort & representative pick (half by score, half by diversity)
4. why_selected natural-language reason
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import _query_builder as qb  # type: ignore[import-not-found]


def detect_matched_terms(items: List[Dict[str, Any]], structured: qb.StructuredQuery) -> Dict[str, List[str]]:
    """Return {source_id: [matched_terms]} based on a simple substring scan.

    Matches on keywords, phrases, any_of (flat), hashtags. NOT on
    `exclude` (those are negative). Case-insensitive.
    """
    terms: List[str] = []
    terms.extend(structured.keywords or [])
    terms.extend(structured.phrases or [])
    for group in structured.any_of_groups or []:
        terms.extend(group or [])
    for ht in structured.hashtags or []:
        terms.append(ht if ht.startswith("#") else f"#{ht}")
    terms = [t for t in (s.strip() for s in terms) if t]

    out: Dict[str, List[str]] = {}
    for it in items:
        sid = str(it.get("source_id") or "")
        if not sid:
            continue
        text = (it.get("text") or "").lower()
        if not text:
            out[sid] = []
            continue
        hits: List[str] = []
        for t in terms:
            if t.lower() in text:
                hits.append(t)
        out[sid] = hits
    return out


def _published_age_seconds(item: Dict[str, Any]) -> Optional[float]:
    published = item.get("published_at")
    if not published:
        return None
    try:
        dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _zscores(values: List[float]) -> List[float]:
    if not values:
        return []
    mu = sum(values) / len(values)
    var = sum((v - mu) ** 2 for v in values) / len(values)
    sd = math.sqrt(var) or 1.0
    return [(v - mu) / sd for v in values]


def _period_seconds(period: Optional[str]) -> float:
    """Approximate window length used for recency normalization."""
    if not period:
        return 7 * 24 * 3600
    import re
    m = re.fullmatch(r"(\d+)([hd])", period.strip())
    if m:
        n = int(m.group(1))
        return n * 3600 if m.group(2) == "h" else n * 86400
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", period.strip())
    if m:
        try:
            start = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(m.group(2), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max(3600, (end - start).total_seconds())
        except ValueError:
            return 7 * 24 * 3600
    return 7 * 24 * 3600


def score_items(items: List[Dict[str, Any]], purpose: str, period: Optional[str],
                from_accounts: Optional[List[str]] = None,
                mentions: Optional[List[str]] = None) -> List[Tuple[Dict[str, Any], float]]:
    if not items:
        return []
    likes = [float((it.get("metrics") or {}).get("likes") or 0) for it in items]
    replies = [float((it.get("metrics") or {}).get("replies") or 0) for it in items]
    quotes = [float((it.get("metrics") or {}).get("quotes") or 0) for it in items]
    z_likes = _zscores(likes)
    z_replies = _zscores(replies)
    z_quotes = _zscores(quotes)
    period_s = _period_seconds(period)
    accounts_lower = {a.lstrip("@").lower() for a in (from_accounts or []) + (mentions or [])}

    scored: List[Tuple[Dict[str, Any], float]] = []
    for i, it in enumerate(items):
        age = _published_age_seconds(it)
        recency = max(0.0, 1.0 - (age / period_s)) if age is not None else 0.5
        reply_to_like = (replies[i] / max(likes[i], 1.0))
        matched_density = (len(it.get("matched_terms") or []) / max(len((it.get("text") or "")), 1))
        handle = ((it.get("author") or {}).get("handle") or "").lstrip("@").lower()
        account_match = 1.0 if handle and handle in accounts_lower else 0.0
        q = (it.get("author") or {}).get("quality")
        try:
            qf = float(q) if q is not None else 0.5
        except (TypeError, ValueError):
            qf = 0.5

        if purpose == "trend_discovery":
            base = 0.5 * z_likes[i] + 0.3 * recency + 0.2 * reply_to_like
        elif purpose == "content_planning":
            base = 0.4 * z_replies[i] + 0.4 * reply_to_like + 0.2 * z_quotes[i]
        elif purpose == "competitor_research":
            base = 0.4 * z_likes[i] + 0.3 * recency + 0.3 * account_match
        elif purpose == "influencer_discovery":
            followers = float((it.get("author") or {}).get("followers") or 0)
            engagement = (likes[i] + replies[i] + quotes[i]) / max(followers, 1.0)
            base = 0.4 * engagement + 0.3 * recency + 0.3 * account_match
        else:
            base = 0.5 * z_likes[i] + 0.3 * recency + 0.2 * matched_density
        if purpose == "competitor_research":
            final = base
        else:
            final = base * (0.5 + 0.5 * qf)
        scored.append((it, float(final)))
    return scored


def representative_pick(scored: List[Tuple[Dict[str, Any], float]], limit: int) -> List[Dict[str, Any]]:
    """Top by score for half, then diversify by author / hashtag for the rest."""
    if not scored:
        return []
    scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)
    half = max(1, limit // 2)
    top: List[Dict[str, Any]] = [it for it, _ in scored_sorted[:half]]
    chosen_authors = {((it.get("author") or {}).get("handle") or "").lower() for it in top}
    out = list(top)
    for it, _ in scored_sorted[half:]:
        if len(out) >= limit:
            break
        handle = ((it.get("author") or {}).get("handle") or "").lower()
        if handle and handle in chosen_authors:
            continue
        out.append(it)
        if handle:
            chosen_authors.add(handle)
    # If we still need more (e.g. all same author), top up by raw score
    if len(out) < limit:
        seen_ids = {it.get("source_id") for it in out}
        for it, _ in scored_sorted:
            if len(out) >= limit:
                break
            if it.get("source_id") in seen_ids:
                continue
            out.append(it)
            seen_ids.add(it.get("source_id"))
    return out[:limit]


def attach_why_selected(items: List[Dict[str, Any]], purpose: str) -> None:
    for it in items:
        if it.get("why_selected"):
            continue
        m = it.get("metrics") or {}
        parts: List[str] = []
        likes = m.get("likes")
        replies = m.get("replies")
        if purpose == "trend_discovery":
            if likes:
                parts.append(f"直近期間内で likes={likes}")
            if replies:
                parts.append(f"返信={replies} で会話継続")
        elif purpose == "content_planning":
            if replies:
                parts.append(f"返信={replies} と本文構成が論点を含む")
        elif purpose == "competitor_research":
            handle = (it.get("author") or {}).get("handle")
            if handle:
                parts.append(f"競合アカウント {handle} の投稿")
            if likes:
                parts.append(f"likes={likes}")
        elif purpose == "influencer_discovery":
            followers = (it.get("author") or {}).get("followers")
            if followers:
                parts.append(f"followers={followers} と領域一致度")
        else:
            if likes:
                parts.append(f"likes={likes} 上位")
            if it.get("matched_terms"):
                parts.append(f"matched_terms={'/'.join(it['matched_terms'][:3])}")
        if not parts:
            parts.append("取得集合の代表として採用")
        it["why_selected"] = "、".join(parts)
