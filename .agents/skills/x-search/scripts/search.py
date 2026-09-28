#!/usr/bin/env python3
"""x-search: X (Twitter) research subcommand dispatcher.

Subcommands: diagnose | search | expand | account | counts | lookup | trend

All subcommands print a single JSON object that conforms to
`schemas/result.schema.json`. Failures are returned as structured
`limitations[]` entries (not non-zero exit codes), except for input
validation errors which exit 2.

Secrets are read from environment variables only:
- AUTH_TOKEN, CT0          (bird Cookie auth)
- X_BEARER_TOKEN           (X API v2)

Secret values are never written to stdout, stderr, or files.

See `docs/agent-designs/x-research-expert/SPEC.md` and `SPEC-script.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Allow execution as `python scripts/search.py ...` (no package context).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _bird  # type: ignore[import-not-found]  # noqa: E402
import _noise  # type: ignore[import-not-found]  # noqa: E402
import _normalize  # type: ignore[import-not-found]  # noqa: E402
import _query_builder as qb  # type: ignore[import-not-found]  # noqa: E402
import _x_api  # type: ignore[import-not-found]  # noqa: E402


SUBCOMMANDS = ("diagnose", "search", "expand", "account", "counts", "lookup", "trend", "graph")

_CONTRADICTION_TERMS = {
    "ja": ["不要", "使わない", "使ってない", "問題ない", "困ってない", "代替で十分", "やめた", "乗り換えない"],
    "en": ["not needed", "do not use", "don't use", "no problem", "not a problem", "alternative is enough", "stopped using"],
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _knowledge_dir() -> str:
    env_dir = os.environ.get("CLAUDE_SKILL_DIR")
    base = env_dir if env_dir and os.path.isdir(env_dir) else _SKILL_DIR
    return os.path.join(base, "knowledge")


def _envelope(tool: str, args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "platform": "x",
        "tool": tool,
        "purpose": getattr(args, "purpose", None),
        "language": getattr(args, "language", None),
        "region": getattr(args, "region", None),
        "period": getattr(args, "period", None),
        "fetched_at": _now(),
        "credentials": {
            "bird":  {"available": False, "checked_at": None, "user": None, "reason": None},
            "x_api": {"available": False, "checked_at": None, "reason": None},
        },
        "queries_built": {
            "bird": None,
            "x_api": None,
            "differences": [],
            "recommended_excludes": [],
        },
        "queries_tried": [],
        "items": [],
        "excluded_summary": {"total_excluded": 0, "by_reason": []},
        "search_quality": None,
        "next_query_candidates": [],
        "usage": {"x_api_post_reads": 0, "bird_calls": 0, "cached_hits": 0},
        "limitations": [],
        "next_human_actions": [],
    }


def _add_limitation(env: Dict[str, Any], code: str, message: str, recoverable: bool, scope: str) -> None:
    env["limitations"].append({
        "code": code,
        "scope": scope,
        "message": message,
        "recoverable": recoverable,
    })


def _bool_arg(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--purpose", choices=[
        "market_research", "competitor_research", "trend_discovery",
        "influencer_discovery", "content_planning", "social_marketing_research",
    ])
    parser.add_argument("--language")
    parser.add_argument("--region")
    parser.add_argument("--period")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-fetch", type=int)
    parser.add_argument("--tool", choices=["auto", "bird", "x_api"], default="auto")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--debug", action="store_true")


def _add_search_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--keywords", action="append", nargs="+", default=[])
    parser.add_argument("--phrases", action="append", nargs="+", default=[])
    parser.add_argument("--any-of", dest="any_of", action="append", nargs="+", default=[],
                        help="One invocation = one OR group. Pass multiple values per invocation "
                             "(e.g. `--any-of A B`) or use comma-separated form (`--any-of A,B`). "
                             "Repeat the flag for multiple OR groups.")
    parser.add_argument("--exclude", action="append", nargs="+", default=[])
    parser.add_argument("--hashtags", action="append", nargs="+", default=[])
    parser.add_argument("--from-accounts", dest="from_accounts", action="append", nargs="+", default=[])
    parser.add_argument("--to-accounts", dest="to_accounts", action="append", nargs="+", default=[])
    parser.add_argument("--mentions", action="append", nargs="+", default=[])
    parser.add_argument("--include-types", dest="include_types", action="append", nargs="+", default=[])
    parser.add_argument("--exclude-types", dest="exclude_types", action="append", nargs="+", default=[])
    parser.add_argument("--min-followers", dest="min_followers", type=int)
    parser.add_argument("--max-followers", dest="max_followers", type=int)
    parser.add_argument("--min-likes", dest="min_likes", type=int)
    parser.add_argument("--min-replies", dest="min_replies", type=int)
    parser.add_argument("--sort", choices=["recency", "relevancy", "engagement"])
    parser.add_argument("--raw-query", dest="raw_query")


def _add_noise_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text-min-length", dest="text_min_length", type=int)
    parser.add_argument("--max-url-ratio", dest="max_url_ratio", type=float)
    parser.add_argument("--max-hashtag-density", dest="max_hashtag_density", type=float)
    parser.add_argument("--require-matched-terms", dest="require_matched_terms",
                        type=_bool_arg, default=True)
    parser.add_argument("--noise-phrases-path", dest="noise_phrases_path")
    parser.add_argument("--disable-noise-phrases", dest="disable_noise_phrases", action="store_true")
    parser.add_argument("--filter-automated-source", dest="filter_automated_source",
                        choices=["auto", "on", "off"], default="auto")
    parser.add_argument("--detect-near-duplicates", dest="detect_near_duplicates",
                        type=_bool_arg, default=True)
    parser.add_argument("--near-dup-similarity-threshold", dest="near_dup_similarity_threshold",
                        type=float, default=0.85)
    parser.add_argument("--min-author-quality", dest="min_author_quality", type=float)
    parser.add_argument("--detect-engagement-anomaly", dest="detect_engagement_anomaly",
                        type=_bool_arg, default=True)
    parser.add_argument("--recommend-excludes", dest="recommend_excludes",
                        type=_bool_arg, default=True)
    parser.add_argument("--same-author-limit", dest="same_author_limit", type=int)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="search", description="x-search dispatcher")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_diag = sub.add_parser("diagnose", help="Check credential availability (no real search)")
    _add_common(p_diag)

    p_search = sub.add_parser("search", help="Keyword / hashtag / profile search")
    _add_common(p_search)
    p_search.add_argument("--collect-with-api", dest="collect_with_api", action="store_true",
                          help="In --tool=auto, also run X API Recent Search after bird discovery for reproducible collection.")
    _add_search_fields(p_search)
    _add_noise_options(p_search)

    p_expand = sub.add_parser("expand", help="Thread / replies deep dive for one post")
    _add_common(p_expand)
    p_expand.add_argument("--id", required=True)
    p_expand.add_argument("--include-thread", dest="include_thread", type=_bool_arg, default=True)
    p_expand.add_argument("--include-replies", dest="include_replies", type=_bool_arg, default=True)
    p_expand.add_argument("--replies-max-pages", dest="replies_max_pages", type=int, default=2)
    p_expand.add_argument("--quote-depth", dest="quote_depth", type=int, default=1)
    _add_noise_options(p_expand)

    p_account = sub.add_parser("account", help="Account-rooted (tweets / mentions / profile)")
    _add_common(p_account)
    p_account.add_argument("--handle", required=True)
    p_account.add_argument("--include-profile", dest="include_profile", type=_bool_arg, default=True)
    p_account.add_argument("--include-tweets", dest="include_tweets", type=int, default=50)
    p_account.add_argument("--include-mentions", dest="include_mentions", type=int, default=30)
    p_account.add_argument("--exclude-types", dest="exclude_types", action="append", nargs="+", default=[])
    _add_noise_options(p_account)

    p_counts = sub.add_parser("counts", help="Time-series counts via X API")
    _add_common(p_counts)
    _add_search_fields(p_counts)
    p_counts.add_argument("--granularity", choices=["minute", "hour", "day"], default="hour")

    p_lookup = sub.add_parser("lookup", help="URL / ID existence check")
    _add_common(p_lookup)
    p_lookup.add_argument("--id", action="append", nargs="+", default=[], required=True)

    p_trend = sub.add_parser("trend", help="X internal topic candidates (bird only)")
    _add_common(p_trend)

    p_graph = sub.add_parser("graph", help="Rank accounts by follow-overlap with a set of seed accounts")
    _add_common(p_graph)
    p_graph.add_argument("--seed", action="append", nargs="+", default=[], required=True,
                         help="Quality-bar seed account (@handle or numeric id). Repeat for multiple seeds.")
    p_graph.add_argument("--candidate", action="append", nargs="+", default=[],
                         help="score mode: account to score by seed-overlap (@handle or numeric id). Repeat.")
    p_graph.add_argument("--mode", choices=["auto", "score", "expand"], default="auto",
                         help="auto picks score when --candidate is given, otherwise expand.")
    p_graph.add_argument("--min-overlap", dest="min_overlap", type=int, default=1,
                         help="Only return accounts following at least this many seeds.")
    p_graph.add_argument("--max-following-pages", dest="max_following_pages", type=int, default=10,
                         help="score mode: page cap when fetching each candidate's following list.")
    p_graph.add_argument("--max-follower-pages", dest="max_follower_pages", type=int, default=10,
                         help="expand mode: page cap when enumerating each seed's followers.")
    p_graph.add_argument("--page-size", dest="page_size", type=int, default=100,
                         help="bird -n per page (max 100).")
    p_graph.add_argument("--min-followers", dest="min_followers", type=int)
    p_graph.add_argument("--max-followers", dest="max_followers", type=int)

    return parser


def _mark_credential_skipped(env: Dict[str, Any], tool: str, reason: str) -> None:
    if env["credentials"][tool].get("checked_at") is None:
        env["credentials"][tool]["checked_at"] = _now()
        env["credentials"][tool]["reason"] = reason


def _resolve_credentials(env: Dict[str, Any], check_bird: bool = True, check_api: bool = True) -> None:
    """Check only credentials needed for this run.

    X API diagnose spends a real Recent Search request, so bird-only exploratory
    paths should not probe it just to fill the credentials block.
    """
    if check_bird:
        bird_status, bird_errs = _bird.diagnose()
        env["credentials"]["bird"] = bird_status
        for e in bird_errs:
            _add_limitation(env, e.code, e.message, e.recoverable, e.scope)
    else:
        _mark_credential_skipped(env, "bird", "skipped_by_tool_selection")

    if check_api:
        api_status, api_errs = _x_api.diagnose()
        env["credentials"]["x_api"] = api_status
        for e in api_errs:
            _add_limitation(env, e.code, e.message, e.recoverable, e.scope)
    else:
        _mark_credential_skipped(env, "x_api", "skipped_by_tool_selection")


def _flatten_csv(values: Any) -> List[str]:
    """Flatten append+nargs structures into a plain list, splitting CSV strings."""
    out: List[str] = []
    if not values:
        return out
    if isinstance(values, str):
        items = [values]
    elif isinstance(values, list):
        items = []
        for v in values:
            if isinstance(v, list):
                items.extend(v)
            else:
                items.append(v)
    else:
        items = [str(values)]
    for v in items:
        if isinstance(v, str) and "," in v:
            out.extend(s.strip() for s in v.split(",") if s.strip())
        else:
            s = str(v).strip()
            if s:
                out.append(s)
    return out


def _structured_from_args(args: argparse.Namespace) -> qb.StructuredQuery:
    any_of_groups: List[List[str]] = []
    for raw in getattr(args, "any_of", []) or []:
        values: List[str] = raw if isinstance(raw, list) else [raw]
        group: List[str] = []
        for v in values:
            if isinstance(v, str) and "," in v:
                group.extend(x.strip() for x in v.split(",") if x.strip())
            else:
                s = str(v).strip()
                if s:
                    group.append(s)
        if group:
            any_of_groups.append(group)
    return qb.StructuredQuery(
        keywords=_flatten_csv(getattr(args, "keywords", [])),
        phrases=_flatten_csv(getattr(args, "phrases", [])),
        any_of_groups=any_of_groups,
        exclude=_flatten_csv(getattr(args, "exclude", [])),
        hashtags=_flatten_csv(getattr(args, "hashtags", [])),
        from_accounts=_flatten_csv(getattr(args, "from_accounts", [])),
        to_accounts=_flatten_csv(getattr(args, "to_accounts", [])),
        mentions=_flatten_csv(getattr(args, "mentions", [])),
        include_types=_flatten_csv(getattr(args, "include_types", [])),
        exclude_types=_flatten_csv(getattr(args, "exclude_types", [])),
        min_followers=getattr(args, "min_followers", None),
        max_followers=getattr(args, "max_followers", None),
        language=getattr(args, "language", None),
        period=getattr(args, "period", None),
        sort=getattr(args, "sort", None),
        raw_query=getattr(args, "raw_query", None),
    )


def _noise_options(args: argparse.Namespace) -> _noise.NoiseOptions:
    return _noise.NoiseOptions(
        purpose=getattr(args, "purpose", None),
        language=getattr(args, "language", None),
        text_min_length=getattr(args, "text_min_length", None),
        max_url_ratio=getattr(args, "max_url_ratio", None),
        max_hashtag_density=getattr(args, "max_hashtag_density", None),
        require_matched_terms=getattr(args, "require_matched_terms", True),
        noise_phrases_path=getattr(args, "noise_phrases_path", None),
        disable_noise_phrases=getattr(args, "disable_noise_phrases", False),
        filter_automated_source=getattr(args, "filter_automated_source", "auto"),
        detect_near_duplicates=getattr(args, "detect_near_duplicates", True),
        near_dup_similarity_threshold=getattr(args, "near_dup_similarity_threshold", 0.85),
        min_author_quality=getattr(args, "min_author_quality", None),
        detect_engagement_anomaly=getattr(args, "detect_engagement_anomaly", True),
        recommend_excludes=getattr(args, "recommend_excludes", True),
        same_author_limit=getattr(args, "same_author_limit", None),
        knowledge_dir=_knowledge_dir(),
    )


def _push_query_tried(env: Dict[str, Any], stage: str, tool: str, query: str,
                      result_count: int, elapsed_ms: int, next_token: Optional[str] = None) -> None:
    row: Dict[str, Any] = {
        "stage": stage,
        "tool": tool,
        "query": query,
        "result_count": int(result_count),
        "elapsed_ms": int(elapsed_ms),
    }
    if next_token:
        row["next_token"] = next_token
    env["queries_tried"].append(row)


def _period_supported_by_recent_api(period: Optional[str]) -> bool:
    """Return whether a period can be served by X API Recent Search/Counts."""
    if not period:
        return True
    p = period.strip()
    import re
    m = re.fullmatch(r"(\d+)([hd])", p)
    if m:
        n = int(m.group(1))
        return n <= 168 if m.group(2) == "h" else n <= 7

    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", p)
    if not m:
        return True
    try:
        start = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(m.group(2), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    return start >= now - timedelta(days=7) and end <= now + timedelta(days=1)


def _add_recent_api_period_limitation(env: Dict[str, Any], scope: str) -> None:
    _add_limitation(
        env,
        "API_RECENT_WINDOW_UNSUPPORTED",
        "X API Recent Search/Counts only supports roughly the last 7 days. "
        "Use Full-archive access for older periods, or use Google/Bing `site:x.com` URL discovery as a fallback.",
        recoverable=True,
        scope=scope,
    )


def _enrich_items_with_api_profile(env: Dict[str, Any], handle: str, items: List[Dict[str, Any]]) -> None:
    """Supplement bird account results with official user metadata when an API token is already configured."""
    if not items or not os.environ.get("X_BEARER_TOKEN"):
        return

    res = _x_api.user_by_username(handle.lstrip("@"))
    env["credentials"]["x_api"]["checked_at"] = _now()
    if not res.ok:
        if res.error:
            env["credentials"]["x_api"]["available"] = False
            env["credentials"]["x_api"]["reason"] = res.error.code.lower()
            _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "account")
        return

    env["credentials"]["x_api"]["available"] = True
    env["credentials"]["x_api"]["reason"] = None
    if not isinstance(res.data, dict):
        return
    user = res.data.get("data") or {}
    if not isinstance(user, dict):
        return
    metrics = user.get("public_metrics") or {}
    api_handle = str(user.get("username") or handle).lstrip("@").lower()
    profile_patch = {
        "name": user.get("name"),
        "followers": metrics.get("followers_count"),
        "verified": user.get("verified"),
        "quality": _noise.author_quality(user),
    }
    for item in items:
        author = item.get("author") or {}
        item_handle = str(author.get("handle") or "").lstrip("@").lower()
        if item_handle and item_handle != api_handle:
            continue
        for key, value in profile_patch.items():
            if value is not None:
                author[key] = value
        item["author"] = author


def _ratio_score(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(max(0.0, min(1.0, numerator / denominator)), 2)


def _structured_terms(structured: Optional[qb.StructuredQuery]) -> List[str]:
    if structured is None:
        return []
    terms: List[str] = []
    terms.extend(structured.keywords)
    terms.extend(structured.phrases)
    for group in structured.any_of_groups:
        terms.extend(group)
    terms.extend(structured.hashtags)
    terms.extend(structured.from_accounts)
    terms.extend(structured.mentions)
    return [str(t).strip() for t in terms if str(t).strip()]


def _negative_terms(language: Optional[str]) -> List[str]:
    lang = (language or "en").lower()
    return _CONTRADICTION_TERMS["ja"] if lang.startswith("ja") else _CONTRADICTION_TERMS["en"]


def _add_search_guidance(env: Dict[str, Any], args: argparse.Namespace,
                         structured: Optional[qb.StructuredQuery]) -> None:
    items = env.get("items") or []
    item_count = len(items)
    terms = _structured_terms(structured)
    matched_terms = {
        str(term).strip().lower()
        for item in items
        for term in (item.get("matched_terms") or [])
        if str(term).strip()
    }
    coverage_score = _ratio_score(len(matched_terms), len({t.lower() for t in terms}))

    authors = {
        ((item.get("author") or {}).get("handle") or "").strip().lower()
        for item in items
        if ((item.get("author") or {}).get("handle") or "").strip()
    }
    urls = {str(item.get("url") or "").strip() for item in items if str(item.get("url") or "").strip()}
    author_score = _ratio_score(len(authors), item_count)
    url_score = _ratio_score(len(urls), item_count)
    diversity_parts = [s for s in (author_score, url_score) if s is not None]
    diversity_score = round(sum(diversity_parts) / len(diversity_parts), 2) if diversity_parts else None

    negative_terms = _negative_terms(getattr(args, "language", None))
    contradiction_count = 0
    for item in items:
        text = str(item.get("text") or "").lower()
        if any(term.lower() in text for term in negative_terms):
            contradiction_count += 1

    notes: List[str] = []
    if coverage_score is not None and coverage_score < 0.5:
        notes.append("Accepted items cover only a small part of the structured search terms.")
    if diversity_score is not None and diversity_score < 0.5:
        notes.append("Accepted items are concentrated in a small set of authors or URLs.")
    if contradiction_count == 0 and item_count:
        notes.append("No accepted item contains obvious contradiction or negative-validation terms.")

    env["search_quality"] = {
        "coverage_score": coverage_score,
        "diversity_score": diversity_score,
        "novelty_score": None,
        "contradiction_count": contradiction_count,
        "notes": notes,
    }

    candidates: List[Dict[str, Any]] = []
    limitation_codes = {lim.get("code") for lim in env.get("limitations", [])}
    recommended = [r.get("term") for r in env.get("queries_built", {}).get("recommended_excludes", []) if r.get("term")]

    if "QUERY_TOO_BROAD" in limitation_codes or recommended:
        suggested: Dict[str, Any] = {}
        if recommended:
            suggested["exclude"] = recommended[:5]
        suggested["exclude_types"] = ["retweet", "reply"]
        if getattr(args, "period", None):
            suggested["period"] = getattr(args, "period")
        candidates.append({
            "kind": "narrow",
            "reason": "Current search produced broad or noisy results; use recommended excludes and remove low-context post types.",
            "suggested_fields": suggested,
            "expected_observation": "A higher share of accepted items should contain concrete first-party observations.",
        })

    if "RESULTS_INSUFFICIENT" in limitation_codes or item_count <= 3:
        suggested = {
            "period": "30d" if getattr(args, "period", None) in {"24h", "7d"} else getattr(args, "period", None),
        }
        if terms:
            suggested["any_of"] = [terms[: min(5, len(terms))]]
        candidates.append({
            "kind": "broaden",
            "reason": "Accepted results are too few; widen time range or loosen exact terms before judging the topic.",
            "suggested_fields": {k: v for k, v in suggested.items() if v},
            "expected_observation": "More posts should appear without changing the investigation purpose.",
        })

    if contradiction_count == 0 and item_count:
        suggested = {"any_of": [negative_terms[:4]]}
        if getattr(args, "period", None):
            suggested["period"] = getattr(args, "period")
        candidates.append({
            "kind": "contradict",
            "reason": "Current accepted items do not include obvious counterexamples or negative-validation language.",
            "suggested_fields": suggested,
            "expected_observation": "Posts that weaken, qualify, or contradict the current interpretation should become visible.",
        })

    expand_item = next(
        (
            item for item in items
            if ((item.get("metrics") or {}).get("replies") or 0) + ((item.get("metrics") or {}).get("quotes") or 0) >= 10
        ),
        None,
    )
    if expand_item:
        candidates.append({
            "kind": "expand",
            "reason": "At least one accepted post has enough replies or quotes to inspect surrounding conversation context.",
            "suggested_fields": {"id": expand_item.get("url") or expand_item.get("source_id")},
            "expected_observation": "Replies, quotes, or thread context should clarify whether the post represents a broader conversation.",
        })

    env["next_query_candidates"] = candidates[:5]


def _finalize(env: Dict[str, Any], args: argparse.Namespace,
              raw_items: List[Dict[str, Any]],
              structured: Optional[qb.StructuredQuery] = None) -> None:
    """Apply noise filters, scoring, representative pick, and update envelope."""
    if structured is not None:
        matched_terms_for = _normalize.detect_matched_terms(raw_items, structured)
    else:
        matched_terms_for = None

    opts = _noise_options(args)
    # account / expand / lookup pass structured=None — there are no search
    # terms to "match", so dropping items by require_matched_terms would
    # wipe out a legitimate account timeline or thread. Disable that filter
    # for term-less scopes.
    if structured is None:
        opts.require_matched_terms = False
    kept, excluded, recommended = _noise.apply_filters(raw_items, opts, matched_terms_for)

    purpose = getattr(args, "purpose", None) or "market_research"
    if structured is not None:
        from_accounts = structured.from_accounts
        mentions = [m.lstrip("@") for m in structured.mentions]
    else:
        from_accounts = []
        mentions = []
    scored = _normalize.score_items(kept, purpose=purpose, period=getattr(args, "period", None),
                                    from_accounts=from_accounts, mentions=mentions)
    limit = max(1, int(getattr(args, "limit", 20)))
    picked = _normalize.representative_pick(scored, limit=limit)
    _normalize.attach_why_selected(picked, purpose=purpose)

    env["items"] = picked
    env["excluded_summary"] = {
        "total_excluded": sum(r.count for r in excluded),
        "by_reason": [r.to_dict() for r in excluded],
    }
    env["queries_built"]["recommended_excludes"] = recommended

    total_in = len(raw_items)
    total_kept = len(picked)
    if total_in >= 50 and total_kept <= max(3, limit // 4):
        _add_limitation(env, "QUERY_TOO_BROAD", "Many results were filtered out as noise. "
                                                "Consider adding --exclude terms from queries_built.recommended_excludes.",
                        recoverable=True, scope="discovery")
    if total_kept == 0 and total_in <= 3:
        _add_limitation(env, "RESULTS_INSUFFICIENT",
                        "Too few results. Consider relaxing --exclude, widening --period, "
                        "or replacing --phrases with --keywords.",
                        recoverable=True, scope="discovery")
    _add_search_guidance(env, args, structured)


# ----- Subcommand handlers -----


def handle_diagnose(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("diagnose", args)
    _resolve_credentials(env)
    if not env["credentials"]["bird"]["available"] and not env["credentials"]["x_api"]["available"]:
        env["next_human_actions"].append(
            "Configure AUTH_TOKEN / CT0 (bird) or X_BEARER_TOKEN (X API) in the aachat env provider, run `aachat up`, then re-run."
        )
    return env


def handle_search(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("search", args)
    tool_pref = getattr(args, "tool", "auto")
    collect_with_api = bool(getattr(args, "collect_with_api", False))

    if tool_pref == "bird":
        _resolve_credentials(env, check_bird=True, check_api=False)
    elif tool_pref == "x_api":
        _resolve_credentials(env, check_bird=False, check_api=True)
    elif collect_with_api:
        _resolve_credentials(env, check_bird=True, check_api=True)
    else:
        _resolve_credentials(env, check_bird=True, check_api=False)
        if not env["credentials"]["bird"]["available"]:
            _resolve_credentials(env, check_bird=False, check_api=True)

    bird_ok = env["credentials"]["bird"]["available"]
    api_ok = env["credentials"]["x_api"]["available"]

    try:
        structured = _structured_from_args(args)
        built = qb.build(structured)
    except ValueError as exc:
        _add_limitation(env, "INVALID_INPUT", str(exc), recoverable=False, scope="discovery")
        return env

    env["queries_built"]["bird"] = built["bird"]
    env["queries_built"]["x_api"] = built["x_api"]
    env["queries_built"]["differences"] = built["differences"]
    x_api_params = built.get("x_api_params") or {}

    raw_items: List[Dict[str, Any]] = []
    max_fetch = int(getattr(args, "max_fetch", None) or max(int(args.limit) * 3, 30))

    use_bird = tool_pref in ("auto", "bird") and bird_ok
    use_api = (
        (tool_pref == "x_api" and api_ok)
        or (tool_pref == "auto" and api_ok and (collect_with_api or not use_bird))
    )

    if not use_bird and not use_api:
        env["next_human_actions"].append(
            "Neither bird nor X API is available. Use `site:x.com <query>` Google/Bing search "
            "to discover URLs, then run `search.py lookup --id <url>` to confirm existence."
        )
        return env

    # Discovery stage (bird preferred).
    if use_bird:
        discovery_n = min(30, max(5, int(args.limit) // 2)) if use_api else max_fetch
        res = _bird.search(built["bird"], limit=discovery_n, max_fetch=discovery_n)
        env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
        if res.ok and isinstance(res.data, list):
            normalized = [_bird.normalize_tweet(t, stage="discovery") for t in res.data]
            normalized = [n for n in normalized if n]
            raw_items.extend(normalized)
            _push_query_tried(env, "discovery", "bird", built["bird"], len(normalized), res.elapsed_ms)
        elif res.error:
            _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "discovery")

    # Collection stage (X API for reproducibility).
    if use_api:
        if not _period_supported_by_recent_api(getattr(args, "period", None)):
            _add_recent_api_period_limitation(env, "collection")
            use_api = False

    if use_api:
        sort_order = x_api_params.get("sort_order")
        res = _x_api.recent_search(
            query=built["x_api"],
            max_results=min(max_fetch, 100),
            start_time=x_api_params.get("start_time"),
            end_time=x_api_params.get("end_time"),
            sort_order=sort_order,
        )
        env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + min(max_fetch, 100)
        if res.ok:
            api_items = _x_api.items_from_response(res.data, stage="collection")
            raw_items.extend(api_items)
            _push_query_tried(env, "collection", "x_api", built["x_api"], len(api_items),
                              res.elapsed_ms, next_token=res.next_token)
        elif res.error:
            _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "collection")

    _finalize(env, args, raw_items, structured=structured)
    return env


def handle_expand(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("expand", args)
    tool_pref = getattr(args, "tool", "auto")

    if tool_pref == "bird":
        _resolve_credentials(env, check_bird=True, check_api=False)
    elif tool_pref == "x_api":
        _resolve_credentials(env, check_bird=False, check_api=True)
    else:
        _resolve_credentials(env, check_bird=True, check_api=False)
        if not env["credentials"]["bird"]["available"]:
            _resolve_credentials(env, check_bird=False, check_api=True)

    bird_ok = env["credentials"]["bird"]["available"]
    api_ok = env["credentials"]["x_api"]["available"]

    raw_items: List[Dict[str, Any]] = []
    post_id = args.id

    use_bird = tool_pref in ("auto", "bird") and bird_ok
    use_api = tool_pref in ("auto", "x_api") and api_ok and not use_bird

    if use_bird:
        if getattr(args, "include_thread", True):
            res = _bird.thread(post_id)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if res.ok and isinstance(res.data, list):
                normalized = [_bird.normalize_tweet(t, stage="single") for t in res.data]
                normalized = [n for n in normalized if n]
                raw_items.extend(normalized)
                _push_query_tried(env, "single", "bird", f"thread:{post_id}", len(normalized), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "expand")

        if getattr(args, "include_replies", True):
            res = _bird.replies(post_id, max_pages=int(getattr(args, "replies_max_pages", 2)))
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if res.ok and isinstance(res.data, list):
                normalized = [_bird.normalize_tweet(t, stage="single") for t in res.data]
                normalized = [n for n in normalized if n]
                raw_items.extend(normalized)
                _push_query_tried(env, "single", "bird", f"replies:{post_id}", len(normalized), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "expand")
    elif use_api:
        # Need conversation_id; resolve via post lookup first.
        lookup = _x_api.post_lookup([post_id])
        env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + 1
        if lookup.ok and isinstance(lookup.data, dict):
            data_arr = lookup.data.get("data") or []
            conv_id = data_arr[0].get("conversation_id") if data_arr else None
            if conv_id:
                res = _x_api.conversation_search(conv_id, max_results=100)
                if res.ok:
                    api_items = _x_api.items_from_response(res.data, stage="single")
                    raw_items.extend(api_items)
                    _push_query_tried(env, "single", "x_api", f"conversation_id:{conv_id}",
                                      len(api_items), res.elapsed_ms, next_token=res.next_token)
                    env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + 100
                elif res.error:
                    _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "expand")
        elif lookup.error:
            _add_limitation(env, lookup.error.code, lookup.error.message, lookup.error.recoverable, "expand")
    else:
        env["next_human_actions"].append(
            "Neither bird nor X API is available for expand. Configure AUTH_TOKEN / CT0 or X_BEARER_TOKEN in the aachat env provider, run `aachat up`, then re-run."
        )

    _finalize(env, args, raw_items, structured=None)
    return env


def handle_account(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("account", args)
    tool_pref = getattr(args, "tool", "auto")

    if tool_pref == "bird":
        _resolve_credentials(env, check_bird=True, check_api=False)
    elif tool_pref == "x_api":
        _resolve_credentials(env, check_bird=False, check_api=True)
    else:
        _resolve_credentials(env, check_bird=True, check_api=False)
        if not env["credentials"]["bird"]["available"]:
            _resolve_credentials(env, check_bird=False, check_api=True)

    bird_ok = env["credentials"]["bird"]["available"]
    api_ok = env["credentials"]["x_api"]["available"]

    raw_items: List[Dict[str, Any]] = []
    handle = args.handle

    use_bird = tool_pref in ("auto", "bird") and bird_ok

    if use_bird:
        n_tweets = int(getattr(args, "include_tweets", 50))
        if n_tweets > 0:
            res = _bird.user_tweets(handle, n=n_tweets)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if res.ok and isinstance(res.data, list):
                normalized = [_bird.normalize_tweet(t, stage="single") for t in res.data]
                normalized = [n for n in normalized if n]
                raw_items.extend(normalized)
                _push_query_tried(env, "single", "bird", f"user-tweets:{handle}", len(normalized), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "account")

        n_mentions = int(getattr(args, "include_mentions", 30))
        if n_mentions > 0:
            res = _bird.user_mentions(handle, n=n_mentions)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if res.ok and isinstance(res.data, list):
                normalized = [_bird.normalize_tweet(t, stage="single") for t in res.data]
                normalized = [n for n in normalized if n]
                raw_items.extend(normalized)
                _push_query_tried(env, "single", "bird", f"mentions:{handle}", len(normalized), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "account")
        if getattr(args, "include_profile", True) and tool_pref == "auto":
            _enrich_items_with_api_profile(env, handle, raw_items)
    elif api_ok and tool_pref in ("auto", "x_api"):
        # API fallback: resolve user id then fetch timeline / mentions.
        user_res = _x_api.user_by_username(handle.lstrip("@"))
        if user_res.ok and isinstance(user_res.data, dict):
            data_obj = user_res.data.get("data") or {}
            user_id = data_obj.get("id")
            if user_id:
                tw_res = _x_api.user_tweets(user_id, max_results=int(getattr(args, "include_tweets", 50)))
                env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + int(getattr(args, "include_tweets", 50))
                if tw_res.ok:
                    api_items = _x_api.items_from_response(tw_res.data, stage="single")
                    raw_items.extend(api_items)
                    _push_query_tried(env, "single", "x_api", f"user-tweets:{handle}",
                                      len(api_items), tw_res.elapsed_ms, next_token=tw_res.next_token)
                elif tw_res.error:
                    _add_limitation(env, tw_res.error.code, tw_res.error.message, tw_res.error.recoverable, "account")
                mn_res = _x_api.user_mentions(user_id, max_results=int(getattr(args, "include_mentions", 30)))
                env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + int(getattr(args, "include_mentions", 30))
                if mn_res.ok:
                    api_items = _x_api.items_from_response(mn_res.data, stage="single")
                    raw_items.extend(api_items)
                    _push_query_tried(env, "single", "x_api", f"mentions:{handle}",
                                      len(api_items), mn_res.elapsed_ms, next_token=mn_res.next_token)
                elif mn_res.error:
                    _add_limitation(env, mn_res.error.code, mn_res.error.message, mn_res.error.recoverable, "account")
        elif user_res.error:
            _add_limitation(env, user_res.error.code, user_res.error.message, user_res.error.recoverable, "account")
    else:
        env["next_human_actions"].append(
            "Neither bird nor X API is available for account. Configure AUTH_TOKEN / CT0 or X_BEARER_TOKEN in the aachat env provider, run `aachat up`, then re-run."
        )

    _finalize(env, args, raw_items, structured=None)
    return env


def handle_counts(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("counts", args)
    tool_pref = getattr(args, "tool", "auto")

    if tool_pref == "bird":
        _resolve_credentials(env, check_bird=False, check_api=False)
        _add_limitation(env, "BIRD_FEATURE_NOT_SUPPORTED",
                        "bird does not support counts. Use --tool=auto or --tool=x_api.",
                        recoverable=False, scope="counts")
        return env
    _resolve_credentials(env, check_bird=False, check_api=True)
    api_ok = env["credentials"]["x_api"]["available"]
    if not api_ok:
        env["next_human_actions"].append(
            "counts requires X API. Configure X_BEARER_TOKEN in the aachat env provider, run `aachat up`, then re-run."
        )
        return env

    try:
        structured = _structured_from_args(args)
        built = qb.build(structured)
    except ValueError as exc:
        _add_limitation(env, "INVALID_INPUT", str(exc), recoverable=False, scope="counts")
        return env

    env["queries_built"]["bird"] = built["bird"]
    env["queries_built"]["x_api"] = built["x_api"]
    env["queries_built"]["differences"] = built["differences"]
    x_api_params = built.get("x_api_params") or {}

    if not _period_supported_by_recent_api(getattr(args, "period", None)):
        _add_recent_api_period_limitation(env, "counts")
        return env

    res = _x_api.counts_recent(
        query=built["x_api"],
        granularity=args.granularity,
        start_time=x_api_params.get("start_time"),
        end_time=x_api_params.get("end_time"),
    )
    if res.ok and isinstance(res.data, dict):
        series = []
        for row in res.data.get("data") or []:
            if isinstance(row, dict):
                series.append({
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "count": int(row.get("tweet_count") or 0),
                })
        env["counts"] = series
        _push_query_tried(env, "collection", "x_api", built["x_api"], len(series), res.elapsed_ms)
    elif res.error:
        _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "counts")

    return env


def handle_lookup(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("lookup", args)
    tool_pref = getattr(args, "tool", "auto")

    if tool_pref == "bird":
        _resolve_credentials(env, check_bird=True, check_api=False)
    elif tool_pref == "x_api":
        _resolve_credentials(env, check_bird=False, check_api=True)
    else:
        _resolve_credentials(env, check_bird=True, check_api=False)
        if not env["credentials"]["bird"]["available"]:
            _resolve_credentials(env, check_bird=False, check_api=True)

    bird_ok = env["credentials"]["bird"]["available"]
    api_ok = env["credentials"]["x_api"]["available"]

    raw_items: List[Dict[str, Any]] = []
    ids = _flatten_csv(args.id or [])

    use_bird = tool_pref in ("auto", "bird") and bird_ok

    for post_id in ids:
        if use_bird:
            res = _bird.read_post(post_id)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if res.ok and isinstance(res.data, list):
                normalized = [_bird.normalize_tweet(t, stage="lookup") for t in res.data]
                normalized = [n for n in normalized if n]
                raw_items.extend(normalized)
                _push_query_tried(env, "lookup", "bird", f"read:{post_id}", len(normalized), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "lookup")
        elif api_ok and tool_pref in ("auto", "x_api"):
            res = _x_api.post_lookup([post_id])
            env["usage"]["x_api_post_reads"] = env["usage"].get("x_api_post_reads", 0) + 1
            if res.ok:
                api_items = _x_api.items_from_response(res.data, stage="lookup")
                raw_items.extend(api_items)
                _push_query_tried(env, "lookup", "x_api", f"tweets/ids={post_id}", len(api_items), res.elapsed_ms)
            elif res.error:
                _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "lookup")
        else:
            env["next_human_actions"].append(
                "lookup requires either bird (AUTH_TOKEN / CT0) or X API (X_BEARER_TOKEN). "
                "Configure the needed env in the aachat env provider, run `aachat up`, then re-run."
            )
            break

    env["items"] = raw_items
    return env


def handle_trend(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope("trend", args)
    _resolve_credentials(env, check_bird=True, check_api=False)
    bird_ok = env["credentials"]["bird"]["available"]
    if not bird_ok:
        _add_limitation(env, "BIRD_AUTH_MISSING",
                        "trend requires bird. Configure AUTH_TOKEN / CT0 in the aachat env provider, run `aachat up`, then re-run.",
                        recoverable=False, scope="trend")
        return env

    trends: List[Dict[str, Any]] = []

    def _trend_name(n: Dict[str, Any]) -> str:
        # bird 0.8.x uses `headline`; older / alternate shapes may use title/name/query.
        return str(n.get("headline") or n.get("title") or n.get("name") or n.get("query") or "").strip()

    def _trend_volume(n: Dict[str, Any]) -> Any:
        return n.get("postCount") or n.get("tweet_volume") or n.get("volume")

    res_news = _bird.news_with_tweets()
    env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
    if res_news.ok and res_news.data:
        items = res_news.data if isinstance(res_news.data, list) else (
            res_news.data.get("items") or res_news.data.get("news") or [])
        for n in items:
            if not isinstance(n, dict):
                continue
            trends.append({
                "name": _trend_name(n),
                "url": n.get("url"),
                "volume": _trend_volume(n),
                "category": n.get("category") or "news",
            })
        _push_query_tried(env, "single", "bird", "news --with-tweets", len(items), res_news.elapsed_ms)
    elif res_news.error:
        _add_limitation(env, res_news.error.code, res_news.error.message, res_news.error.recoverable, "trend")

    res_trend = _bird.trending()
    env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
    if res_trend.ok and res_trend.data:
        items = res_trend.data if isinstance(res_trend.data, list) else (
            res_trend.data.get("trends") or res_trend.data.get("items") or [])
        for n in items:
            if not isinstance(n, dict):
                continue
            trends.append({
                "name": _trend_name(n),
                "url": n.get("url"),
                "volume": _trend_volume(n),
                "category": n.get("category") or "trending",
            })
        _push_query_tried(env, "single", "bird", "trending", len(items), res_trend.elapsed_ms)
    elif res_trend.error:
        _add_limitation(env, res_trend.error.code, res_trend.error.message, res_trend.error.recoverable, "trend")

    trends = [t for t in trends if t.get("name")]
    limit = int(getattr(args, "limit", 20))
    env["trends"] = trends[:limit]
    return env


def _resolve_account(env: Dict[str, Any], token: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a seed/candidate token to (user_id, handle).

    A purely-numeric token is treated as a user id directly (no handle known).
    Otherwise it is a @handle resolved to an id via bird. Resolution failures
    are recorded as recoverable limitations and return (None, handle).
    """
    raw = str(token).strip()
    if not raw:
        return None, None
    if raw.lstrip("@").isdigit():
        return raw.lstrip("@"), None
    handle = raw if raw.startswith("@") else f"@{raw}"
    res = _bird.resolve_user_id(handle)
    env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
    if not res.ok:
        if res.error:
            _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "graph")
        return None, handle
    if not res.data:
        _add_limitation(env, "RESULTS_INSUFFICIENT",
                        f"Could not resolve {handle} to a user id (no recent tweets visible).",
                        recoverable=True, scope="graph")
        return None, handle
    return str(res.data), handle


def _candidate_record(uid: str, handle: Optional[str], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = profile or {}
    h = p.get("handle") or handle
    url = p.get("url")
    if not url and h:
        url = f"https://x.com/{h.lstrip('@')}"
    return {
        "id": uid,
        "handle": h,
        "name": p.get("name"),
        "url": url,
        "bio": p.get("bio"),
        "followers_count": p.get("followers_count"),
        "following_count": p.get("following_count"),
        "blue_verified": p.get("blue_verified"),
        "created_at": p.get("created_at"),
        "overlap_count": 0,
        "matched_seeds": [],
        "coverage": None,
    }


def handle_graph(args: argparse.Namespace) -> Dict[str, Any]:
    """Rank accounts by how many seed accounts they follow (follow-overlap).

    Two modes share one scoring idea: overlap(account.following, seeds).

    - score:  for each --candidate, fetch its following list (bounded) and
              count how many seeds it contains. Seeds may be any size because
              we never enumerate a seed's followers.
    - expand: enumerate each seed's followers (use niche seeds; page-capped)
              and tally per-account appearance counts. Discovers new accounts
              and scores overlap in one pass without per-candidate fetches.

    Returns observable facts only (overlap_count, matched_seeds, profile).
    Any "is this a good recruit" judgment belongs in x-search-insight.
    """
    env = _envelope("graph", args)
    _resolve_credentials(env, check_bird=True, check_api=False)
    if not env["credentials"]["bird"]["available"]:
        env["next_human_actions"].append(
            "graph requires bird (AUTH_TOKEN / CT0). Configure them in the aachat env provider, run `aachat up`, then re-run."
        )
        return env

    seeds_in = _flatten_csv(getattr(args, "seed", []))
    cands_in = _flatten_csv(getattr(args, "candidate", []))
    if not seeds_in:
        _add_limitation(env, "INVALID_INPUT", "graph requires at least one --seed.", recoverable=False, scope="graph")
        return env

    mode = getattr(args, "mode", "auto")
    if mode == "auto":
        mode = "score" if cands_in else "expand"
    env["graph_mode"] = mode

    page_size = max(1, min(int(getattr(args, "page_size", 100) or 100), 100))
    max_following_pages = max(1, int(getattr(args, "max_following_pages", 10) or 10))
    max_follower_pages = max(1, int(getattr(args, "max_follower_pages", 10) or 10))
    min_overlap = max(1, int(getattr(args, "min_overlap", 1) or 1))
    limit = max(1, int(getattr(args, "limit", 20) or 20))

    # Resolve seeds once. seed_ids is the membership set every overlap is measured against.
    seed_records: List[Dict[str, Any]] = []
    seed_ids: set = set()
    seed_handle_by_id: Dict[str, str] = {}
    for token in seeds_in:
        sid, shandle = _resolve_account(env, token)
        rec = {"input": token, "id": sid, "handle": shandle, "coverage": None}
        seed_records.append(rec)
        if sid:
            seed_ids.add(sid)
            seed_handle_by_id[sid] = shandle or token
    env["seeds"] = seed_records
    if not seed_ids:
        _add_limitation(env, "RESULTS_INSUFFICIENT",
                        "Could not resolve any seed to a user id. Check the handles and retry.",
                        recoverable=True, scope="graph")
        return env

    candidates_by_id: Dict[str, Dict[str, Any]] = {}

    if mode == "score":
        if not cands_in:
            _add_limitation(env, "INVALID_INPUT", "score mode requires at least one --candidate.",
                            recoverable=False, scope="graph")
            return env
        for token in cands_in:
            cid, chandle = _resolve_account(env, token)
            if not cid:
                continue
            res = _bird.following(cid, max_pages=max_following_pages, page_size=page_size)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if not res.ok:
                if res.error:
                    _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "graph")
                continue
            following_users = res.data or []
            following_ids = {str(u.get("id")) for u in following_users if isinstance(u, dict) and u.get("id")}
            matched = sorted(seed_ids & following_ids)
            partial = len(following_users) >= max_following_pages * page_size
            rec = _candidate_record(cid, chandle, profile=None)
            rec["overlap_count"] = len(matched)
            rec["matched_seeds"] = [{"id": mid, "handle": seed_handle_by_id.get(mid)} for mid in matched]
            rec["following_sampled"] = len(following_ids)
            rec["coverage"] = "partial" if partial else "full"
            _push_query_tried(env, "graph", "bird", f"following:{chandle or cid}",
                              len(following_users), res.elapsed_ms)
            candidates_by_id[cid] = rec
    else:  # expand
        for rec in seed_records:
            sid = rec["id"]
            if not sid:
                continue
            res = _bird.followers(sid, max_pages=max_follower_pages, page_size=page_size)
            env["usage"]["bird_calls"] = env["usage"].get("bird_calls", 0) + 1
            if not res.ok:
                if res.error:
                    _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, "graph")
                continue
            follower_users = res.data or []
            partial = len(follower_users) >= max_follower_pages * page_size
            rec["followers_sampled"] = len(follower_users)
            rec["coverage"] = "partial" if partial else "full"
            if partial:
                _add_limitation(env, "GRAPH_PARTIAL_COVERAGE",
                                f"Hit the follower page cap for {rec.get('handle') or sid}; only the first "
                                f"{len(follower_users)} followers were tallied. Overlap may be undercounted. "
                                f"Use a more niche seed or raise --max-follower-pages.",
                                recoverable=True, scope="graph")
            for u in follower_users:
                nu = _bird.normalize_user(u)
                uid = nu.get("id")
                if not uid:
                    continue
                cand = candidates_by_id.get(uid)
                if cand is None:
                    cand = _candidate_record(uid, nu.get("handle"), profile=nu)
                    candidates_by_id[uid] = cand
                cand["overlap_count"] += 1
                cand["matched_seeds"].append({"id": sid, "handle": rec.get("handle") or rec.get("input")})
            _push_query_tried(env, "graph", "bird", f"followers:{rec.get('handle') or sid}",
                              len(follower_users), res.elapsed_ms)

    min_followers = getattr(args, "min_followers", None)
    max_followers = getattr(args, "max_followers", None)

    def _band_ok(c: Dict[str, Any]) -> bool:
        f = c.get("followers_count")
        if min_followers is not None and (f is None or f < min_followers):
            return False
        if max_followers is not None and (f is None or f > max_followers):
            return False
        return True

    cand_list = [
        c for c in candidates_by_id.values()
        if c.get("overlap_count", 0) >= min_overlap and c.get("id") not in seed_ids and _band_ok(c)
    ]
    cand_list.sort(key=lambda c: (c.get("overlap_count", 0), c.get("followers_count") or 0), reverse=True)

    env["candidates"] = cand_list[:limit]
    env["graph_summary"] = {
        "mode": mode,
        "seed_count": len(seed_ids),
        "candidate_pool": len(candidates_by_id),
        "returned": len(env["candidates"]),
        "min_overlap": min_overlap,
    }
    if not env["candidates"]:
        _add_limitation(env, "RESULTS_INSUFFICIENT",
                        "No account met the min-overlap threshold. In expand mode use more niche seeds or "
                        "raise --max-follower-pages; in score mode verify the candidate handles and lower --min-overlap.",
                        recoverable=True, scope="graph")
    return env


HANDLERS = {
    "diagnose": handle_diagnose,
    "search": handle_search,
    "expand": handle_expand,
    "account": handle_account,
    "counts": handle_counts,
    "lookup": handle_lookup,
    "trend": handle_trend,
    "graph": handle_graph,
}


def _to_markdown(env: Dict[str, Any]) -> str:
    """Minimal Markdown rendering for human eyes. JSON remains canonical."""
    lines: List[str] = []
    lines.append(f"# x-search result ({env['tool']})")
    lines.append("")
    lines.append(f"- fetched_at: {env['fetched_at']}")
    if env.get("purpose"):
        lines.append(f"- purpose: {env['purpose']}")
    if env.get("language"):
        lines.append(f"- language: {env['language']}")
    if env.get("period"):
        lines.append(f"- period: {env['period']}")
    lines.append(f"- bird: available={env['credentials']['bird']['available']}")
    lines.append(f"- x_api: available={env['credentials']['x_api']['available']}")

    if env.get("queries_built", {}).get("bird") or env.get("queries_built", {}).get("x_api"):
        lines.append("\n## queries_built\n")
        qb_ = env["queries_built"]
        if qb_.get("bird"):
            lines.append(f"- bird: `{qb_['bird']}`")
        if qb_.get("x_api"):
            lines.append(f"- x_api: `{qb_['x_api']}`")

    if env.get("items"):
        lines.append("\n## items\n")
        for i, it in enumerate(env["items"], 1):
            url = it.get("url") or ""
            handle = (it.get("author") or {}).get("handle") or ""
            text = (it.get("text") or "").replace("\n", " ")
            metrics = it.get("metrics") or {}
            lines.append(f"### {i}. {handle}")
            if url:
                lines.append(f"- URL: {url}")
            if it.get("published_at"):
                lines.append(f"- published_at: {it['published_at']}")
            lines.append(f"- metrics: likes={metrics.get('likes')}, reposts={metrics.get('reposts')}, "
                         f"replies={metrics.get('replies')}, quotes={metrics.get('quotes')}, views={metrics.get('views')}")
            if it.get("why_selected"):
                lines.append(f"- why_selected: {it['why_selected']}")
            lines.append(f"- text: {text[:200]}")
            lines.append("")

    if env.get("candidates"):
        summary = env.get("graph_summary") or {}
        lines.append(f"\n## candidates (graph / {summary.get('mode')})\n")
        lines.append(f"- seed_count={summary.get('seed_count')}, candidate_pool={summary.get('candidate_pool')}, "
                     f"returned={summary.get('returned')}, min_overlap={summary.get('min_overlap')}")
        lines.append("")
        for i, c in enumerate(env["candidates"], 1):
            matched = ", ".join(str(m.get("handle") or m.get("id")) for m in (c.get("matched_seeds") or []))
            lines.append(f"### {i}. {c.get('handle') or c.get('id')} — follows {c.get('overlap_count')} seed(s)")
            if c.get("url"):
                lines.append(f"- URL: {c['url']}")
            lines.append(f"- followers={c.get('followers_count')}, following={c.get('following_count')}, "
                         f"blue_verified={c.get('blue_verified')}, created_at={c.get('created_at')}")
            lines.append(f"- matched_seeds: {matched}")
            if c.get("bio"):
                lines.append(f"- bio: {str(c['bio']).replace(chr(10), ' ')[:200]}")
            lines.append("")

    if env.get("limitations"):
        lines.append("\n## limitations\n")
        for lim in env["limitations"]:
            lines.append(f"- [{lim['code']}] (recoverable={lim['recoverable']}) {lim['message']}")

    if env.get("next_human_actions"):
        lines.append("\n## next_human_actions\n")
        for a in env["next_human_actions"]:
            lines.append(f"- {a}")

    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    handler = HANDLERS.get(args.subcommand)
    if handler is None:
        parser.error(f"unknown subcommand: {args.subcommand}")
        return 2

    try:
        env = handler(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - defensive envelope
        env = _envelope(args.subcommand, args)
        _add_limitation(env, "BIRD_UNEXPECTED_ERROR" if "bird" in str(exc).lower() else "API_UNEXPECTED_ERROR",
                        f"Unexpected internal error: {type(exc).__name__}",
                        recoverable=False, scope="global")

    if getattr(args, "format", "json") == "markdown":
        sys.stdout.write(_to_markdown(env))
    else:
        json.dump(env, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
