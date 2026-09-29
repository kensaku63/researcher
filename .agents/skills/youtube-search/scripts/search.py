#!/usr/bin/env python3
"""YouTube research helper that returns normalized, report-ready JSON.

Backends:
- api: YouTube Data API v3 (needs YOUTUBE_API_KEY, exact metrics, costs quota)
- web: YouTube web endpoints (InnerTube search/next/player, RSS) with optional yt-dlp --deep (no key, no quota)
- auto: api when YOUTUBE_API_KEY is set, otherwise web; falls back to web on quota errors
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
INNERTUBE_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
INNERTUBE_NEXT_URL = "https://www.youtube.com/youtubei/v1/next?prettyPrint=false"
INNERTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
INNERTUBE_CLIENT_VERSION = "2.20260901.00.00"
OEMBED_URL = "https://www.youtube.com/oembed"
RSS_URL = "https://www.youtube.com/feeds/videos.xml"
DEFAULT_TIMEOUT = 20
MAX_TEXT = 300
MAX_COMMENT_TEXT = 300
MAX_TAGS = 15
SHORTS_MAX_SECONDS = 180
ENRICH_WORKERS = 8
MAX_SEARCH_PAGES = 8
DEFAULT_ENRICH_CAP = 30
BLOCK_PATTERNS = ("not a bot", "http error 429", "too many requests", "sign in to confirm")
CJK_LANG_PATTERNS = {
    "ja": r"[\u3040-\u30ff\u4e00-\u9fff]",
    "zh": r"[\u4e00-\u9fff]",
    "ko": r"[\uac00-\ud7af]",
}

# YouTube search "sp" protobuf values. YouTube removed the upload-date/rating sorts in 2025;
# relevance and popularity (view count) still work.
SP_SORT = {"relevance": 0, "viewCount": 3}
SP_TYPE = {"video": 1, "channel": 2, "playlist": 3, "shorts": 9}
SP_DURATION = {"short": 1, "long": 2, "medium": 3}
SP_UPLOAD_WINDOWS = [(1 / 24, 1), (1, 2), (7, 3), (31, 4), (366, 5)]  # (max days, value)

SORT_CHOICES = ["auto", "youtube", "quality", "views", "velocity", "recent", "engagement", "outlier", "vs_median"]
BASELINE_MIN_SAMPLES = 3
VIDEO_URL_PATTERN = r"youtu\.be/|youtube\.com/(watch|shorts/|live/|embed/)"


class SearchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return iso(dt.datetime.now(dt.timezone.utc))


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def message(code: str, text: str) -> dict[str, str]:
    return {"code": code, "message": text}


def truncate(value: str | None, limit: int = MAX_TEXT) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compact_counts(counter: Counter[str]) -> list[dict[str, int | str]]:
    return [{"reason": reason, "count": count} for reason, count in counter.items() if count]


def parse_video_id(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        if parsed.path == "/watch":
            candidate = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            parts = parsed.path.strip("/").split("/")
            candidate = parts[1] if len(parts) > 1 else ""
        else:
            candidate = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    else:
        candidate = ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate or "") else None


def parse_channel_input(value: str) -> dict[str, str | None]:
    raw = value.strip()
    parsed = urllib.parse.urlparse(raw)
    path = urllib.parse.unquote(parsed.path.strip("/"))
    if raw.startswith("@"):
        return {"input": raw, "handle": raw[1:], "channel_id": None, "url": None}
    if re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", raw):
        return {"input": raw, "handle": None, "channel_id": raw, "url": None}
    if "youtube.com" in parsed.netloc.lower():
        parts = path.split("/")
        if parts and parts[0].startswith("@"):
            return {"input": raw, "handle": parts[0][1:], "channel_id": None, "url": raw}
        if len(parts) >= 2 and parts[0] == "channel":
            return {"input": raw, "handle": None, "channel_id": parts[1], "url": raw}
    return {"input": raw, "handle": raw.lstrip("@"), "channel_id": None, "url": None}


def channel_page_url(parsed: dict[str, str | None]) -> str:
    if parsed["channel_id"]:
        return f"https://www.youtube.com/channel/{parsed['channel_id']}"
    return f"https://www.youtube.com/@{urllib.parse.quote(parsed['handle'] or '')}"


# ---------------------------------------------------------------------------
# Period helpers


def period_bounds(period: str) -> tuple[str | None, str | None]:
    period = period.strip()
    now = dt.datetime.now(dt.timezone.utc)
    if ".." in period:
        start, end = period.split("..", 1)
        return normalize_date(start, start_of_day=True), normalize_date(end, start_of_day=False)
    match = re.fullmatch(r"(\d+)([hdwmy])", period)
    if not match:
        return None, None
    amount = int(match.group(1))
    days = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365}[match.group(2)] * amount
    return iso(now - dt.timedelta(days=days)), None


def normalize_date(value: str, start_of_day: bool) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "T" in value:
        return value
    suffix = "T00:00:00Z" if start_of_day else "T23:59:59Z"
    return f"{value}{suffix}"


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def period_start_days_ago(period: str) -> float | None:
    start = parse_iso_datetime(period_bounds(period)[0])
    if not start:
        return None
    return (dt.datetime.now(dt.timezone.utc) - start).total_seconds() / 86400


def in_period(item: dict[str, Any], period: str) -> bool:
    """Exact dates are compared directly. Approximate dates ("3 weeks ago") are kept
    when the minimum possible age is inside the period; enrichment re-checks later."""
    published = parse_iso_datetime(item.get("published_at"))
    if not published:
        return True
    start_raw, end_raw = period_bounds(period)
    start = parse_iso_datetime(start_raw)
    end = parse_iso_datetime(end_raw)
    if item.get("published_at_precision") == "approx":
        min_age_days = item.get("_min_age_days")
        if start and min_age_days is not None:
            limit_days = (dt.datetime.now(dt.timezone.utc) - start).total_seconds() / 86400
            return min_age_days <= limit_days
        return True
    if start and published < start:
        return False
    if end and published > end:
        return False
    return True


RELATIVE_UNITS = [
    (("秒", "second"), 1 / 86400),
    (("分", "minute"), 1 / 1440),
    (("時間", "hour"), 1 / 24),
    (("日", "day"), 1),
    (("週間", "週", "week"), 7),
    (("か月", "ヶ月", "カ月", "month"), 30),
    (("年", "year"), 365),
]


def parse_relative_time(text: str | None) -> tuple[str | None, float | None]:
    """'3 週間前' / 'Streamed 2 days ago' -> (approx ISO datetime, minimum age in days)."""
    if not text:
        return None, None
    match = re.search(r"(\d+)\s*([^\d\s]+)", text.replace(",", ""))
    if not match:
        return None, None
    amount = int(match.group(1))
    unit_text = match.group(2).lower()
    for names, days in RELATIVE_UNITS:
        if any(unit_text.startswith(name) for name in names):
            age = amount * days
            published = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age)
            return iso(published), age
    return None, None


def parse_count_text(text: str | None) -> int | None:
    """'37,577回視聴' / '3.7万回視聴' / '1.2M views' / '登録者数 2.24万人' -> int."""
    if not text:
        return None
    if re.search(r"(視聴なし|no views)", text, re.IGNORECASE):
        return 0
    match = re.search(r"([\d.,]+)\s*(万|億|千|[KMB])?", text.replace(" ", " "))
    if not match:
        return None
    number = match.group(1).replace(",", "")
    try:
        value = float(number)
    except ValueError:
        return None
    multiplier = {"千": 1e3, "万": 1e4, "億": 1e8, "K": 1e3, "M": 1e6, "B": 1e9}.get(match.group(2) or "", 1)
    return int(round(value * multiplier))


def parse_duration_text(text: str | None) -> int | None:
    if not text or not re.fullmatch(r"\d+(:\d{1,2}){0,2}", text.strip()):
        return None
    seconds = 0
    for part in text.strip().split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def parse_iso_duration(value: str | None) -> int | None:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return None
    d, h, m, s = (int(x or 0) for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


# ---------------------------------------------------------------------------
# Result helpers


def api_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY")


def require_api_key() -> str:
    key = api_key()
    if not key:
        raise SearchError("missing_api_key", "YOUTUBE_API_KEY is required for this subcommand.")
    return key


def resolve_backend(args: argparse.Namespace) -> str:
    backend = getattr(args, "backend", "auto")
    if backend == "auto":
        return "api" if api_key() else "web"
    return backend


def queries_of(args: argparse.Namespace) -> list[str]:
    return [q for q in (getattr(args, "query", None) or []) if q and q.strip()]


def empty_result(args: argparse.Namespace, subcommand: str, tool: str) -> dict[str, Any]:
    queries = queries_of(args)
    return {
        "platform": "youtube",
        "purpose": getattr(args, "purpose", "social_marketing_research"),
        "subcommand": subcommand,
        "tool": tool,
        "query": " | ".join(queries) or None,
        "queries": queries,
        "language": getattr(args, "language", "ja"),
        "region": getattr(args, "region", "JP"),
        "period": getattr(args, "period", "30d"),
        "fetched_at": utc_now(),
        "quota_estimate": 0,
        "auto_routing": {
            "applied": False,
            "from_purpose": None,
            "chosen_subcommand": subcommand,
            "chosen_defaults": {},
        },
        "summary": {},
        "items": [],
        "comments": [],
        "trends": [],
        "queries_tried": [],
        "excluded_summary": [],
        "limitations": [],
        "next_human_actions": [],
    }


def query_log(endpoint: str, query: str, quota_cost: int, before: int | None, after: int | None, memo: str | None = None) -> dict[str, Any]:
    log = {
        "endpoint": endpoint,
        "query": query,
        "quota_cost": quota_cost,
        "result_count_before_filter": before,
        "result_count_after_filter": after,
    }
    if memo:
        log["memo"] = memo
    return log


def blank_metrics() -> dict[str, Any]:
    return {"views": None, "likes": None, "comments": None, "subscribers": None, "video_count": None}


def base_item(video_id: str, content_type: str = "video") -> dict[str, Any]:
    url = f"https://www.youtube.com/shorts/{video_id}" if content_type == "short" else f"https://www.youtube.com/watch?v={video_id}"
    return {
        "url": url,
        "source_id": video_id,
        "content_type": content_type,
        "author": None,
        "author_url": None,
        "channel_id": None,
        "channel_handle": None,
        "published_at": None,
        "published_at_precision": None,
        "published_text": None,
        "title": None,
        "text": None,
        "duration": None,
        "duration_seconds": None,
        "metrics": blank_metrics(),
        "matched_terms": [],
        "found_by_queries": [],
        "search_rank": None,
        "quality_score": None,
        "selection_filters": [],
        "why_selected": "",
        "limitations": [],
    }


# ---------------------------------------------------------------------------
# YouTube Data API backend


def youtube_get(endpoint: str, params: dict[str, Any], quota_cost: int) -> tuple[dict[str, Any], dict[str, Any]]:
    key = require_api_key()
    safe_params = {k: v for k, v in params.items() if v is not None and v != ""}
    safe_params["key"] = key
    url = f"{YOUTUBE_API_BASE}/{endpoint}"
    try:
        response = requests.get(url, params=safe_params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise SearchError("network_error", f"YouTube API request failed: {exc}") from exc
    if response.status_code == 403:
        code = "quota_exceeded" if "quota" in response.text.lower() else "forbidden"
        raise SearchError(code, "YouTube API returned 403. The key may lack access or quota.")
    if response.status_code == 404:
        raise SearchError("not_found", "YouTube API returned 404.")
    if response.status_code >= 400:
        code = "quota_exceeded" if "quota" in response.text.lower() else "api_error"
        raise SearchError(code, f"YouTube API returned HTTP {response.status_code}.")
    query = str(params.get("q") or params.get("id") or params.get("channelId") or params.get("playlistId") or "")
    return response.json(), query_log(endpoint, query, quota_cost, None, None)


def item_from_api_video(video: dict[str, Any], channel_metrics: dict[str, dict[str, int | None]] | None = None) -> dict[str, Any]:
    snippet = video.get("snippet") or {}
    stats = video.get("statistics") or {}
    details = video.get("contentDetails") or {}
    channel_id = snippet.get("channelId")
    seconds = parse_iso_duration(details.get("duration"))
    live = snippet.get("liveBroadcastContent")
    content_type = "live" if live in {"live", "upcoming"} else "video"
    if content_type == "video" and seconds is not None and seconds <= SHORTS_MAX_SECONDS:
        content_type = "short"
    item = base_item(video.get("id"), "video")
    item.update(
        {
            "content_type": content_type,
            "author": snippet.get("channelTitle"),
            "author_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
            "channel_id": channel_id,
            "published_at": snippet.get("publishedAt"),
            "published_at_precision": "exact",
            "title": snippet.get("title"),
            "text": truncate(snippet.get("description")),
            "duration": details.get("duration"),
            "duration_seconds": seconds,
            "default_audio_language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
            "tags": (snippet.get("tags") or [])[:MAX_TAGS],
            "has_captions": details.get("caption") == "true",
        }
    )
    item["metrics"].update(
        {
            "views": safe_int(stats.get("viewCount")),
            "likes": safe_int(stats.get("likeCount")),
            "comments": safe_int(stats.get("commentCount")),
        }
    )
    if content_type == "short":
        item["limitations"].append(message("shorts_heuristic", f"Classified as Short because duration <= {SHORTS_MAX_SECONDS}s."))
    if channel_metrics and channel_id in channel_metrics:
        channel_info = dict(channel_metrics[channel_id])
        handle = channel_info.pop("_handle", None)
        item["metrics"].update(channel_info)
        if handle:
            item["channel_handle"] = handle
            item["author_url"] = f"https://www.youtube.com/@{urllib.parse.quote(handle)}"
    return item


def channel_metrics_from_response(channels: list[dict[str, Any]]) -> dict[str, dict[str, int | None]]:
    result: dict[str, dict[str, int | None]] = {}
    for channel in channels:
        stats = channel.get("statistics") or {}
        result[channel.get("id")] = {
            "subscribers": safe_int(stats.get("subscriberCount")),
            "video_count": safe_int(stats.get("videoCount")),
            "_handle": ((channel.get("snippet") or {}).get("customUrl") or "").lstrip("@") or None,
        }
    return result


def fetch_videos(video_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    videos: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    ids = list(dict.fromkeys(video_ids))
    for offset in range(0, len(ids), 50):
        chunk = ids[offset : offset + 50]
        data, log = youtube_get(
            "videos",
            {"part": "snippet,contentDetails,statistics,status", "id": ",".join(chunk), "maxResults": 50},
            quota_cost=1,
        )
        items = data.get("items") or []
        log.update({"query": f"{len(chunk)} video ids", "result_count_before_filter": len(chunk), "result_count_after_filter": len(items)})
        videos.extend(items)
        logs.append(log)
    return videos, logs


def fetch_channels(channel_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    channels: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    ids = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    for offset in range(0, len(ids), 50):
        chunk = ids[offset : offset + 50]
        data, log = youtube_get(
            "channels",
            {"part": "snippet,contentDetails,statistics", "id": ",".join(chunk), "maxResults": 50},
            quota_cost=1,
        )
        items = data.get("items") or []
        log.update({"query": f"{len(chunk)} channel ids", "result_count_before_filter": len(chunk), "result_count_after_filter": len(items)})
        channels.extend(items)
        logs.append(log)
    return channels, logs


def resolve_channels_api(inputs: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    for raw in inputs:
        parsed = parse_channel_input(raw)
        if parsed["channel_id"]:
            channels, channel_logs = fetch_channels([parsed["channel_id"]])
            resolved.extend(channels)
            logs.extend(channel_logs)
            continue
        handle = parsed["handle"]
        if not handle:
            continue
        data, log = youtube_get(
            "channels",
            {"part": "snippet,contentDetails,statistics", "forHandle": handle, "maxResults": 1},
            quota_cost=1,
        )
        items = data.get("items") or []
        log.update({"query": f"@{handle}", "result_count_before_filter": 1, "result_count_after_filter": len(items)})
        resolved.extend(items)
        logs.append(log)
    return resolved, logs


def add_logs(result: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    result["queries_tried"].extend(logs)
    result["quota_estimate"] += sum(log.get("quota_cost") or 0 for log in logs)


def api_search_candidates(args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]]:
    start, end = period_bounds(args.period)
    per_query = min(args.max_fetch or args.limit * 3, 50)
    search_type = "channel" if args.type == "channel" else "video"
    found: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for query in queries_of(args):
        params = {
            "part": "snippet",
            "q": query,
            "type": search_type,
            "order": args.order,
            "regionCode": args.region,
            "relevanceLanguage": args.language,
            "publishedAfter": start if search_type == "video" else None,
            "publishedBefore": end if search_type == "video" else None,
            "videoDuration": args.video_duration if search_type == "video" else None,
            "safeSearch": "moderate",
            "maxResults": per_query,
        }
        data, log = youtube_get("search", params, quota_cost=100)
        hits = data.get("items") or []
        ids = []
        for rank, hit in enumerate(hits, 1):
            ident = (hit.get("id") or {}).get("channelId" if search_type == "channel" else "videoId")
            if not ident:
                continue
            ids.append(ident)
            if ident not in found:
                found[ident] = {"rank": rank, "queries": []}
                order.append(ident)
            found[ident]["queries"].append(query)
        log.update({"result_count_before_filter": len(hits), "result_count_after_filter": len(ids)})
        add_logs(result, [log])

    if search_type == "channel":
        channels, logs = fetch_channels(order)
        add_logs(result, logs)
        items = [item_from_api_channel(channel) for channel in channels]
    else:
        videos, logs = fetch_videos(order)
        add_logs(result, logs)
        channel_metrics: dict[str, dict[str, int | None]] = {}
        if not args.no_channel_enrich:
            channels, channel_logs = fetch_channels([(v.get("snippet") or {}).get("channelId") for v in videos])
            add_logs(result, channel_logs)
            channel_metrics = channel_metrics_from_response(channels)
        items = [item_from_api_video(video, channel_metrics) for video in videos]
    for item in items:
        meta = found.get(item["source_id"]) or {}
        item["search_rank"] = meta.get("rank")
        item["found_by_queries"] = meta.get("queries", [])
    items.sort(key=lambda item: order.index(item["source_id"]) if item["source_id"] in order else len(order))
    return items


def item_from_api_channel(channel: dict[str, Any]) -> dict[str, Any]:
    snippet = channel.get("snippet") or {}
    stats = channel.get("statistics") or {}
    handle = (snippet.get("customUrl") or "").lstrip("@") or None
    item = base_item(channel.get("id"), "channel")
    item.update(
        {
            "url": f"https://www.youtube.com/channel/{channel.get('id')}",
            "author": snippet.get("title"),
            "author_url": f"https://www.youtube.com/@{handle}" if handle else f"https://www.youtube.com/channel/{channel.get('id')}",
            "channel_id": channel.get("id"),
            "channel_handle": handle,
            "published_at": snippet.get("publishedAt"),
            "published_at_precision": "exact",
            "title": snippet.get("title"),
            "text": truncate(snippet.get("description")),
            "country": snippet.get("country"),
        }
    )
    item["metrics"].update(
        {
            "views": safe_int(stats.get("viewCount")),
            "subscribers": safe_int(stats.get("subscriberCount")),
            "video_count": safe_int(stats.get("videoCount")),
        }
    )
    return item


# ---------------------------------------------------------------------------
# Web backend: InnerTube search + yt-dlp enrichment


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _pb_int(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _pb_msg(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def build_search_params(args: argparse.Namespace, search_type: str) -> tuple[str | None, list[str]]:
    """Encode the YouTube web search filter ('sp' param). Returns (params, applied filter labels)."""
    labels: list[str] = []
    filters = b""
    days = period_start_days_ago(args.period)
    if days is not None and ".." not in args.period and search_type != "channel":
        for max_days, value in SP_UPLOAD_WINDOWS:
            if days <= max_days + 0.05:
                filters += _pb_int(1, value)
                labels.append(f"upload_date<={max_days:g}d")
                break
    filters += _pb_int(2, SP_TYPE[search_type])
    labels.append(f"type={search_type}")
    if args.video_duration and search_type == "video":
        filters += _pb_int(3, SP_DURATION[args.video_duration])
        labels.append(f"duration={args.video_duration}")
    params = b""
    sort = SP_SORT.get(args.order)
    if sort:
        params += _pb_int(1, sort)
        labels.append(f"sort={args.order}")
    elif args.order == "date":
        labels.append("sort=date_unsupported_by_web_sorted_locally")
    params += _pb_msg(2, filters)
    return base64.b64encode(params).decode(), labels


def innertube_search(query: str | None, args: argparse.Namespace, params: str | None, continuation: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": INNERTUBE_CLIENT_VERSION,
                "hl": args.language,
                "gl": args.region,
            }
        }
    }
    if continuation:
        body["continuation"] = continuation
    else:
        body["query"] = query
        if params:
            body["params"] = params
    try:
        response = requests.post(INNERTUBE_SEARCH_URL, json=body, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        raise SearchError("network_error", f"YouTube web search failed: {exc}") from exc
    if response.status_code >= 400:
        raise SearchError("web_search_failed", f"YouTube web search returned HTTP {response.status_code}.")
    return response.json()


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    if "content" in node:
        return node["content"]
    return "".join(run.get("text", "") for run in node.get("runs") or [])


def _walk(node: Any, keys: set[str]):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                yield key, value
            else:
                yield from _walk(value, keys)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, keys)


def _browse_endpoint(runs_node: Any) -> tuple[str | None, str | None]:
    for run in (runs_node or {}).get("runs") or []:
        endpoint = (run.get("navigationEndpoint") or {}).get("browseEndpoint") or {}
        if endpoint.get("browseId"):
            return endpoint.get("browseId"), (endpoint.get("canonicalBaseUrl") or "").lstrip("/") or None
    return None, None


def item_from_video_renderer(renderer: dict[str, Any]) -> dict[str, Any]:
    video_id = renderer.get("videoId")
    channel_id, handle_path = _browse_endpoint(renderer.get("ownerText") or renderer.get("longBylineText"))
    published_text = _text(renderer.get("publishedTimeText")) or None
    published_at, min_age = parse_relative_time(published_text)
    length_text = _text(renderer.get("lengthText")) or None
    badges = [
        ((badge.get("metadataBadgeRenderer") or {}).get("style") or "")
        for badge in (renderer.get("badges") or [])
    ]
    is_live = any("LIVE" in style for style in badges) or (not length_text and not published_text)
    item = base_item(video_id, "live" if is_live else "video")
    snippet = " ".join(
        _text(part.get("snippetText")) for part in renderer.get("detailedMetadataSnippets") or []
    ) or _text(renderer.get("descriptionSnippet"))
    owner_badges = [((badge.get("metadataBadgeRenderer") or {}).get("style") or "") for badge in renderer.get("ownerBadges") or []]
    item.update(
        {
            "author": _text(renderer.get("ownerText")) or _text(renderer.get("longBylineText")) or None,
            "author_url": f"https://www.youtube.com/{handle_path}" if handle_path else (f"https://www.youtube.com/channel/{channel_id}" if channel_id else None),
            "channel_id": channel_id,
            "channel_handle": handle_path[1:] if handle_path and handle_path.startswith("@") else None,
            "channel_verified": any("VERIFIED" in style for style in owner_badges),
            "published_at": published_at,
            "published_at_precision": "approx" if published_at else None,
            "published_text": published_text,
            "_min_age_days": min_age,
            "title": _text(renderer.get("title")) or None,
            "text": truncate(snippet),
            "duration": length_text,
            "duration_seconds": parse_duration_text(length_text),
        }
    )
    item["metrics"]["views"] = parse_count_text(_text(renderer.get("viewCountText")))
    return item


def item_from_shorts_lockup(model: dict[str, Any]) -> dict[str, Any] | None:
    endpoint = (((model.get("onTap") or {}).get("innertubeCommand") or {}).get("reelWatchEndpoint")) or {}
    video_id = endpoint.get("videoId")
    if not video_id:
        return None
    overlay = model.get("overlayMetadata") or {}
    title = _text(overlay.get("primaryText")) or (model.get("accessibilityText") or "").split(",")[0]
    item = base_item(video_id, "short")
    item["title"] = title or None
    item["metrics"]["views"] = parse_count_text(_text(overlay.get("secondaryText")))
    item["limitations"].append(message("shorts_partial", "Web search results for Shorts omit channel and date until enriched."))
    return item


def item_from_channel_renderer(renderer: dict[str, Any]) -> dict[str, Any]:
    channel_id = renderer.get("channelId")
    handle = None
    subscribers = None
    for field in ("subscriberCountText", "videoCountText"):
        value = _text(renderer.get(field))
        if value.startswith("@"):
            handle = value[1:]
        elif value:
            subscribers = parse_count_text(value)
    item = base_item(channel_id, "channel")
    item.update(
        {
            "url": f"https://www.youtube.com/@{urllib.parse.quote(handle)}" if handle else f"https://www.youtube.com/channel/{channel_id}",
            "author": _text(renderer.get("title")) or None,
            "author_url": f"https://www.youtube.com/channel/{channel_id}",
            "channel_id": channel_id,
            "channel_handle": handle,
            "channel_verified": bool(renderer.get("ownerBadges")),
            "title": _text(renderer.get("title")) or None,
            "text": truncate(_text(renderer.get("descriptionSnippet"))),
        }
    )
    item["metrics"]["subscribers"] = subscribers
    return item


def _next_page_token(data: dict[str, Any]) -> str | None:
    """The page continuation is the continuationItemRenderer at the end of the result list;
    other continuation tokens in the response belong to shelves and filter chips."""
    sections = (
        ((((data.get("contents") or {}).get("twoColumnSearchResultsRenderer") or {}).get("primaryContents") or {}).get("sectionListRenderer") or {}).get("contents")
    )
    if sections is None:
        for command in data.get("onResponseReceivedCommands") or []:
            sections = (command.get("appendContinuationItemsAction") or {}).get("continuationItems") or sections
    for section in reversed(sections or []):
        renderer = section.get("continuationItemRenderer")
        if renderer:
            return (((renderer.get("continuationEndpoint") or {}).get("continuationCommand")) or {}).get("token")
    return None


def parse_search_page(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    items: list[dict[str, Any]] = []
    continuation = _next_page_token(data)
    keys = {"videoRenderer", "shortsLockupViewModel", "channelRenderer"}
    for key, value in _walk(data, keys):
        if key == "videoRenderer" and value.get("videoId"):
            items.append(item_from_video_renderer(value))
        elif key == "shortsLockupViewModel":
            item = item_from_shorts_lockup(value)
            if item:
                items.append(item)
        elif key == "channelRenderer" and value.get("channelId"):
            items.append(item_from_channel_renderer(value))
    return items, continuation


def web_search_candidates(args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]]:
    if args.type == "channel":
        search_types = ["channel"]
    else:
        search_types = {"exclude": ["video"], "only": ["shorts"], "include": ["video", "shorts"]}[args.shorts]
    per_query = args.max_fetch or max(args.limit * 3, 30)
    found: dict[str, dict[str, Any]] = {}
    for query, search_type in ((q, t) for q in queries_of(args) for t in search_types):
        params, labels = build_search_params(args, search_type)
        continuation = None
        collected: list[dict[str, Any]] = []
        pages = 0
        usable = 0
        while usable < per_query and pages < MAX_SEARCH_PAGES:
            data = innertube_search(query, args, params, continuation)
            page_items, continuation = parse_search_page(data)
            pages += 1
            collected.extend(page_items)
            # Count only candidates that can survive the period / shorts filters, so a
            # coarse web date filter (e.g. "this year" for 90d) still yields enough items.
            usable += sum(
                1
                for item in page_items
                if (item["content_type"] == "short") == (args.shorts == "only") or args.shorts == "include"
                if item["content_type"] == "channel" or in_period(item, args.period)
            )
            if not continuation or not page_items:
                break
        rank = 0
        for item in collected:
            ident = item["source_id"]
            if ident in found:
                if query not in found[ident]["found_by_queries"]:
                    found[ident]["found_by_queries"].append(query)
                continue
            rank += 1
            item["search_rank"] = rank
            item["found_by_queries"] = [query]
            found[ident] = item
        result["queries_tried"].append(
            query_log(
                "web_search",
                query,
                0,
                len(collected),
                len(collected),
                memo=f"pages={pages} filters={','.join(labels)}",
            )
        )
    return list(found.values())


def _load_yt_dlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return None
    return yt_dlp


class _SilentLogger:
    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


COOKIES_FILE: str | None = None


def ytdlp_extract(url: str, flat: bool = False, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    yt_dlp = _load_yt_dlp()
    if yt_dlp is None:
        raise SearchError("ytdlp_missing", "yt-dlp is not installed.")
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "extractor_args": {"youtube": {}},
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if flat:
        opts["extract_flat"] = "in_playlist"
    for key, value in (extra or {}).items():
        if key == "extractor_args":
            opts["extractor_args"]["youtube"].update(value.get("youtube", {}))
        else:
            opts[key] = value
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False, process=flat) or {}
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many exception types
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        code = "rate_limited" if any(pattern in text.lower() for pattern in BLOCK_PATTERNS) else "ytdlp_failed"
        raise SearchError(code, truncate(text, 160)) from exc


def apply_detail(item: dict[str, Any], info: dict[str, Any]) -> None:
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp:
        item["published_at"] = iso(dt.datetime.fromtimestamp(timestamp, dt.timezone.utc))
        item["published_at_precision"] = "exact"
    elif info.get("upload_date"):
        item["published_at"] = f"{info['upload_date'][:4]}-{info['upload_date'][4:6]}-{info['upload_date'][6:]}T00:00:00Z"
        item["published_at_precision"] = "day"
    item.pop("_min_age_days", None)
    channel_id = info.get("channel_id") or item.get("channel_id")
    handle = (info.get("uploader_id") or "").lstrip("@") or item.get("channel_handle")
    captions = sorted(set((info.get("subtitles") or {}).keys()))
    # A single "<lang>-orig" auto-caption track is the spoken language; auto-dubbed videos list several.
    spoken = [lang[: -len("-orig")] for lang in (info.get("automatic_captions") or {}) if lang.endswith("-orig")]
    audio_language = info.get("language") or (spoken[0] if len(spoken) == 1 else None)
    item.update(
        {
            "title": info.get("title") or item.get("title"),
            "text": truncate(info.get("description")) or item.get("text"),
            "author": info.get("channel") or info.get("uploader") or item.get("author"),
            "channel_id": channel_id,
            "channel_handle": handle,
            "author_url": f"https://www.youtube.com/@{handle}" if handle else item.get("author_url"),
            "duration_seconds": info.get("duration") or item.get("duration_seconds"),
            "default_audio_language": audio_language,
            "category": (info.get("categories") or [None])[0],
            "tags": (info.get("tags") or [])[:MAX_TAGS],
            "chapters_count": len(info.get("chapters") or []),
            "enriched_by": sorted({*(item.get("enriched_by") or []), "ytdlp"}),
        }
    )
    item["caption_languages"] = captions
    item["has_captions"] = bool(captions or info.get("automatic_captions"))
    if len(spoken) > 1:
        item["auto_dubbed_languages"] = len(spoken)
    live_status = info.get("live_status")
    if live_status in {"is_live", "is_upcoming", "was_live", "post_live"}:
        item["live_status"] = live_status
    if item.get("content_type") == "video" and "/shorts/" in (info.get("webpage_url") or ""):
        item["content_type"] = "short"
    metrics = item["metrics"]
    for key, source in (("views", "view_count"), ("likes", "like_count"), ("comments", "comment_count"), ("subscribers", "channel_follower_count")):
        if info.get(source) is not None:
            metrics[key] = safe_int(info.get(source))
    item["limitations"] = [lim for lim in item["limitations"] if lim.get("code") != "shorts_partial"]


def innertube_next(video_id: str, args: argparse.Namespace) -> dict[str, Any]:
    body = {
        "context": {"client": {"clientName": "WEB", "clientVersion": INNERTUBE_CLIENT_VERSION, "hl": args.language, "gl": args.region}},
        "videoId": video_id,
    }
    try:
        response = requests.post(INNERTUBE_NEXT_URL, json=body, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        raise SearchError("network_error", f"YouTube next request failed: {exc}") from exc
    if response.status_code == 429:
        raise SearchError("rate_limited", "YouTube next endpoint returned HTTP 429.")
    if response.status_code >= 400:
        raise SearchError("web_detail_failed", f"YouTube next endpoint returned HTTP {response.status_code}.")
    return response.json()


MONTHS = {name: index for index, name in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date_text(text: str | None) -> tuple[str | None, str | None]:
    """'2026/09/20' / 'ライブ配信日: 2026/09/20' / 'Sep 20, 2026' / '9 時間前' -> (ISO, precision)."""
    if not text:
        return None, None
    match = re.search(r"(\d{4})[/年.-]\s*(\d{1,2})[/月.-]\s*(\d{1,2})", text)
    if match:
        year, month, day = (int(x) for x in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}T00:00:00Z", "day"
    match = re.search(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})", text)
    if match and match.group(1).lower() in MONTHS:
        return f"{int(match.group(3)):04d}-{MONTHS[match.group(1).lower()]:02d}-{int(match.group(2)):02d}T00:00:00Z", "day"
    published, _age = parse_relative_time(text)
    return (published, "approx") if published else (None, None)


def apply_next_detail(item: dict[str, Any], data: dict[str, Any]) -> bool:
    primary = next((value for _key, value in _walk(data, {"videoPrimaryInfoRenderer"})), None)
    if not primary:
        return False
    secondary = next((value for _key, value in _walk(data, {"videoSecondaryInfoRenderer"})), {}) or {}
    owner = (secondary.get("owner") or {}).get("videoOwnerRenderer") or {}
    channel_id, handle_path = _browse_endpoint(owner.get("title"))
    views_node = ((primary.get("viewCount") or {}).get("videoViewCountRenderer") or {})
    published, precision = parse_date_text(_text(primary.get("dateText")))
    likes = None
    for key, value in _walk(primary, {"likeButtonViewModel"}):
        text = json.dumps(value, ensure_ascii=False)
        match = re.search(r'"accessibilityText": "[^"]*?([\d,]+)[^"]*"', text)
        if match:
            likes = safe_int(match.group(1).replace(",", ""))
            break
    comments = None
    has_transcript = False
    for key, value in _walk(data, {"engagementPanelSectionListRenderer"}):
        panel = value.get("panelIdentifier") or value.get("targetId") or ""
        if panel == "engagement-panel-comments-section":
            header = (value.get("header") or {}).get("engagementPanelTitleHeaderRenderer") or {}
            comments = parse_count_text(_text(header.get("contextualInfo")))
        elif panel == "engagement-panel-searchable-transcript":
            has_transcript = True
    description = secondary.get("attributedDescription") or secondary.get("description")
    hashtags = [run.strip() for run in re.findall(r"#[^\s#]+", _text(primary.get("superTitleLink")))]
    is_live = bool(views_node.get("isLive"))
    if published:
        item["published_at"] = published
        item["published_at_precision"] = precision
        item.pop("_min_age_days", None)
    item.update(
        {
            "title": _text(primary.get("title")) or item.get("title"),
            "text": truncate(_text(description)) or item.get("text"),
            "author": _text(owner.get("title")) or item.get("author"),
            "channel_id": channel_id or item.get("channel_id"),
            "channel_handle": (handle_path[1:] if handle_path and handle_path.startswith("@") else None) or item.get("channel_handle"),
            "has_transcript": has_transcript,
        }
    )
    if hashtags:
        item["hashtags"] = hashtags
    if item.get("channel_handle"):
        item["author_url"] = f"https://www.youtube.com/@{item['channel_handle']}"
    elif item.get("channel_id"):
        item["author_url"] = f"https://www.youtube.com/channel/{item['channel_id']}"
    if is_live:
        item["content_type"] = "live"
    metrics = item["metrics"]
    views = parse_count_text(_text(views_node.get("viewCount")))
    for key, value in (("views", views), ("likes", likes), ("comments", comments), ("subscribers", parse_count_text(_text(owner.get("subscriberCountText"))))):
        if value is not None:
            metrics[key] = value
    item["enriched_by"] = sorted({*(item.get("enriched_by") or []), "web_next"})
    item["limitations"] = [lim for lim in item["limitations"] if lim.get("code") not in {"shorts_partial", "date_approx"}]
    return True


def innertube_player(video_id: str, args: argparse.Namespace) -> dict[str, Any]:
    body = {
        "context": {"client": {"clientName": "WEB", "clientVersion": INNERTUBE_CLIENT_VERSION, "hl": args.language, "gl": args.region}},
        "videoId": video_id,
    }
    try:
        response = requests.post(INNERTUBE_PLAYER_URL, json=body, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        raise SearchError("network_error", f"YouTube player request failed: {exc}") from exc
    if response.status_code == 429:
        raise SearchError("rate_limited", "YouTube player endpoint returned HTTP 429.")
    if response.status_code >= 400:
        raise SearchError("web_detail_failed", f"YouTube player endpoint returned HTTP {response.status_code}.")
    return response.json()


def apply_player_detail(item: dict[str, Any], data: dict[str, Any]) -> bool:
    """Player metadata carries the ORIGINAL title/description (search and next may show
    YouTube's auto-translated title), exact publish time, duration, keywords, category and likes."""
    details = data.get("videoDetails") or {}
    micro = (data.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not details.get("videoId"):
        return False
    original_title = details.get("title") or _text(micro.get("title"))
    shown_title = item.get("title")
    if original_title and shown_title and shown_title != original_title:
        item["title_localized"] = shown_title
    publish = parse_iso_datetime(micro.get("publishDate") or micro.get("uploadDate"))
    if publish:
        item["published_at"] = iso(publish)
        item["published_at_precision"] = "exact"
        item.pop("_min_age_days", None)
    seconds = safe_int(details.get("lengthSeconds") or micro.get("lengthSeconds"))
    owner_url = micro.get("ownerProfileUrl") or ""
    handle = owner_url.rsplit("/@", 1)[1] if "/@" in owner_url else None
    item.update(
        {
            "title": original_title or shown_title,
            "text": truncate(details.get("shortDescription")) or item.get("text"),
            "author": details.get("author") or item.get("author"),
            "channel_id": details.get("channelId") or item.get("channel_id"),
            "channel_handle": handle or item.get("channel_handle"),
            "category": micro.get("category"),
            "tags": (details.get("keywords") or [])[:MAX_TAGS],
        }
    )
    if seconds:
        item["duration_seconds"] = seconds
        item["duration"] = fmt_duration(seconds)
    if item.get("channel_handle"):
        item["author_url"] = f"https://www.youtube.com/@{item['channel_handle']}"
    if micro.get("isShortsEligible") and item.get("content_type") == "video":
        item["content_type"] = "short"
        item["url"] = f"https://www.youtube.com/shorts/{item['source_id']}"
    if details.get("isLiveContent") and (micro.get("liveBroadcastDetails") or {}).get("isLiveNow"):
        item["content_type"] = "live"
    metrics = item["metrics"]
    for key, value in (("views", safe_int(details.get("viewCount"))), ("likes", safe_int(micro.get("likeCount")))):
        if value is not None:
            metrics[key] = value
    item["enriched_by"] = sorted({*(item.get("enriched_by") or []), "web_player"})
    return True


def run_parallel(targets: list[dict[str, Any]], fetch) -> tuple[list[tuple[dict[str, Any], Any, SearchError | None]], list[str]]:
    """Run fetch(item) in a thread pool; stop issuing requests after the first rate-limit error."""
    blocked: list[str] = []

    def work(item: dict[str, Any]):
        if blocked:
            return item, None, SearchError("skipped_rate_limited", "Skipped after YouTube rate limiting.")
        try:
            return item, fetch(item), None
        except SearchError as exc:
            if exc.code == "rate_limited":
                blocked.append(exc.message)
            return item, None, exc

    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        return list(pool.map(work, targets)), blocked


def report_block(result: dict[str, Any], blocked: list[str], source: str, failed: int, total: int) -> None:
    if not blocked:
        return
    result["limitations"].append(message("rate_limited", f"YouTube blocked {source} ({blocked[0]}). {failed}/{total} items were not enriched by {source}."))
    action = message("avoid_rate_limit", "Set YOUTUBE_API_KEY (preferred), pass --cookies with an exported YouTube cookies.txt, or wait before re-running.")
    if action not in result["next_human_actions"]:
        result["next_human_actions"].append(action)


def enrich_items(items: list[dict[str, Any]], result: dict[str, Any], args: argparse.Namespace, deep: bool = False) -> None:
    """Fill exact date / likes / comments / subscribers. Web 'next' endpoint first (1 request per
    video); yt-dlp only with deep=True (tags, category, captions, duration) because it is slower
    and is the first thing YouTube rate-limits."""
    targets = [item for item in items if item.get("content_type") in {"video", "short", "live"} and "web_next" not in (item.get("enriched_by") or [])]
    if targets:
        def fetch_both(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
            next_data = innertube_next(item["source_id"], args)
            try:
                player_data = innertube_player(item["source_id"], args)
            except SearchError as exc:
                if exc.code == "rate_limited":
                    raise
                player_data = None
            return next_data, player_data

        outcomes, blocked = run_parallel(targets, fetch_both)
        done = 0
        for item, data, error in outcomes:
            if data is not None and apply_next_detail(item, data[0]):
                if not (data[1] and apply_player_detail(item, data[1])):
                    item["limitations"].append(message("original_title_unverified", "Player metadata unavailable; title may be YouTube's auto-translation and publish date is day precision."))
                done += 1
            elif error and error.code != "skipped_rate_limited":
                item["limitations"].append(message("enrich_failed", f"Web detail fetch failed ({error.code}): {error.message}"))
            elif data is not None:
                item["limitations"].append(message("video_unavailable", "Web detail returned no video metadata (private, deleted, or region-blocked)."))
        result["queries_tried"].append(query_log("web_video_detail", f"{len(targets)} videos (next+player)", 0, len(targets), done))
        report_block(result, blocked, "web detail", len(targets) - done, len(targets))

    if not deep:
        return
    targets = [item for item in items if item.get("content_type") in {"video", "short", "live"} and "ytdlp" not in (item.get("enriched_by") or [])]
    if not targets:
        return
    if _load_yt_dlp() is None:
        result["limitations"].append(message("ytdlp_missing", "yt-dlp is not installed; tags, category, captions and duration were not fetched."))
        return
    outcomes, blocked = run_parallel(targets, lambda item: ytdlp_extract(f"https://www.youtube.com/watch?v={item['source_id']}"))
    done = 0
    for item, info, error in outcomes:
        if info:
            apply_detail(item, info)
            done += 1
        elif error and error.code != "skipped_rate_limited":
            item["limitations"].append(message("deep_enrich_failed", f"yt-dlp detail fetch failed ({error.code}): {error.message}"))
    result["queries_tried"].append(query_log("ytdlp_video_detail", f"{len(targets)} videos", 0, len(targets), done))
    report_block(result, blocked, "yt-dlp detail", len(targets) - done, len(targets))


# ---------------------------------------------------------------------------
# Ranking, filtering, summary


def derived_metrics(item: dict[str, Any]) -> None:
    metrics = item["metrics"]
    views = metrics.get("views")
    published = parse_iso_datetime(item.get("published_at"))
    if item.get("content_type") == "channel":
        return
    if views is not None and published:
        age_days = max((dt.datetime.now(dt.timezone.utc) - published).total_seconds() / 86400, 1)
        metrics["age_days"] = round(age_days, 1)
        metrics["views_per_day"] = round(views / age_days, 1)
    if views:
        if metrics.get("likes") is not None:
            metrics["like_rate"] = round(metrics["likes"] / views, 4)
        if metrics.get("comments") is not None:
            metrics["comment_rate"] = round(metrics["comments"] / views, 5)
        if metrics.get("subscribers"):
            metrics["views_per_subscriber"] = round(views / metrics["subscribers"], 3)


def quality_score(item: dict[str, Any]) -> float:
    metrics = item.get("metrics") or {}
    if item.get("content_type") == "channel":
        return round(math.log10(max(metrics.get("subscribers") or 1, 1)) / 7, 4)
    views = metrics.get("views") or 0
    view_score = min(math.log10(max(views, 1)) / 7, 1)
    velocity_score = min(math.log10(max(metrics.get("views_per_day") or 1, 1)) / 5, 1)
    like_rate = metrics.get("like_rate")
    engagement_score = min((like_rate or 0) * 25, 1)
    outlier = metrics.get("views_per_subscriber")
    outlier_score = min(math.log10(1 + outlier * 9) if outlier else 0, 1)
    return round(view_score * 0.3 + velocity_score * 0.3 + engagement_score * 0.2 + outlier_score * 0.2, 4)


SORT_KEYS = {
    "quality": lambda item: item.get("quality_score") or 0,
    "views": lambda item: item["metrics"].get("views") or item["metrics"].get("subscribers") or 0,
    "velocity": lambda item: item["metrics"].get("views_per_day") or 0,
    "recent": lambda item: item.get("published_at") or "",
    "engagement": lambda item: item["metrics"].get("like_rate") or 0,
    "outlier": lambda item: item["metrics"].get("views_per_subscriber") or 0,
    "vs_median": lambda item: item["metrics"].get("views_vs_channel_median") or 0,
}


def effective_sort(args: argparse.Namespace) -> str:
    sort = getattr(args, "sort", "auto")
    if sort != "auto":
        return sort
    order = getattr(args, "order", "relevance")
    if args.subcommand == "channel":
        return "recent"
    return {"date": "recent", "viewCount": "views"}.get(order, "quality")


def matched_terms(item: dict[str, Any], queries: list[str], include: list[str]) -> list[str]:
    text = f"{item.get('title') or ''} {item.get('text') or ''} {' '.join(item.get('tags') or [])}".lower()
    terms: list[str] = []
    for term in [*queries, *include]:
        for token in re.split(r"\s+", term.strip()):
            if token and token.lower() in text and token not in terms:
                terms.append(token)
    return terms[:20]


def filter_items(
    items: list[dict[str, Any]],
    args: argparse.Namespace,
    apply_channel_limit: bool,
    excluded: Counter[str] | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    excluded = excluded if excluded is not None else Counter()
    include = getattr(args, "include", []) or []
    exclude = getattr(args, "exclude", []) or []
    min_views = getattr(args, "min_views", 0) or 0
    shorts_mode = getattr(args, "shorts", "exclude")
    kept: list[dict[str, Any]] = []

    for item in items:
        filters: list[str] = []
        content_type = item.get("content_type")
        if content_type == "short" and shorts_mode == "exclude":
            excluded["shorts_excluded"] += 1
            continue
        if content_type != "short" and shorts_mode == "only":
            excluded["not_short"] += 1
            continue
        if content_type == "live" and not getattr(args, "allow_live", False):
            excluded["live_excluded"] += 1
            continue
        haystack = f"{item.get('title') or ''} {item.get('text') or ''} {' '.join(item.get('tags') or [])}".lower()
        if exclude and any(term.lower() in haystack for term in exclude):
            excluded["excluded_term"] += 1
            continue
        if include and not any(term.lower() in haystack for term in include):
            excluded["no_matched_terms"] += 1
            continue
        if content_type != "channel":
            if not in_period(item, getattr(args, "period", "30d")):
                excluded["out_of_period"] += 1
                continue
            filters.append("in_period")
        views = item["metrics"].get("views")
        if content_type != "channel" and views is not None and views < min_views:
            excluded["below_min_views"] += 1
            continue
        if min_views:
            filters.append("min_views_ok")
        min_subs = getattr(args, "min_subscribers", 0) or 0
        subs = item["metrics"].get("subscribers")
        if min_subs and subs is not None and subs < min_subs:
            excluded["below_min_subscribers"] += 1
            continue
        lang = item.get("default_audio_language")
        wanted = (getattr(args, "language", "") or "").lower()
        if lang and wanted and not str(lang).lower().startswith(wanted):
            excluded["language_mismatch"] += 1
            continue
        script = CJK_LANG_PATTERNS.get(wanted)
        if not lang and script and content_type != "channel" and item.get("title") and not re.search(script, item["title"]):
            excluded["title_script_mismatch"] += 1
            continue
        filters.append("language_match" if lang else ("title_script_match" if script else "language_undetectable"))
        derived_metrics(item)
        item["matched_terms"] = matched_terms(item, queries_of(args), include)
        item["quality_score"] = quality_score(item)
        item["selection_filters"] = filters
        kept.append(item)

    sort = effective_sort(args)
    if sort != "youtube":
        kept.sort(key=SORT_KEYS[sort], reverse=True)

    if apply_channel_limit and getattr(args, "max_per_channel", 2) > 0:
        limited = []
        seen: defaultdict[str, int] = defaultdict(int)
        for item in kept:
            channel_id = item.get("channel_id") or item.get("author")
            if item.get("content_type") != "channel" and channel_id and seen[channel_id] >= args.max_per_channel:
                excluded["same_channel_limit"] += 1
                continue
            if channel_id:
                seen[channel_id] += 1
            item["selection_filters"].append("channel_diversity_ok")
            limited.append(item)
        kept = limited

    final = kept[: getattr(args, "limit", 15)]
    if len(kept) > len(final):
        excluded["over_limit"] += len(kept) - len(final)
    for position, item in enumerate(final, 1):
        item["rank"] = position
        item["why_selected"] = why_selected(item, sort)
    return final, excluded


def why_selected(item: dict[str, Any], sort: str) -> str:
    metrics = item.get("metrics") or {}
    parts = [f"sort={sort}"]
    if item.get("search_rank"):
        parts.append(f"search_rank={item['search_rank']}")
    for key in ("views", "views_per_day", "like_rate", "views_per_subscriber", "subscribers"):
        if metrics.get(key) is not None:
            parts.append(f"{key}={metrics[key]}")
    parts.append(f"quality_score={item.get('quality_score')}")
    if len(item.get("found_by_queries") or []) > 1:
        parts.append(f"found_by={len(item['found_by_queries'])}_queries")
    parts.append(f"filters={','.join(item.get('selection_filters') or [])}")
    return " ".join(parts)


def build_summary(result: dict[str, Any], candidates: int) -> dict[str, Any]:
    items = result["items"]
    videos = [item for item in items if item.get("content_type") != "channel"]
    views = [item["metrics"]["views"] for item in videos if item["metrics"].get("views") is not None]
    dates = sorted(item["published_at"][:10] for item in videos if item.get("published_at"))
    channels = Counter(item.get("author") for item in videos if item.get("author"))
    summary: dict[str, Any] = {
        "candidates": candidates,
        "selected": len(items),
        "content_types": dict(Counter(item.get("content_type") for item in items)),
    }
    if views:
        summary["views"] = {"total": sum(views), "median": int(statistics.median(views)), "max": max(views), "min": min(views)}
    if dates:
        summary["published_range"] = {"oldest": dates[0], "newest": dates[-1]}
    if channels:
        summary["top_channels"] = [{"channel": name, "videos": count} for name, count in channels.most_common(10)]
    approx = sum(1 for item in items if item.get("published_at_precision") == "approx")
    if approx:
        summary["approx_dates"] = approx
    return summary


def strip_private(items: list[dict[str, Any]]) -> None:
    for item in items:
        for key in [key for key in item if key.startswith("_")]:
            item.pop(key)


# ---------------------------------------------------------------------------
# Subcommands


def run_diag(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "diag", "diagnostic")
    key = api_key()
    if not key:
        result["limitations"].append(message("missing_api_key", "YOUTUBE_API_KEY is not set. The web backend is used instead."))
    else:
        try:
            data, log = youtube_get("videos", {"part": "id", "id": "dQw4w9WgXcQ"}, quota_cost=1)
            log["result_count_before_filter"] = 1
            log["result_count_after_filter"] = len(data.get("items") or [])
            add_logs(result, [log])
        except SearchError as exc:
            result["limitations"].append(message(exc.code, exc.message))
    try:
        data = innertube_search("youtube", args, None, None)
        count = len(parse_search_page(data)[0])
        result["queries_tried"].append(query_log("web_search", "youtube", 0, count, count))
        if not count:
            result["limitations"].append(message("web_search_empty", "Web search returned no parsable results; the page format may have changed."))
    except SearchError as exc:
        result["limitations"].append(message(exc.code, exc.message))
    try:
        ok = apply_next_detail(base_item("dQw4w9WgXcQ"), innertube_next("dQw4w9WgXcQ", args))
        result["queries_tried"].append(query_log("web_next_detail", "dQw4w9WgXcQ", 0, 1, 1 if ok else 0))
        if not ok:
            result["limitations"].append(message("web_detail_empty", "Web detail returned no parsable metadata; the page format may have changed."))
    except SearchError as exc:
        result["limitations"].append(message(exc.code, exc.message))
    if _load_yt_dlp() is None:
        result["limitations"].append(message("ytdlp_missing", "yt-dlp is not installed; --deep and channel listing are unavailable."))
    else:
        try:
            ytdlp_extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            result["queries_tried"].append(query_log("ytdlp_video_detail", "dQw4w9WgXcQ", 0, 1, 1, memo=f"version={_load_yt_dlp().version.__version__}"))
        except SearchError as exc:
            result["limitations"].append(message(exc.code, f"yt-dlp probe failed; --deep will not work right now: {exc.message}"))
    try:
        response = requests.get(RSS_URL, params={"channel_id": "UCBR8-60-B28hp2BmDPdntcQ"}, timeout=DEFAULT_TIMEOUT)
        result["queries_tried"].append(query_log("rss", "UCBR8-60-B28hp2BmDPdntcQ", 0, 1, 1 if response.ok else 0, memo=f"HTTP {response.status_code}"))
        if not response.ok:
            result["limitations"].append(message("rss_unreachable", f"RSS returned HTTP {response.status_code}."))
    except requests.RequestException as exc:
        result["limitations"].append(message("rss_unreachable", f"RSS check failed: {exc}"))
    result["summary"] = {"backend_auto_resolves_to": "api" if key else "web"}
    return result


def run_lookup(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "lookup", "oembed")
    values = [value for value in [*(args.url or []), *(args.id or [])] if value]
    video_ids = list(dict.fromkeys(filter(None, (parse_video_id(value) for value in values))))
    if not video_ids:
        result["limitations"].append(message("url_invalid", "Could not normalize a YouTube video URL or ID."))
        result["next_human_actions"].append(message("provide_video_url", "Provide a YouTube watch URL or 11-character video ID."))
        return result

    backend = resolve_backend(args)
    if backend == "api":
        result["tool"] = "youtube_data_api"
        videos, logs = fetch_videos(video_ids)
        add_logs(result, logs)
        channels, channel_logs = fetch_channels([(v.get("snippet") or {}).get("channelId") for v in videos])
        add_logs(result, channel_logs)
        metrics = channel_metrics_from_response(channels)
        items = [item_from_api_video(video, metrics) for video in videos]
    else:
        result["tool"] = "youtube_web"
        shorts = {parse_video_id(value) for value in values if "/shorts/" in value}
        items = [base_item(video_id, "short" if video_id in shorts else "video") for video_id in video_ids]
        enrich_items(items, result, args, deep=not args.no_deep)
        for item in items:
            if not item.get("enriched_by"):
                oembed_fill(item, result)
        items = [item for item in items if item.get("title")]
    for item in items:
        derived_metrics(item)
        item["why_selected"] = "Requested directly by URL / ID."
        item["limitations"] = [lim for lim in item["limitations"] if lim.get("code") != "video_unavailable"]
    missing = set(video_ids) - {item["source_id"] for item in items}
    for video_id in missing:
        result["limitations"].append(message("video_unavailable", f"No metadata returned for {video_id} (private, deleted, or region-blocked)."))
    result["items"] = items
    return result


def oembed_fill(item: dict[str, Any], result: dict[str, Any]) -> None:
    url = f"https://www.youtube.com/watch?v={item['source_id']}"
    try:
        response = requests.get(OEMBED_URL, params={"url": url, "format": "json"}, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        result["limitations"].append(message("oembed_failed", f"oEmbed failed: {exc}"))
        return
    result["queries_tried"].append(query_log("oembed", url, 0, 1, 1 if response.ok else 0))
    if not response.ok:
        result["limitations"].append(message("oembed_failed", f"oEmbed returned HTTP {response.status_code} for {item['source_id']}."))
        return
    data = response.json()
    item.update({"title": data.get("title"), "author": data.get("author_name"), "author_url": data.get("author_url")})
    item["limitations"].append(message("metric_missing", "oEmbed does not include metrics or publish date."))


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    backend = resolve_backend(args)
    result = empty_result(args, "search", "youtube_data_api" if backend == "api" else "youtube_web")
    candidates: list[dict[str, Any]] = []
    if backend == "api":
        try:
            candidates = api_search_candidates(args, result)
        except SearchError as exc:
            if args.backend != "auto" or exc.code not in {"quota_exceeded", "forbidden"}:
                raise
            result["limitations"].append(message(exc.code, f"{exc.message} Fell back to the web backend."))
            backend = "web"
            result["tool"] = "youtube_web"
    if backend == "web":
        candidates = web_search_candidates(args, result)
        if args.order == "date":
            result["limitations"].append(message("web_date_sort", "Web search cannot sort by upload date; results were sorted locally within the period filter."))
    result["auto_routing"]["chosen_defaults"] = {"backend": backend, "sort": effective_sort(args)}

    discovered = len(candidates)
    excluded: Counter[str] = Counter()
    if backend == "web":
        # Pre-filter and rank with cheap facts, then spend detail enrichment on the top of that ranking.
        prefilter_args = argparse.Namespace(**{**vars(args), "min_views": 0, "limit": 10**6, "max_per_channel": 0, "sort": effective_sort(args)})
        pool, excluded = filter_items(candidates, prefilter_args, apply_channel_limit=False, excluded=excluded)
        excluded.pop("over_limit", None)
        if args.type != "channel" and not args.no_enrich:
            enrich_target = pool[: args.enrich_top or max(DEFAULT_ENRICH_CAP, args.limit)]
            enrich_items(enrich_target, result, args, deep=args.deep)
            for item in pool:
                if item.get("published_at_precision") == "approx":
                    item["limitations"].append(message("date_approx", f"Publish date estimated from '{item.get('published_text')}'."))
        elif args.type != "channel":
            result["limitations"].append(message("not_enriched", "--no-enrich: dates are approximate, likes/comments/subscribers are missing, and titles may be YouTube's auto-translation."))
        candidates = pool

    filtered, excluded = filter_items(candidates, args, apply_channel_limit=args.type != "channel", excluded=excluded)
    strip_private(filtered)
    result["items"] = filtered
    result["excluded_summary"] = compact_counts(excluded)
    result["summary"] = build_summary(result, discovered)
    if not filtered:
        result["limitations"].append(message("result_empty", "No items remained after filtering."))
    elif args.type == "channel" and not args.no_activity:
        add_channel_activity(filtered, result)
    if args.comments and args.type != "channel":
        top = [item for item in filtered if item.get("content_type") != "channel"][: args.comments]
        comment_args = argparse.Namespace(**{**vars(args), "limit": args.comments_per_video})
        collect_comments([item["source_id"] for item in top], backend, comment_args, result, {item["source_id"]: item.get("title") for item in top})
        result["summary"]["comments"] = len(result["comments"])
    return result


def run_channel(args: argparse.Namespace) -> dict[str, Any]:
    backend = resolve_backend(args)
    result = empty_result(args, "channel", "youtube_data_api" if backend == "api" else "youtube_web")
    result["inputs"] = list(args.channels)
    args.channels = channels_from_video_urls(args.channels, backend, args, result)
    if backend == "api":
        items = api_channel_items(args, result)
    else:
        items = web_channel_items(args, result)
    if items is None:
        return result
    baselines = apply_channel_baseline(items)
    filtered, excluded = filter_items(items, args, apply_channel_limit=False)
    strip_private(filtered)
    for item in filtered:
        item["why_selected"] = f"Selected from the channel uploads. {item['why_selected']}"
    result["items"] = filtered
    result["excluded_summary"] = compact_counts(excluded)
    result["summary"] = build_summary(result, len(items))
    if baselines:
        result["summary"]["channel_baselines"] = baselines
        result["limitations"].append(message("baseline_age_bias", "views_vs_channel_median compares against the median of the fetched uploads; very recent videos have had less time to collect views."))
    result["auto_routing"]["chosen_defaults"] = {"backend": backend, "sort": effective_sort(args)}
    if not filtered:
        result["limitations"].append(message("result_empty", "No channel videos remained after filtering."))
    return result


def channels_from_video_urls(values: list[str], backend: str, args: argparse.Namespace, result: dict[str, Any]) -> list[str]:
    """Let `channel --channels <video URL>` research the channel that uploaded that video."""
    resolved: list[str] = []
    for raw in values:
        video_id = parse_video_id(raw) if re.search(VIDEO_URL_PATTERN, raw) else None
        if not video_id:
            resolved.append(raw)
            continue
        channel_id = None
        try:
            if backend == "api":
                videos, logs = fetch_videos([video_id])
                add_logs(result, logs)
                channel_id = (videos[0].get("snippet") or {}).get("channelId") if videos else None
            else:
                channel_id = (innertube_player(video_id, args).get("videoDetails") or {}).get("channelId")
                result["queries_tried"].append(query_log("web_player", video_id, 0, 1, 1 if channel_id else 0, memo="video URL -> channel"))
        except SearchError as exc:
            result["limitations"].append(message(exc.code, f"Could not resolve the channel of {raw}: {exc.message}"))
        if channel_id:
            resolved.append(channel_id)
        else:
            result["limitations"].append(message("channel_id_unknown", f"Could not resolve the channel of video {raw}."))
    return resolved


def apply_channel_baseline(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Median views of the fetched uploads per channel and content type (videos and Shorts differ a lot),
    and each video's views relative to it. Uses all fetched uploads, before the period filter."""
    groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item["metrics"].get("views") is not None and item.get("content_type") in {"video", "short"}:
            groups[(item.get("channel_id") or item.get("author") or "", item["content_type"])].append(item)
    baselines = []
    for (channel, content_type), group in groups.items():
        if len(group) < BASELINE_MIN_SAMPLES:
            continue
        median = statistics.median(item["metrics"]["views"] for item in group)
        for item in group:
            item["metrics"]["channel_median_views"] = int(median)
            if median:
                item["metrics"]["views_vs_channel_median"] = round(item["metrics"]["views"] / median, 2)
        baselines.append(
            {
                "channel": group[0].get("author"),
                "channel_id": group[0].get("channel_id"),
                "content_type": content_type,
                "uploads_sampled": len(group),
                "median_views": int(median),
                "subscribers": group[0]["metrics"].get("subscribers"),
            }
        )
    return baselines


def api_channel_items(args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]] | None:
    channels, logs = resolve_channels_api(args.channels)
    add_logs(result, logs)
    if not channels:
        result["limitations"].append(message("channel_id_unknown", "Could not resolve any channel inputs."))
        result["next_human_actions"].append(message("provide_channel", "Provide a channel ID, channel URL, or @handle."))
        return None
    video_ids: list[str] = []
    for channel in channels:
        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            result["limitations"].append(message("uploads_playlist_missing", f"Uploads playlist missing for channel {channel.get('id')}."))
            continue
        data, log = youtube_get(
            "playlistItems",
            {"part": "contentDetails", "playlistId": uploads, "maxResults": min(args.max_fetch or args.limit * 3, 50)},
            quota_cost=1,
        )
        ids = [((entry.get("contentDetails") or {}).get("videoId")) for entry in data.get("items") or []]
        ids = [video_id for video_id in ids if video_id]
        log.update({"query": uploads, "result_count_before_filter": len(ids), "result_count_after_filter": len(ids)})
        add_logs(result, [log])
        video_ids.extend(ids)
    videos, video_logs = fetch_videos(video_ids)
    add_logs(result, video_logs)
    metrics = channel_metrics_from_response(channels)
    return [item_from_api_video(video, metrics) for video in videos]


def web_channel_items(args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]] | None:
    per_channel = args.max_fetch or min(args.limit * 2, 50)
    tabs = ["videos"] if args.shorts == "exclude" else (["shorts"] if args.shorts == "only" else ["videos", "shorts"])
    items: list[dict[str, Any]] = []
    for raw in args.channels:
        parsed = parse_channel_input(raw)
        for tab in tabs:
            url = f"{channel_page_url(parsed)}/{tab}"
            try:
                info = ytdlp_extract(url, flat=True, extra={"playlistend": per_channel, "extractor_args": {"youtube": {"lang": [args.language]}}})
            except SearchError as exc:
                result["limitations"].append(message("channel_fetch_failed", f"{raw} /{tab}: {exc.message} Falling back to RSS (latest 15 uploads)."))
                channel_id = resolve_channel_id(raw, result)
                if channel_id and tab == "videos":
                    items.extend(rss_items(channel_id, result))
                continue
            entries = [entry for entry in info.get("entries") or [] if entry.get("id")]
            result["queries_tried"].append(query_log(f"ytdlp_channel_{tab}", raw, 0, len(entries), len(entries)))
            for entry in entries:
                item = base_item(entry["id"], "short" if tab == "shorts" else "video")
                seconds = safe_int(entry.get("duration"))
                item.update(
                    {
                        "title": entry.get("title"),
                        "author": info.get("channel"),
                        "channel_id": info.get("channel_id"),
                        "duration_seconds": seconds,
                        "duration": fmt_duration(seconds) if seconds else None,
                    }
                )
                items.append(item)
    if not items:
        result["limitations"].append(message("channel_id_unknown", "Could not list videos for any channel input."))
        result["next_human_actions"].append(message("provide_channel", "Provide a channel ID, channel URL, or @handle."))
        return None
    enrich_items(items, result, args, deep=args.deep)
    return items


RSS_FEED_SIZE = 15
RSS_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}


def resolve_channel_id(value: str, result: dict[str, Any]) -> str | None:
    parsed = parse_channel_input(value)
    if parsed["channel_id"]:
        return parsed["channel_id"]
    channel_id = resolve_handle_web(parsed)
    if channel_id:
        result["queries_tried"].append(query_log("channel_page", value, 0, 1, 1))
    else:
        result["limitations"].append(message("channel_id_unknown", f"Could not resolve {value} to a channel ID."))
    return channel_id


def rss_items(channel_id: str, result: dict[str, Any], log: bool = True) -> list[dict[str, Any]]:
    """Latest 15 uploads with exact dates, views and likes. No key, no quota."""
    response = None
    for _attempt in range(3):  # YouTube RSS intermittently returns 5xx
        try:
            response = requests.get(RSS_URL, params={"channel_id": channel_id}, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            result["limitations"].append(message("rss_unreachable", f"RSS failed for {channel_id}: {exc}"))
            return []
        if response.status_code < 500:
            break
    if not response.ok:
        result["limitations"].append(message("rss_unreachable", f"RSS returned HTTP {response.status_code} for {channel_id}. Use the channel subcommand instead."))
        return []
    items = []
    for entry in ET.fromstring(response.text).findall("atom:entry", RSS_NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=RSS_NS)
        link_node = entry.find("atom:link", RSS_NS)
        link = link_node.get("href", "") if link_node is not None else ""
        item = base_item(video_id, "short" if "/shorts/" in link else "video")
        views = entry.find("media:group/media:community/media:statistics", RSS_NS)
        rating = entry.find("media:group/media:community/media:starRating", RSS_NS)
        item.update(
            {
                "author": entry.findtext("atom:author/atom:name", default="", namespaces=RSS_NS),
                "author_url": f"https://www.youtube.com/channel/{channel_id}",
                "channel_id": channel_id,
                "published_at": entry.findtext("atom:published", default="", namespaces=RSS_NS),
                "published_at_precision": "exact",
                "title": entry.findtext("atom:title", default="", namespaces=RSS_NS),
                "text": truncate(entry.findtext("media:group/media:description", default="", namespaces=RSS_NS)),
            }
        )
        item["metrics"]["views"] = safe_int(views.get("views")) if views is not None else None
        item["metrics"]["likes"] = safe_int(rating.get("count")) if rating is not None else None
        items.append(item)
    if log:
        result["queries_tried"].append(query_log("rss", channel_id, 0, len(items), len(items)))
    return items


def add_channel_activity(items: list[dict[str, Any]], result: dict[str, Any]) -> None:
    """Is the channel still active, and how do its recent uploads perform? From RSS (latest 15 uploads)."""
    channels = [item for item in items if item.get("content_type") == "channel" and item.get("channel_id")]
    if not channels:
        return
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        feeds = list(pool.map(lambda item: rss_items(item["channel_id"], result, log=False), channels))
    now = dt.datetime.now(dt.timezone.utc)
    done = 0
    for item, uploads in zip(channels, feeds):
        dates = [parse_iso_datetime(upload.get("published_at")) for upload in uploads]
        dates = sorted((d for d in dates if d), reverse=True)
        if not dates:
            continue
        done += 1
        views = [upload["metrics"]["views"] for upload in uploads if upload.get("content_type") == "video" and upload["metrics"].get("views") is not None]
        shorts_views = [upload["metrics"]["views"] for upload in uploads if upload.get("content_type") == "short" and upload["metrics"].get("views") is not None]
        item["activity"] = {
            "sampled_uploads": len(uploads),
            "last_upload_at": iso(dates[0]),
            "days_since_last_upload": round((now - dates[0]).total_seconds() / 86400, 1),
            "uploads_last_30d": sum(1 for d in dates if (now - d).days < 30),
            "recent_videos": len(views),
            "recent_video_median_views": int(statistics.median(views)) if views else None,
            "recent_shorts": len(shorts_views),
            "recent_shorts_median_views": int(statistics.median(shorts_views)) if shorts_views else None,
        }
    result["queries_tried"].append(query_log("rss", f"{len(channels)} channels (activity)", 0, len(channels), done, memo="latest 15 uploads per channel"))


def run_monitor(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "monitor", "rss")
    result["inputs"] = list(args.channels)
    for value in args.channels:
        channel_id = resolve_channel_id(value, result)
        if not channel_id:
            continue
        kept = 0
        for item in rss_items(channel_id, result):
            if kept >= args.limit or not in_period(item, args.period):
                continue
            if args.shorts == "exclude" and item["content_type"] == "short":
                continue
            derived_metrics(item)
            item["selection_filters"] = ["rss_entry", "in_period"]
            item["why_selected"] = "Selected from the channel RSS feed (latest 15 uploads)."
            result["items"].append(item)
            kept += 1
    result["items"].sort(key=lambda item: item.get("published_at") or "", reverse=True)
    result["summary"] = build_summary(result, len(result["items"]))
    if not result["items"]:
        result["limitations"].append(message("result_empty", "No RSS entries were found for the provided channels and period."))
    return result


def resolve_handle_web(parsed: dict[str, str | None]) -> str | None:
    try:
        response = requests.get(channel_page_url(parsed), timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"})
    except requests.RequestException:
        return None
    match = re.search(r'"(?:externalId|browseId)":"(UC[A-Za-z0-9_-]{22})"', response.text)
    return match.group(1) if match else None


def run_comments(args: argparse.Namespace) -> dict[str, Any]:
    backend = resolve_backend(args)
    result = empty_result(args, "comments", "youtube_data_api" if backend == "api" else "youtube_web")
    video_ids = []
    for value in args.video_ids:
        video_id = parse_video_id(value)
        if video_id and video_id not in video_ids:
            video_ids.append(video_id)
    result["inputs"] = video_ids
    if not video_ids:
        result["limitations"].append(message("url_invalid", "No valid video IDs were provided."))
        return result

    collect_comments(video_ids[: args.comment_top_n], backend, args, result, {})
    if not result["comments"]:
        result["limitations"].append(message("result_empty", "No comments were returned. Comments may be disabled or unavailable."))
    result["summary"] = {"videos": len(video_ids[: args.comment_top_n]), "comments": len(result["comments"])}
    return result


def collect_comments(video_ids: list[str], backend: str, args: argparse.Namespace, result: dict[str, Any], titles: dict[str, str]) -> None:
    for video_id in video_ids:
        try:
            comments = api_comments(video_id, args, result) if backend == "api" else web_comments(video_id, args, result)
        except SearchError as exc:
            code = "comments_disabled" if exc.code in {"forbidden", "api_error"} else exc.code
            result["limitations"].append(message(code, f"Could not fetch comments for {video_id}: {exc.message}"))
            continue
        for comment in comments:
            if titles.get(video_id):
                comment["video_title"] = titles[video_id]
        result["comments"].extend(comments)


def api_comments(video_id: str, args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]]:
    data, log = youtube_get(
        "commentThreads",
        {"part": "snippet", "videoId": video_id, "maxResults": min(args.limit, 100), "order": args.comment_order, "textFormat": "plainText"},
        quota_cost=1,
    )
    items = data.get("items") or []
    log.update({"query": video_id, "result_count_before_filter": len(items), "result_count_after_filter": len(items)})
    add_logs(result, [log])
    comments = []
    for item in items:
        snippet = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        text = truncate(snippet.get("textDisplay"), MAX_COMMENT_TEXT)
        if not text:
            continue
        comments.append(
            {
                "video_id": video_id,
                "comment_id": item.get("id"),
                "text": text,
                "author_display_name": snippet.get("authorDisplayName"),
                "like_count": safe_int(snippet.get("likeCount")),
                "reply_count": safe_int((item.get("snippet") or {}).get("totalReplyCount")),
                "published_at": snippet.get("publishedAt"),
                "order": args.comment_order,
                "why_selected": f"Fetched via commentThreads.list order={args.comment_order}.",
            }
        )
    return comments


def innertube_continuation(token: str, args: argparse.Namespace) -> dict[str, Any]:
    body = {
        "context": {"client": {"clientName": "WEB", "clientVersion": INNERTUBE_CLIENT_VERSION, "hl": args.language, "gl": args.region}},
        "continuation": token,
    }
    try:
        response = requests.post(INNERTUBE_NEXT_URL, json=body, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        raise SearchError("network_error", f"YouTube comments request failed: {exc}") from exc
    if response.status_code >= 400:
        raise SearchError("rate_limited" if response.status_code == 429 else "web_comments_failed", f"YouTube comments returned HTTP {response.status_code}.")
    return response.json()


def _comments_start_token(data: dict[str, Any], newest: bool) -> str | None:
    # The sort menu lists [top, newest]; position is language independent.
    for _key, menu in _walk(data, {"sortFilterSubMenuRenderer"}):
        options = menu.get("subMenuItems") or []
        if options:
            option = options[1 if newest and len(options) > 1 else 0]
            token = ((option.get("serviceEndpoint") or {}).get("continuationCommand") or {}).get("token")
            if token:
                return token
    for _key, section in _walk(data, {"itemSectionRenderer"}):
        if section.get("sectionIdentifier") == "comment-item-section":
            for content in section.get("contents") or []:
                token = (((content.get("continuationItemRenderer") or {}).get("continuationEndpoint") or {}).get("continuationCommand") or {}).get("token")
                if token:
                    return token
    return None


def web_comments(video_id: str, args: argparse.Namespace, result: dict[str, Any]) -> list[dict[str, Any]]:
    newest = args.comment_order == "time"
    data = innertube_next(video_id, args)
    total = None
    for _key, panel in _walk(data, {"engagementPanelSectionListRenderer"}):
        if panel.get("panelIdentifier") == "engagement-panel-comments-section":
            header = (panel.get("header") or {}).get("engagementPanelTitleHeaderRenderer") or {}
            total = parse_count_text(_text(header.get("contextualInfo")))
    primary = next((value for _key, value in _walk(data, {"videoPrimaryInfoRenderer"})), None) or {}
    video_title = _text(primary.get("title")) or None
    token = _comments_start_token(data, newest)
    if not token:
        raise SearchError("comments_disabled", "No comment section found (comments may be disabled).")
    comments: list[dict[str, Any]] = []
    pages = 0
    skipped_creator = 0
    while token and len(comments) < args.limit and pages < 10:
        page = innertube_continuation(token, args)
        pages += 1
        payloads = {}
        for mutation in ((page.get("frameworkUpdates") or {}).get("entityBatchUpdate") or {}).get("mutations") or []:
            payload = (mutation.get("payload") or {}).get("commentEntityPayload")
            if payload:
                payloads[(payload.get("properties") or {}).get("commentId")] = payload
        token = None
        for _key, items in _walk(page, {"continuationItems"}):
            for entry in items or []:
                thread = entry.get("commentThreadRenderer")
                if thread:
                    view = (thread.get("commentViewModel") or {}).get("commentViewModel") or {}
                    payload = payloads.get(view.get("commentId"))
                    if payload:
                        comment = comment_from_payload(video_id, payload, thread, args)
                        comment["video_title"] = video_title
                        if comment["author_is_uploader"] and not args.include_creator_comments:
                            skipped_creator += 1
                        else:
                            comments.append(comment)
                next_token = (((entry.get("continuationItemRenderer") or {}).get("continuationEndpoint") or {}).get("continuationCommand") or {}).get("token")
                if next_token:
                    token = next_token
    result["queries_tried"].append(
        query_log("web_comments", video_id, 0, len(comments), min(len(comments), args.limit), memo=f"sort={'newest' if newest else 'top'} pages={pages} total_comment_count={total} creator_comments_skipped={skipped_creator}")
    )
    return comments[: args.limit]


def comment_from_payload(video_id: str, payload: dict[str, Any], thread: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    properties = payload.get("properties") or {}
    toolbar = payload.get("toolbar") or {}
    author = payload.get("author") or {}
    published_text = properties.get("publishedTime")
    published_at, _age = parse_relative_time(published_text)
    return {
        "video_id": video_id,
        "comment_id": properties.get("commentId"),
        "text": truncate(((properties.get("content") or {}).get("content")), MAX_COMMENT_TEXT),
        "author_display_name": author.get("displayName"),
        "like_count": parse_count_text(toolbar.get("likeCountA11y") or toolbar.get("likeCountNotliked")) or 0,
        "reply_count": parse_count_text(toolbar.get("replyCountA11y") or toolbar.get("replyCount")) or 0,
        "published_at": published_at,
        "published_at_precision": "approx" if published_at else None,
        "published_text": published_text,
        "is_pinned": thread.get("renderingPriority") == "RENDERING_PRIORITY_PINNED_COMMENT",
        "author_is_uploader": bool(author.get("isCreator")),
        "order": args.comment_order,
        "why_selected": f"Fetched via YouTube web comments sort={'newest' if args.comment_order == 'time' else 'top'}.",
    }


def pytrends_timeframe(period: str) -> str:
    days = period_start_days_ago(period) or 90
    if days <= 1:
        return "now 1-d"
    if days <= 7:
        return "now 7-d"
    if days <= 31:
        return "today 1-m"
    if days <= 90:
        return "today 3-m"
    if days <= 366:
        return "today 12-m"
    return "today 5-y"


def run_trends(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "trends", "google_trends")
    try:
        from pytrends.request import TrendReq  # type: ignore
    except ImportError:
        result["limitations"].append(message("trends_unavailable", "pytrends is not installed."))
        result["next_human_actions"].append(message("install_pytrends", "Install pytrends or run in the declared environment."))
        return result

    trend = TrendReq(hl=f"{args.language}-{args.region}", tz=0)
    for query in queries_of(args):
        try:
            trend.build_payload([query], cat=0, timeframe=pytrends_timeframe(args.period), geo=args.region, gprop="youtube")
            related = trend.related_queries().get(query) or {}
        except Exception as exc:  # noqa: BLE001
            result["limitations"].append(message("trends_unavailable", f"Google Trends request failed for '{query}': {exc}"))
            continue
        rows = []
        for section_name in ("top", "rising"):
            table = related.get(section_name)
            if table is None:
                continue
            for row in table.head(args.limit).to_dict("records"):
                rows.append(
                    {
                        "keyword": row.get("query"),
                        "score": safe_int(row.get("value")),
                        "rising": section_name == "rising",
                        "seed_query": query,
                        "related_queries": [],
                        "region": args.region,
                        "period": args.period,
                    }
                )
        seen = {(item["seed_query"], item["keyword"], item["rising"]) for item in result["trends"]}
        added = 0
        for row in rows:
            key = (row["seed_query"], row["keyword"], row["rising"])
            if row["keyword"] and key not in seen:
                seen.add(key)
                result["trends"].append(row)
                added += 1
        result["queries_tried"].append(
            query_log("google_trends_youtube_search", query, 0, len(rows), added, memo="Values are relative 0-100 scores; rising values are % growth.")
        )
    result["limitations"].append(message("relative_score", "Google Trends scores are relative and sampled, not absolute search volume."))
    if not result["trends"]:
        result["limitations"].append(message("result_empty", "No related Trends queries were returned."))
    return result


# ---------------------------------------------------------------------------
# Output


def fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value < 1:
        return f"{value:.2%}"
    value = float(value)
    for threshold, suffix in ((1e8, "億"), (1e4, "万")):
        if abs(value) >= threshold:
            return f"{value / threshold:.1f}{suffix}"
    return f"{value:,.0f}"


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "-"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def md_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "｜").replace("\n", " ")


def render_markdown(result: dict[str, Any], details: bool = False) -> str:
    summary = result.get("summary") or {}
    lines = [
        f"# YouTube {result.get('subcommand')}: {result.get('query') or ', '.join(result.get('inputs') or [])}".rstrip(),
        "",
        f"- Purpose: {result.get('purpose')} / Tool: {result.get('tool')} / Quota: {result.get('quota_estimate')}",
        f"- Language/Region/Period: {result.get('language')} / {result.get('region')} / {result.get('period')}",
        f"- Fetched at: {result.get('fetched_at')}",
    ]
    if summary.get("selected") is not None:
        lines.append(f"- Selected {summary['selected']} of {summary.get('candidates')} candidates {json.dumps(summary.get('content_types') or {}, ensure_ascii=False)}")
    if summary.get("views"):
        views = summary["views"]
        lines.append(f"- Views: median {fmt_num(views['median'])} / max {fmt_num(views['max'])} / total {fmt_num(views['total'])}")
    if summary.get("published_range"):
        lines.append(f"- Published: {summary['published_range']['oldest']} .. {summary['published_range']['newest']}")
    repeated = [f"{c['channel']} ×{c['videos']}" for c in summary.get("top_channels") or [] if c["videos"] > 1]
    if repeated:
        lines.append(f"- Repeated channels: {', '.join(repeated)}")
    items = result.get("items") or []
    for baseline in summary.get("channel_baselines") or []:
        lines.append(
            f"- Baseline {baseline['channel']} ({baseline['content_type']}): median {fmt_num(baseline['median_views'])} views "
            f"over {baseline['uploads_sampled']} fetched uploads, subscribers {fmt_num(baseline.get('subscribers'))}"
        )
    items = result.get("items") or []
    if items and all(item.get("content_type") == "channel" for item in items):
        with_activity = any(item.get("activity") for item in items)
        header = "| # | Channel | Subscribers | Videos |" + (" Last upload | Uploads/30d | Recent median views |" if with_activity else "") + " Description |"
        lines += ["", header, "|" + "---|" * (header.count("|") - 1)]
        for idx, item in enumerate(items, 1):
            m = item["metrics"]
            activity_cells = ""
            if with_activity:
                a = item.get("activity") or {}
                recent = fmt_num(a.get("recent_video_median_views"))
                if a.get("recent_shorts_median_views") is not None:
                    recent += f" (Shorts {fmt_num(a['recent_shorts_median_views'])})"
                uploads = a.get("uploads_last_30d", "-")
                if a and uploads == a.get("sampled_uploads") and uploads >= RSS_FEED_SIZE:
                    uploads = f"{uploads}+"
                activity_cells = f" {(a.get('last_upload_at') or '-')[:10]} | {uploads} | {recent} |"
            lines.append(
                f"| {idx} | [{md_cell(item.get('title'))}]({item.get('url')}) | {fmt_num(m.get('subscribers'))} | {fmt_num(m.get('video_count'))} |{activity_cells} {md_cell(truncate(item.get('text'), 60))} |"
            )
    elif items:
        baseline = any(item["metrics"].get("views_vs_channel_median") is not None for item in items)
        last_header = "vs Ch. median" if baseline else "Views/Subs"
        lines += [
            "",
            f"| # | Title | Channel | Published | Len | Views | Views/day | Like% | {last_header} |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for idx, item in enumerate(items, 1):
            m = item["metrics"]
            published = (item.get("published_at") or "")[:10] or "-"
            if item.get("published_at_precision") == "approx":
                published = f"~{published}"
            kind = "" if item.get("content_type") == "video" else f"[{item.get('content_type')}] "
            ratio = m.get("views_vs_channel_median") if baseline else m.get("views_per_subscriber")
            channel = f"[{md_cell(item.get('author'))}]({item['author_url']})" if item.get("author_url") else md_cell(item.get("author"))
            lines.append(
                f"| {idx} | {kind}[{md_cell(truncate(item.get('title'), 60))}]({item.get('url')}) | {channel} "
                f"({fmt_num(m.get('subscribers'))}) | {published} | {fmt_duration(item.get('duration_seconds'))} | {fmt_num(m.get('views'))} "
                f"| {fmt_num(m.get('views_per_day'))} | {fmt_num(m.get('like_rate'))} | {f'{ratio:.2f}x' if ratio is not None else '-'} |"
            )
    if items and details:
        lines += ["", "## Details", ""]
        for idx, item in enumerate(items, 1):
            lines.append(f"{idx}. **{md_cell(item.get('title'))}** `{item.get('source_id')}`")
            if item.get("title_localized"):
                lines.append(f"   - Shown on YouTube as: {item['title_localized']}")
            if item.get("text"):
                lines.append(f"   - {truncate(item['text'], 200)}")
            extras = []
            if item.get("tags"):
                extras.append("tags: " + ", ".join(item["tags"][:8]))
            if len(item.get("found_by_queries") or []) > 1:
                extras.append("found by: " + " / ".join(item["found_by_queries"]))
            if item.get("has_transcript") is not None:
                extras.append(f"transcript: {'yes' if item['has_transcript'] else 'no'}")
            elif item.get("has_captions"):
                extras.append("captions: yes")
            if extras:
                lines.append("   - " + " ・ ".join(extras))
    if result.get("comments"):
        lines += ["", "## Comments"]
        current = None
        for comment in result["comments"]:
            if comment.get("video_id") != current:
                current = comment.get("video_id")
                title = comment.get("video_title")
                lines += ["", f"### {md_cell(title) + ' ' if title else ''}(https://youtu.be/{current})", ""]
            flags = "".join(flag for flag, on in (("📌", comment.get("is_pinned")), ("🎙", comment.get("author_is_uploader"))) if on)
            replies = f" 💬{comment['reply_count']}" if comment.get("reply_count") else ""
            lines.append(f"- 👍{comment.get('like_count') or 0}{replies}{flags} {comment.get('text')}")
    if result.get("trends"):
        lines += ["", "## Trends", "", "| Seed | Keyword | Score (top: 0-100 / rising: growth) | Rising |", "|---|---|---|---|"]
        for trend in result["trends"]:
            score = f"+{trend.get('score')}%" if trend.get("rising") else trend.get("score")
            lines.append(f"| {md_cell(trend.get('seed_query'))} | {md_cell(trend.get('keyword'))} | {score} | {'yes' if trend.get('rising') else ''} |")
    if result.get("queries_tried"):
        lines += ["", "## Search log", ""]
        for log in result["queries_tried"]:
            lines.append(
                f"- {log.get('endpoint')} `{log.get('query')}` quota={log.get('quota_cost')} "
                f"{log.get('result_count_before_filter')}→{log.get('result_count_after_filter')} {log.get('memo') or ''}".rstrip()
            )
    if result.get("excluded_summary"):
        lines.append("- Excluded: " + ", ".join(f"{e['reason']}={e['count']}" for e in result["excluded_summary"]))
    if result.get("limitations"):
        lines += ["", "## Limitations", ""]
        for item in result["limitations"]:
            lines.append(f"- {item.get('code')}: {item.get('message')}")
    if result.get("next_human_actions"):
        lines += ["", "## Next human actions", ""]
        for item in result["next_human_actions"]:
            lines.append(f"- {item.get('code')}: {item.get('message')}")
    return "\n".join(lines) + "\n"


CSV_ITEM_COLUMNS = [
    ("rank", lambda i: i.get("rank")),
    ("content_type", lambda i: i.get("content_type")),
    ("title", lambda i: i.get("title")),
    ("url", lambda i: i.get("url")),
    ("channel", lambda i: i.get("author")),
    ("channel_url", lambda i: i.get("author_url")),
    ("subscribers", lambda i: i["metrics"].get("subscribers")),
    ("published_at", lambda i: i.get("published_at")),
    ("duration_seconds", lambda i: i.get("duration_seconds")),
    ("views", lambda i: i["metrics"].get("views")),
    ("likes", lambda i: i["metrics"].get("likes")),
    ("comments", lambda i: i["metrics"].get("comments")),
    ("views_per_day", lambda i: i["metrics"].get("views_per_day")),
    ("like_rate", lambda i: i["metrics"].get("like_rate")),
    ("views_per_subscriber", lambda i: i["metrics"].get("views_per_subscriber")),
    ("views_vs_channel_median", lambda i: i["metrics"].get("views_vs_channel_median")),
    ("video_count", lambda i: i["metrics"].get("video_count")),
    ("last_upload_at", lambda i: (i.get("activity") or {}).get("last_upload_at")),
    ("recent_video_median_views", lambda i: (i.get("activity") or {}).get("recent_video_median_views")),
    ("found_by_queries", lambda i: " | ".join(i.get("found_by_queries") or [])),
    ("title_localized", lambda i: i.get("title_localized")),
    ("description", lambda i: i.get("text")),
]
CSV_COMMENT_COLUMNS = ["video_id", "video_title", "like_count", "reply_count", "published_at", "is_pinned", "author_is_uploader", "text"]
CSV_TREND_COLUMNS = ["seed_query", "keyword", "score", "rising"]


def render_csv(result: dict[str, Any]) -> str:
    """Items if any, otherwise comments, otherwise trends (one table per CSV)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if result.get("items"):
        writer.writerow([name for name, _ in CSV_ITEM_COLUMNS])
        for item in result["items"]:
            writer.writerow([getter(item) for _, getter in CSV_ITEM_COLUMNS])
    elif result.get("comments"):
        writer.writerow(CSV_COMMENT_COLUMNS)
        for comment in result["comments"]:
            writer.writerow([comment.get(name) for name in CSV_COMMENT_COLUMNS])
    else:
        writer.writerow(CSV_TREND_COLUMNS)
        for trend in result.get("trends") or []:
            writer.writerow([trend.get(name) for name in CSV_TREND_COLUMNS])
    return buffer.getvalue()


def run_render(args: argparse.Namespace) -> dict[str, Any]:
    """Merge saved result JSON files and re-rank them locally, without new requests."""
    results = []
    for path in args.input:
        try:
            with open(path, encoding="utf-8") as handle:
                results.append(json.load(handle))
        except (OSError, ValueError) as exc:
            raise SearchError("input_unreadable", f"Could not read {path}: {exc}") from exc
    merged = dict(results[0])
    merged["subcommand"] = results[0].get("subcommand") if len(results) == 1 else "render"
    merged["tool"] = " + ".join(dict.fromkeys(r.get("tool") or "?" for r in results))
    merged["inputs"] = list(args.input)
    queries = list(dict.fromkeys(q for r in results for q in (r.get("queries") or ([r["query"]] if r.get("query") else []))))
    merged["queries"] = queries
    merged["query"] = " | ".join(queries) or None
    merged["quota_estimate"] = sum(r.get("quota_estimate") or 0 for r in results)
    items: dict[str, dict[str, Any]] = {}
    for r in results:
        for item in r.get("items") or []:
            known = items.get(item["source_id"])
            if known:
                known["found_by_queries"] = list(dict.fromkeys([*(known.get("found_by_queries") or []), *(item.get("found_by_queries") or [])]))
            else:
                items[item["source_id"]] = dict(item)
    for key in ("comments", "trends", "queries_tried", "limitations", "next_human_actions"):
        seen: list[Any] = []
        for r in results:
            for entry in r.get(key) or []:
                if entry not in seen:
                    seen.append(entry)
        merged[key] = seen
    kept = []
    for item in items.values():
        haystack = f"{item.get('title') or ''} {item.get('text') or ''} {' '.join(item.get('tags') or [])}".lower()
        if args.exclude and any(term.lower() in haystack for term in args.exclude):
            continue
        if args.include and not any(term.lower() in haystack for term in args.include):
            continue
        if item.get("content_type") != "channel" and (item["metrics"].get("views") or 0) < args.min_views:
            continue
        kept.append(item)
    if args.sort not in {"auto", "youtube"}:
        kept.sort(key=SORT_KEYS[args.sort], reverse=True)
    if args.max_per_channel:
        counts: Counter[str] = Counter()
        limited = []
        for item in kept:
            channel = item.get("channel_id") or item.get("author") or ""
            if item.get("content_type") != "channel" and counts[channel] >= args.max_per_channel:
                continue
            counts[channel] += 1
            limited.append(item)
        kept = limited
    kept = kept[: args.limit] if args.limit else kept
    for position, item in enumerate(kept, 1):
        item["rank"] = position
    merged["items"] = kept
    merged["summary"] = {**build_summary(merged, len(items)), **({"channel_baselines": results[0]["summary"]["channel_baselines"]} if len(results) == 1 and (results[0].get("summary") or {}).get("channel_baselines") else {})}
    if len(results) > 1:
        merged["limitations"].append(message("merged_results", f"Merged {len(results)} saved results fetched at {', '.join(sorted({r.get('fetched_at') or '?' for r in results}))}; metrics are from each fetch time."))
    return merged


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["auto", "json", "markdown", "csv"], default="auto", help="auto: markdown when --output is given, otherwise json")
    parser.add_argument("--output", help="also write the full JSON to this path")
    parser.add_argument("--details", action="store_true", help="markdown: add description, tags, matched queries and transcript availability per item")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--purpose", default="social_marketing_research")
    parser.add_argument("--backend", choices=["auto", "api", "web"], default="auto", help="auto: api if YOUTUBE_API_KEY is set, else web")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--region", default="JP")
    parser.add_argument("--period", default="30d", help="e.g. 24h, 7d, 4w, 3m, 1y, 2026-01-01..2026-03-31")
    parser.add_argument("--query", action="append", default=[], help="repeatable; results are merged and de-duplicated")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--max-fetch", type=int, help="candidates fetched per query / channel before filtering")
    parser.add_argument("--min-views", type=int, default=1000)
    parser.add_argument("--min-subscribers", type=int, default=0)
    parser.add_argument("--max-per-channel", type=int, default=2)
    parser.add_argument("--sort", choices=SORT_CHOICES, default="auto", help="local ranking after filtering")
    parser.add_argument("--shorts", choices=["exclude", "include", "only"], default="exclude")
    parser.add_argument("--allow-live", action="store_true")
    add_output_arguments(parser)
    parser.add_argument("--cookies", help="web: Netscape cookies.txt passed to yt-dlp (helps when YouTube rate-limits)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    diag = subparsers.add_parser("diag", help="check which routes (API key, web, yt-dlp, RSS) work right now")
    add_common_arguments(diag)

    lookup = subparsers.add_parser("lookup", help="full metadata for video URLs / IDs")
    add_common_arguments(lookup)
    lookup.add_argument("--url", action="append", default=[])
    lookup.add_argument("--id", action="append", default=[])
    lookup.add_argument("--no-deep", action="store_true", help="web: skip yt-dlp (tags, captions, duration)")

    search = subparsers.add_parser("search", help="discover videos or channels from keywords")
    add_common_arguments(search)
    search.add_argument("--order", choices=["relevance", "viewCount", "date"], default="relevance")
    search.add_argument("--type", choices=["video", "channel"], default="video")
    search.add_argument("--video-duration", choices=["short", "medium", "long"])
    search.add_argument("--no-channel-enrich", action="store_true", help="api: skip channels.list")
    search.add_argument("--no-enrich", action="store_true", help="web: skip per-video detail enrichment")
    search.add_argument("--deep", action="store_true", help="web: also fetch tags/category/captions/duration via yt-dlp (slower, rate-limit prone)")
    search.add_argument("--enrich-top", type=int, help=f"web: number of pre-filtered candidates to enrich (default max({DEFAULT_ENRICH_CAP}, limit))")
    search.add_argument("--comments", type=int, default=0, metavar="N", help="also fetch comments for the top N selected videos")
    search.add_argument("--comments-per-video", type=int, default=10)
    search.add_argument("--comment-order", choices=["relevance", "time"], default="relevance")
    search.add_argument("--include-creator-comments", action="store_true")
    search.add_argument("--no-activity", action="store_true", help="--type channel: skip the RSS check of last upload and recent views")

    channel = subparsers.add_parser("channel", help="uploads of known channels (@handle, channel URL/ID, or any video URL of the channel)")
    add_common_arguments(channel)
    channel.add_argument("--channels", action="append", required=True)
    channel.add_argument("--deep", action="store_true", help="web: also fetch tags/category/captions via yt-dlp")

    monitor = subparsers.add_parser("monitor", help="latest uploads of known channels via RSS")
    add_common_arguments(monitor)
    monitor.add_argument("--channels", action="append", required=True)

    comments = subparsers.add_parser("comments", help="representative comments for videos")
    add_common_arguments(comments)
    comments.add_argument("--video-ids", action="append", required=True)
    comments.add_argument("--comment-top-n", type=int, default=5)
    comments.add_argument("--comment-order", choices=["relevance", "time"], default="relevance")
    comments.add_argument("--include-creator-comments", action="store_true", help="web: keep the uploader's own (often pinned promo) comments")

    trends = subparsers.add_parser("trends", help="related YouTube searches from Google Trends")
    add_common_arguments(trends)

    render = subparsers.add_parser("render", help="re-rank / merge saved result JSON files without new requests")
    render.add_argument("--input", action="append", required=True, help="saved result JSON (repeatable; items are merged)")
    render.add_argument("--sort", choices=SORT_CHOICES, default="auto", help="auto keeps the saved order")
    render.add_argument("--limit", type=int, default=0, help="0 = all")
    render.add_argument("--min-views", type=int, default=0)
    render.add_argument("--max-per-channel", type=int, default=0, help="0 = no limit")
    render.add_argument("--include", action="append", default=[])
    render.add_argument("--exclude", action="append", default=[])
    add_output_arguments(render)

    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.subcommand in {"search", "trends"} and not queries_of(args):
        raise SearchError("query_missing", f"--query is required for {args.subcommand}.")
    runner = {
        "diag": run_diag,
        "lookup": run_lookup,
        "search": run_search,
        "channel": run_channel,
        "monitor": run_monitor,
        "comments": run_comments,
        "trends": run_trends,
        "render": run_render,
    }.get(args.subcommand)
    if runner is None:
        raise SearchError("unknown_subcommand", f"Unknown subcommand: {args.subcommand}")
    return runner(args)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    global COOKIES_FILE
    COOKIES_FILE = getattr(args, "cookies", None)
    try:
        result = execute(args)
    except SearchError as exc:
        result = empty_result(args, args.subcommand, resolve_backend(args))
        result["limitations"].append(message(exc.code, exc.message))
        result["next_human_actions"].append(message("resolve_error", exc.message))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    output_format = args.format if args.format != "auto" else ("markdown" if args.output else "json")
    if output_format == "markdown":
        print(render_markdown(result, details=args.details))
    elif output_format == "csv":
        sys.stdout.write(render_csv(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
