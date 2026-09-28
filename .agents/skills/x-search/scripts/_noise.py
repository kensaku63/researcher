"""Noise filters and exclusion-reason aggregation for x-search.

Implements the zero-cost (Phase 2) and lookup-augmented (Phase 3) filters
described in SPEC-script.md L306-565:

- text quality (too_short / link_only / hashtag_stuffing / no_matched_terms)
- spam phrase dictionary (knowledge/noise-phrases.<lang>.txt)
- automated source (knowledge/automated-sources.txt; X API only)
- language mismatch
- near-duplicate clustering (SimHash, 64bit)
- author quality score
- engagement anomaly
- recommended excludes

All filters share a common entry point `apply_filters(items, options) ->
(kept, excluded_summary, recommended_excludes)`.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"(?:^|\s)#[\w\u3040-\u30ff\u4e00-\u9fff_]+", re.UNICODE)
_MENTION_RE = re.compile(r"(?:^|\s)@[A-Za-z0-9_]+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


# Purpose-keyed thresholds. SPEC-script.md L350-359, L454-463, L470-480.
PURPOSE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "market_research": {
        "text_min_length": 20,
        "max_url_ratio": 0.7,
        "max_hashtag_density": 0.5,
        "min_author_quality": 0.30,
        "same_author_limit": 3,
    },
    "competitor_research": {
        "text_min_length": 10,
        "max_url_ratio": 0.9,
        "max_hashtag_density": 0.7,
        "min_author_quality": 0.00,
        "same_author_limit": 5,
    },
    "trend_discovery": {
        "text_min_length": 15,
        "max_url_ratio": 0.7,
        "max_hashtag_density": 0.5,
        "min_author_quality": 0.20,
        "same_author_limit": 2,
    },
    "influencer_discovery": {
        "text_min_length": 20,
        "max_url_ratio": 0.7,
        "max_hashtag_density": 0.4,
        "min_author_quality": 0.40,
        "same_author_limit": 5,
    },
    "content_planning": {
        "text_min_length": 30,
        "max_url_ratio": 0.5,
        "max_hashtag_density": 0.4,
        "min_author_quality": 0.30,
        "same_author_limit": 3,
    },
    "social_marketing_research": {
        "text_min_length": 20,
        "max_url_ratio": 0.7,
        "max_hashtag_density": 0.5,
        "min_author_quality": 0.30,
        "same_author_limit": 3,
    },
}


@dataclass
class NoiseOptions:
    purpose: Optional[str] = None
    language: Optional[str] = None
    text_min_length: Optional[int] = None
    max_url_ratio: Optional[float] = None
    max_hashtag_density: Optional[float] = None
    require_matched_terms: bool = True
    noise_phrases_path: Optional[str] = None
    disable_noise_phrases: bool = False
    filter_automated_source: str = "auto"  # auto | on | off
    detect_near_duplicates: bool = True
    near_dup_similarity_threshold: float = 0.85
    min_author_quality: Optional[float] = None
    detect_engagement_anomaly: bool = True
    recommend_excludes: bool = True
    same_author_limit: Optional[int] = None
    knowledge_dir: Optional[str] = None  # resolved from skill dir at runtime


@dataclass
class ExclusionRow:
    code: str
    count: int = 0
    cluster_count: int = 0
    matched_terms: List[str] = field(default_factory=list)
    matched_sources: List[str] = field(default_factory=list)
    sample_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"code": self.code, "count": self.count}
        if self.cluster_count:
            d["cluster_count"] = self.cluster_count
        if self.matched_terms:
            d["matched_terms"] = self.matched_terms[:5]
        if self.matched_sources:
            d["matched_sources"] = self.matched_sources[:5]
        if self.sample_ids:
            d["sample_ids"] = self.sample_ids[:3]
        return d


# ----- Dictionary loaders -----


def _load_lines(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            out: List[str] = []
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(s)
            return out
    except (OSError, UnicodeDecodeError):
        return []


def _resolve_noise_phrases_path(opts: NoiseOptions) -> Optional[str]:
    if opts.noise_phrases_path:
        return opts.noise_phrases_path
    if not opts.knowledge_dir:
        return None
    lang = (opts.language or "en").lower()
    lang = "ja" if lang.startswith("ja") else "en"
    candidate = os.path.join(opts.knowledge_dir, f"noise-phrases.{lang}.txt")
    return candidate if os.path.exists(candidate) else None


def _resolve_automated_sources_path(opts: NoiseOptions) -> Optional[str]:
    if not opts.knowledge_dir:
        return None
    candidate = os.path.join(opts.knowledge_dir, "automated-sources.txt")
    return candidate if os.path.exists(candidate) else None


# ----- Text metrics -----


def _strip_urls_mentions(text: str) -> str:
    t = _URL_RE.sub("", text)
    t = _MENTION_RE.sub("", t)
    return t.strip()


def _url_ratio(text: str) -> float:
    if not text:
        return 0.0
    total = len(text)
    urls = _URL_RE.findall(text)
    used = sum(len(u) for u in urls)
    return used / total if total else 0.0


def _hashtag_density(text: str) -> float:
    if not text:
        return 0.0
    tokens = _WORD_RE.findall(text)
    if not tokens:
        return 0.0
    hashtags = _HASHTAG_RE.findall(text)
    return min(1.0, len(hashtags) / len(tokens))


# ----- SimHash for near-duplicate detection -----


def _ngrams(text: str, n: int = 5) -> List[str]:
    text = text.strip()
    if len(text) < n:
        return [text] if text else []
    return [text[i:i + n] for i in range(len(text) - n + 1)]


def _simhash(text: str) -> int:
    bits = [0] * 64
    grams = _ngrams(text, 5)
    if not grams:
        return 0
    counts = Counter(grams)
    for g, w in counts.items():
        h = abs(hash(g)) & 0xFFFFFFFFFFFFFFFF
        for i in range(64):
            if (h >> i) & 1:
                bits[i] += w
            else:
                bits[i] -= w
    result = 0
    for i in range(64):
        if bits[i] > 0:
            result |= (1 << i)
    return result


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _similarity(a: int, b: int) -> float:
    return 1.0 - (_hamming(a, b) / 64.0)


# ----- Author quality score -----


def _account_age_days(created_at: Optional[str]) -> Optional[int]:
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return None
    delta = datetime.now(timezone.utc) - dt
    return max(0, delta.days)


def author_quality(user: Dict[str, Any]) -> Optional[float]:
    """Return author quality score in [0,1]. None if not enough metadata."""
    if not user:
        return None
    pm = user.get("public_metrics") or {}
    followers = int(pm.get("followers_count") or 0)
    following = int(pm.get("following_count") or 0)
    listed = int(pm.get("listed_count") or 0)
    age_days = _account_age_days(user.get("created_at"))
    bio_len = len(user.get("description") or "")
    default_profile = user.get("default_profile")
    if followers == 0 and following == 0 and listed == 0 and age_days is None and bio_len == 0:
        return None

    age_score = min((age_days or 0) / 365.0, 1.0) if age_days is not None else 0.5
    profile_complete = 1.0 if (not default_profile and bio_len > 0) else 0.3
    follow_ratio = 1.0 - min(max((following / max(followers, 1)) / 10.0, 0.0), 1.0)
    listed_ratio = min(max(listed / max(followers / 100.0, 1.0), 0.0), 1.0)
    score = (
        0.30 * age_score
        + 0.20 * profile_complete
        + 0.30 * follow_ratio
        + 0.20 * listed_ratio
    )
    return max(0.0, min(1.0, score))


# ----- Engagement anomaly -----


def _engagement_anomaly(item: Dict[str, Any]) -> bool:
    m = item.get("metrics") or {}
    likes = int(m.get("likes") or 0)
    replies = int(m.get("replies") or 0)
    reposts = int(m.get("reposts") or 0)
    quotes = int(m.get("quotes") or 0)
    views = m.get("views")
    # bird returns likeCount=0 on retweet items because the like belongs to
    # the underlying original tweet. Anomaly heuristics based on likes vs.
    # reposts are not meaningful for these surface RTs.
    text = (item.get("text") or "").lstrip()
    if text.startswith("RT @"):
        return False
    if likes >= 100 and reposts == 0 and quotes == 0 and (replies / max(likes, 1)) < 0.001:
        return True
    if reposts >= 100 and likes < reposts * 0.1:
        return True
    if views is not None and int(views) >= 10000 and (likes + replies + quotes) == 0:
        return True
    return False


# ----- Main filtering pipeline -----


def apply_filters(items: List[Dict[str, Any]], opts: NoiseOptions,
                  matched_terms_for: Optional[Dict[str, List[str]]] = None
                  ) -> Tuple[List[Dict[str, Any]], List[ExclusionRow], List[Dict[str, Any]]]:
    """Run all noise filters in fixed order. Return (kept_items, exclusion_rows, recommended_excludes).

    `matched_terms_for[source_id]` is the list of matched_terms detected per
    item by the caller (typically from `_query_builder`). When provided,
    items with zero matches are dropped per `require_matched_terms`.
    """
    purpose = opts.purpose or "market_research"
    defaults = PURPOSE_DEFAULTS.get(purpose, PURPOSE_DEFAULTS["market_research"])
    text_min = opts.text_min_length if opts.text_min_length is not None else defaults["text_min_length"]
    max_url_ratio = opts.max_url_ratio if opts.max_url_ratio is not None else defaults["max_url_ratio"]
    max_ht = opts.max_hashtag_density if opts.max_hashtag_density is not None else defaults["max_hashtag_density"]
    min_aq = opts.min_author_quality if opts.min_author_quality is not None else defaults["min_author_quality"]
    same_author_cap = opts.same_author_limit if opts.same_author_limit is not None else defaults["same_author_limit"]

    if opts.filter_automated_source == "auto":
        filter_automated = purpose != "competitor_research"
    else:
        filter_automated = opts.filter_automated_source == "on"

    exclusion_rows: Dict[str, ExclusionRow] = {}

    def _drop(item: Dict[str, Any], code: str, **extras: Any) -> None:
        row = exclusion_rows.setdefault(code, ExclusionRow(code=code))
        row.count += 1
        sid = str(item.get("source_id") or "")
        if sid and len(row.sample_ids) < 3:
            row.sample_ids.append(sid)
        if "matched_term" in extras:
            row.matched_terms.append(str(extras["matched_term"]))
        if "matched_source" in extras:
            row.matched_sources.append(str(extras["matched_source"]))

    # 1) attach matched_terms
    if matched_terms_for:
        for it in items:
            sid = str(it.get("source_id") or "")
            it["matched_terms"] = matched_terms_for.get(sid, it.get("matched_terms") or [])

    # 2) text quality filter
    quality_kept: List[Dict[str, Any]] = []
    for it in items:
        text = (it.get("text") or "").strip()
        plain = _strip_urls_mentions(text)
        if not text or len(plain) < text_min:
            _drop(it, "too_short")
            continue
        if text and _url_ratio(text) > max_url_ratio:
            _drop(it, "link_only")
            continue
        if text and _hashtag_density(text) > max_ht:
            _drop(it, "hashtag_stuffing")
            continue
        if opts.require_matched_terms and not (it.get("matched_terms") or []):
            _drop(it, "no_matched_terms")
            continue
        quality_kept.append(it)

    # 3) spam phrase dictionary
    spam_kept: List[Dict[str, Any]] = []
    spam_phrases: List[str] = []
    if not opts.disable_noise_phrases:
        path = _resolve_noise_phrases_path(opts)
        if path:
            spam_phrases = _load_lines(path)
    spam_lower = [p.lower() for p in spam_phrases]
    for it in quality_kept:
        text_lower = (it.get("text") or "").lower()
        hit: Optional[str] = None
        for phrase, lo in zip(spam_phrases, spam_lower):
            if lo in text_lower:
                hit = phrase
                break
        if hit:
            _drop(it, "spam_phrase", matched_term=hit)
            continue
        spam_kept.append(it)

    # 4) automated source filter (X API only; bird sets author.source = None)
    auto_kept: List[Dict[str, Any]] = []
    automated_sources: Set[str] = set()
    if filter_automated:
        ap = _resolve_automated_sources_path(opts)
        if ap:
            automated_sources = {s.lower() for s in _load_lines(ap)}
    for it in spam_kept:
        if filter_automated and automated_sources:
            src = ((it.get("author") or {}).get("source") or "").strip()
            if src and src.lower() in automated_sources:
                _drop(it, "automated_source", matched_source=src)
                continue
        auto_kept.append(it)

    # 5) language filter (X API populates `lang` via tweet.fields; bird does not)
    lang_kept: List[Dict[str, Any]] = []
    target_lang = (opts.language or "").lower() if opts.language else ""
    for it in auto_kept:
        lang_field = (it.get("lang") or "").lower()
        if target_lang and lang_field:
            base = target_lang.split("-")[0]
            if not lang_field.startswith(base):
                _drop(it, "language_mismatch")
                continue
        lang_kept.append(it)

    # 6) exact duplicate by source_id, x_api preferred
    dedup_kept: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for it in lang_kept:
        sid = str(it.get("source_id") or "")
        if not sid:
            dedup_kept.append(it)
            continue
        prev = seen.get(sid)
        if prev is None:
            seen[sid] = it
        else:
            prev_tool = ((prev.get("provenance") or {}).get("tool"))
            curr_tool = ((it.get("provenance") or {}).get("tool"))
            if curr_tool == "x_api" and prev_tool != "x_api":
                _drop(prev, "duplicate_source_id")
                seen[sid] = it
            else:
                _drop(it, "duplicate_source_id")
    dedup_kept = list(seen.values())

    # 7) near-duplicate clustering (SimHash, 64bit)
    ndup_kept: List[Dict[str, Any]] = []
    if opts.detect_near_duplicates and dedup_kept:
        hashes: List[Tuple[Dict[str, Any], int]] = [(it, _simhash(it.get("text") or "")) for it in dedup_kept]
        cluster_id_for: Dict[int, int] = {}
        clusters: List[List[Tuple[Dict[str, Any], int]]] = []
        for idx, (it, h) in enumerate(hashes):
            placed = False
            for cid, members in enumerate(clusters):
                if any(_similarity(h, mh) >= opts.near_dup_similarity_threshold for _, mh in members):
                    members.append((it, h))
                    cluster_id_for[idx] = cid
                    placed = True
                    break
            if not placed:
                clusters.append([(it, h)])
                cluster_id_for[idx] = len(clusters) - 1
        cluster_count_excluded = 0
        for members in clusters:
            if len(members) == 1:
                ndup_kept.append(members[0][0])
                continue
            def _rank(it: Dict[str, Any]) -> Tuple[int, int]:
                tool_rank = 1 if (it.get("provenance") or {}).get("tool") == "x_api" else 0
                likes = int((it.get("metrics") or {}).get("likes") or 0)
                return (tool_rank, likes)
            members_sorted = sorted(members, key=lambda x: _rank(x[0]), reverse=True)
            keep_item = members_sorted[0][0]
            ndup_kept.append(keep_item)
            for dropped, _h in members_sorted[1:]:
                _drop(dropped, "near_duplicate")
            cluster_count_excluded += 1
        if "near_duplicate" in exclusion_rows:
            exclusion_rows["near_duplicate"].cluster_count = cluster_count_excluded
    else:
        ndup_kept = dedup_kept

    # 8) author quality
    aq_kept: List[Dict[str, Any]] = []
    for it in ndup_kept:
        author = it.get("author") or {}
        q = author.get("quality")
        if q is None:
            aq_kept.append(it)
            continue
        try:
            qf = float(q)
        except (TypeError, ValueError):
            aq_kept.append(it)
            continue
        if qf < min_aq:
            _drop(it, "low_author_quality")
            continue
        aq_kept.append(it)

    # 9) engagement anomaly (skipped for competitor_research, per spec)
    eng_kept: List[Dict[str, Any]] = []
    skip_eng = purpose == "competitor_research" or not opts.detect_engagement_anomaly
    for it in aq_kept:
        if not skip_eng and _engagement_anomaly(it):
            _drop(it, "engagement_anomaly")
            continue
        eng_kept.append(it)

    # 10) same-author limit
    sa_kept: List[Dict[str, Any]] = []
    per_author: Dict[str, int] = defaultdict(int)
    for it in eng_kept:
        handle = ((it.get("author") or {}).get("handle") or "").lower()
        per_author[handle] += 1
        if handle and per_author[handle] > same_author_cap:
            _drop(it, "same_author_limit")
            continue
        sa_kept.append(it)

    excluded_rows = list(exclusion_rows.values())

    # 11) recommended excludes (only if requested)
    recommended: List[Dict[str, Any]] = []
    if opts.recommend_excludes:
        recommended = _recommended_excludes(
            kept=sa_kept,
            dropped_summary=exclusion_rows,
            total_excluded=sum(r.count for r in excluded_rows),
        )

    return sa_kept, excluded_rows, recommended


def _recommended_excludes(kept: List[Dict[str, Any]],
                          dropped_summary: Dict[str, ExclusionRow],
                          total_excluded: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_terms: Dict[str, Dict[str, Any]] = {}

    spam_row = dropped_summary.get("spam_phrase")
    if spam_row:
        term_counts = Counter(spam_row.matched_terms)
        for term, count in term_counts.most_common(10):
            if not term:
                continue
            if total_excluded and count < max(1, int(total_excluded * 0.05)):
                continue
            seen_terms[term] = {
                "term": term,
                "reason": "dictionary_hit",
                "evidence_count": count,
            }

    for entry in seen_terms.values():
        out.append(entry)
    out.sort(key=lambda x: x["evidence_count"], reverse=True)
    return out[:10]
