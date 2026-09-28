#!/usr/bin/env python3
"""Fetch X bookmarks with bird and normalize them for bookmark deep research.

Secrets are read by bird from environment variables only:
- AUTH_TOKEN
- CT0

Secret values are never written to stdout, stderr, or files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_SKILLS_DIR = os.path.dirname(_SKILL_DIR)
_X_SEARCH_SCRIPTS = os.path.join(_SKILLS_DIR, "x-search", "scripts")
if _X_SEARCH_SCRIPTS not in sys.path:
    sys.path.insert(0, _X_SEARCH_SCRIPTS)

try:
    import _bird  # type: ignore[import-not-found]
except Exception as exc:  # pragma: no cover - import guard for standalone failures
    _bird = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _envelope(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "platform": "x",
        "tool": "bookmarks",
        "purpose": "bookmark_deep_research",
        "language": getattr(args, "language", None),
        "region": getattr(args, "region", None),
        "period": getattr(args, "period", None),
        "fetched_at": _now(),
        "credentials": {
            "bird": {"available": False, "checked_at": None, "user": None, "reason": None},
        },
        "bookmark_options": {
            "limit": args.limit,
            "all": args.fetch_all,
            "max_pages": args.max_pages,
            "folder_id": args.folder_id,
            "cursor": bool(args.cursor),
            "expand_root_only": args.expand_root_only,
            "author_chain": args.author_chain,
            "author_only": args.author_only,
            "full_chain_only": args.full_chain_only,
            "include_ancestor_branches": args.include_ancestor_branches,
            "include_parent": args.include_parent,
            "thread_meta": args.thread_meta,
            "sort_chronological": args.sort_chronological,
        },
        "queries_tried": [],
        "items": [],
        "bookmark_summary": {
            "total_returned": 0,
            "unique_authors": 0,
            "has_thread_metadata": False,
        },
        "usage": {"bird_calls": 0},
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


def _build_bird_args(args: argparse.Namespace) -> List[str]:
    bird_args = ["bookmarks"]
    if args.fetch_all:
        bird_args.append("--all")
        if args.max_pages is not None:
            bird_args.extend(["--max-pages", str(max(1, int(args.max_pages)))])
    else:
        bird_args.extend(["-n", str(max(1, int(args.limit)))])

    if args.folder_id:
        bird_args.extend(["--folder-id", args.folder_id])
    if args.cursor:
        bird_args.extend(["--cursor", args.cursor])

    flag_map = {
        "expand_root_only": "--expand-root-only",
        "author_chain": "--author-chain",
        "author_only": "--author-only",
        "full_chain_only": "--full-chain-only",
        "include_ancestor_branches": "--include-ancestor-branches",
        "include_parent": "--include-parent",
        "thread_meta": "--thread-meta",
        "sort_chronological": "--sort-chronological",
    }
    for attr, flag in flag_map.items():
        if getattr(args, attr):
            bird_args.append(flag)
    bird_args.append("--json")
    return bird_args


def _normalize_items(raw_tweets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw in raw_tweets:
        item = _bird.normalize_tweet(raw, stage="discovery")
        if not item:
            continue
        item["matched_terms"] = []
        item["why_selected"] = "認証ユーザーがブックマークした投稿。関心シグナルとして採用。"
        item["bookmark_context"] = _bookmark_context(raw)
        items.append(item)
    return items


def _bookmark_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "isThread",
        "threadPosition",
        "threadLength",
        "isRoot",
        "isAuthorChain",
        "bookmarkFolderId",
        "cursor",
    ]
    return {key: raw.get(key) for key in keys if key in raw}


def _summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    authors = {
        ((item.get("author") or {}).get("handle") or "")
        for item in items
        if (item.get("author") or {}).get("handle")
    }
    has_thread_metadata = any(bool(item.get("bookmark_context")) for item in items)
    return {
        "total_returned": len(items),
        "unique_authors": len(authors),
        "has_thread_metadata": has_thread_metadata,
    }


def handle_bookmarks(args: argparse.Namespace) -> Dict[str, Any]:
    env = _envelope(args)

    if _bird is None:
        _add_limitation(
            env,
            "BIRD_IMPORT_FAILED",
            f"Could not import x-search bird wrapper: {type(_IMPORT_ERROR).__name__}",
            recoverable=False,
            scope="bookmarks",
        )
        env["next_human_actions"].append("Ensure sibling skill `x-search` exists with `scripts/_bird.py`.")
        return env

    bird_args = _build_bird_args(args)
    res = _bird._run(bird_args, scope="bookmarks", timeout=max(60, int(args.timeout)))  # type: ignore[attr-defined]
    env["usage"]["bird_calls"] = 1
    env["queries_tried"].append({
        "stage": "discovery",
        "tool": "bird",
        "query": " ".join(bird_args[:-1]),
        "result_count": 0,
        "elapsed_ms": res.elapsed_ms,
    })

    if not res.ok:
        if res.error:
            _add_limitation(env, res.error.code, res.error.message, res.error.recoverable, res.error.scope)
            env["credentials"]["bird"]["checked_at"] = _now()
            env["credentials"]["bird"]["reason"] = res.error.code.lower()
            if res.error.code in {"BIRD_AUTH_MISSING", "BIRD_AUTH_EXPIRED"}:
                env["next_human_actions"].append(
                    "Configure AUTH_TOKEN / CT0 in the aachat env provider, run `aachat up`, then re-run."
                )
        return env

    env["credentials"]["bird"]["available"] = True
    env["credentials"]["bird"]["checked_at"] = _now()
    raw_tweets = _bird._extract_tweets(res.data)  # type: ignore[attr-defined]
    items = _normalize_items(raw_tweets)
    env["items"] = items
    env["bookmark_summary"] = _summarize(items)
    env["queries_tried"][0]["result_count"] = len(items)

    if not items:
        _add_limitation(
            env,
            "BOOKMARKS_EMPTY",
            "bird returned no bookmarked tweets for the requested scope.",
            recoverable=True,
            scope="bookmarks",
        )
    return env


def _to_markdown(env: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# X bookmark deep research input")
    lines.append("")
    lines.append(f"- fetched_at: {env.get('fetched_at')}")
    summary = env.get("bookmark_summary") or {}
    lines.append(f"- total_returned: {summary.get('total_returned')}")
    lines.append(f"- unique_authors: {summary.get('unique_authors')}")
    lines.append("")

    for i, item in enumerate(env.get("items") or [], 1):
        author = item.get("author") or {}
        metrics = item.get("metrics") or {}
        text = (item.get("text") or "").replace("\n", " ")
        lines.append(f"## bookmark-{i}: {author.get('handle') or 'unknown'}")
        lines.append(f"- URL: {item.get('url')}")
        lines.append(f"- published_at: {item.get('published_at')}")
        lines.append(
            "- metrics: "
            f"likes={metrics.get('likes')}, reposts={metrics.get('reposts')}, "
            f"replies={metrics.get('replies')}, quotes={metrics.get('quotes')}, views={metrics.get('views')}"
        )
        lines.append(f"- text: {text[:500]}")
        lines.append("")

    if env.get("limitations"):
        lines.append("## limitations")
        for lim in env["limitations"]:
            lines.append(f"- [{lim['code']}] recoverable={lim['recoverable']} {lim['message']}")
        lines.append("")

    if env.get("next_human_actions"):
        lines.append("## next_human_actions")
        for action in env["next_human_actions"]:
            lines.append(f"- {action}")
        lines.append("")

    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookmarks", description="Fetch X bookmarks via bird")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--all", dest="fetch_all", action="store_true")
    parser.add_argument("--max-pages", dest="max_pages", type=int)
    parser.add_argument("--folder-id", dest="folder_id")
    parser.add_argument("--cursor")
    parser.add_argument("--expand-root-only", dest="expand_root_only", action="store_true")
    parser.add_argument("--author-chain", dest="author_chain", action="store_true")
    parser.add_argument("--author-only", dest="author_only", action="store_true")
    parser.add_argument("--full-chain-only", dest="full_chain_only", action="store_true")
    parser.add_argument("--include-ancestor-branches", dest="include_ancestor_branches", action="store_true")
    parser.add_argument("--include-parent", dest="include_parent", action="store_true")
    parser.add_argument("--thread-meta", dest="thread_meta", action="store_true")
    parser.add_argument("--sort-chronological", dest="sort_chronological", action="store_true")
    parser.add_argument("--language")
    parser.add_argument("--region")
    parser.add_argument("--period")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    env = handle_bookmarks(args)

    if args.format == "markdown":
        sys.stdout.write(_to_markdown(env))
        sys.stdout.write("\n")
    else:
        json.dump(env, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
