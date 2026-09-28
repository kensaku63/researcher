#!/usr/bin/env python3
"""YouTube research helper that returns normalized, report-ready JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from typing import Any

import requests


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
OEMBED_URL = "https://www.youtube.com/oembed"
RSS_URL = "https://www.youtube.com/feeds/videos.xml"
DEFAULT_TIMEOUT = 20
MAX_TEXT = 300
MAX_COMMENT_TEXT = 200


class SearchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


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
        elif parsed.path.startswith(("/shorts/", "/embed/")):
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
    path = parsed.path.strip("/")
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


def period_bounds(period: str) -> tuple[str | None, str | None]:
    period = period.strip()
    now = dt.datetime.now(dt.timezone.utc)
    if ".." in period:
        start, end = period.split("..", 1)
        return normalize_date(start, start_of_day=True), normalize_date(end, start_of_day=False)
    match = re.fullmatch(r"(\d+)([hdmy])", period)
    if not match:
        return None, None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        delta = dt.timedelta(hours=amount)
    elif unit == "d":
        delta = dt.timedelta(days=amount)
    elif unit == "m":
        delta = dt.timedelta(days=amount * 30)
    else:
        delta = dt.timedelta(days=amount * 365)
    return (now - delta).isoformat().replace("+00:00", "Z"), None


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
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def in_period(published_at: str | None, period: str) -> bool:
    published = parse_iso_datetime(published_at)
    if not published:
        return True
    start_raw, end_raw = period_bounds(period)
    start = parse_iso_datetime(start_raw)
    end = parse_iso_datetime(end_raw)
    if start and published < start:
        return False
    if end and published > end:
        return False
    return True


def api_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY")


def require_api_key() -> str:
    key = api_key()
    if not key:
        raise SearchError("missing_api_key", "YOUTUBE_API_KEY is required for this subcommand.")
    return key


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
        raise SearchError("forbidden", "YouTube API returned 403. The key may lack access or quota.")
    if response.status_code == 404:
        raise SearchError("not_found", "YouTube API returned 404.")
    if response.status_code >= 400:
        code = "quota_exceeded" if "quota" in response.text.lower() else "api_error"
        raise SearchError(code, f"YouTube API returned HTTP {response.status_code}.")
    return response.json(), {
        "endpoint": endpoint,
        "query": str(params.get("q") or params.get("id") or params.get("channelId") or params.get("playlistId") or ""),
        "quota_cost": quota_cost,
        "result_count_before_filter": None,
        "result_count_after_filter": None,
    }


def empty_result(args: argparse.Namespace, subcommand: str, tool: str) -> dict[str, Any]:
    return {
        "platform": "youtube",
        "purpose": getattr(args, "purpose", "social_marketing_research"),
        "subcommand": subcommand,
        "tool": tool,
        "query": getattr(args, "query", None),
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
        "items": [],
        "comments": [],
        "trends": [],
        "queries_tried": [],
        "excluded_summary": [],
        "limitations": [],
        "next_human_actions": [],
    }


def item_from_video(video: dict[str, Any], channel_metrics: dict[str, dict[str, int | None]] | None = None) -> dict[str, Any]:
    snippet = video.get("snippet") or {}
    stats = video.get("statistics") or {}
    channel_id = snippet.get("channelId")
    metrics = {
        "views": safe_int(stats.get("viewCount")),
        "likes": safe_int(stats.get("likeCount")),
        "comments": safe_int(stats.get("commentCount")),
        "subscribers": None,
        "video_count": None,
    }
    if channel_metrics and channel_id in channel_metrics:
        metrics.update(channel_metrics[channel_id])
    return {
        "url": f"https://www.youtube.com/watch?v={video.get('id')}",
        "source_id": video.get("id"),
        "author": snippet.get("channelTitle"),
        "author_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
        "published_at": snippet.get("publishedAt"),
        "title": snippet.get("title"),
        "text": truncate(snippet.get("description")),
        "metrics": metrics,
        "matched_terms": [],
        "quality_score": None,
        "selection_filters": [],
        "why_selected": "",
        "limitations": [],
        "channel_id": channel_id,
        "duration": (video.get("contentDetails") or {}).get("duration"),
        "default_audio_language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
    }


def channel_metrics_from_response(channels: list[dict[str, Any]]) -> dict[str, dict[str, int | None]]:
    result: dict[str, dict[str, int | None]] = {}
    for channel in channels:
        stats = channel.get("statistics") or {}
        result[channel.get("id")] = {
            "subscribers": safe_int(stats.get("subscriberCount")),
            "video_count": safe_int(stats.get("videoCount")),
        }
    return result


def matched_terms(item: dict[str, Any], query: str | None, include: list[str]) -> list[str]:
    text = f"{item.get('title') or ''} {item.get('text') or ''}".lower()
    terms = []
    for term in [query or "", *include]:
        for token in re.split(r"\s+", term.strip()):
            if token and token.lower() in text and token not in terms:
                terms.append(token)
    return terms[:20]


def quality_score(item: dict[str, Any]) -> float:
    metrics = item.get("metrics") or {}
    views = metrics.get("views") or 0
    likes = metrics.get("likes")
    view_score = math.log10(max(views, 1)) / 8
    like_ratio = (likes / views) if views and likes is not None else 0
    like_score = min(like_ratio * 20, 1)
    published = parse_iso_datetime(item.get("published_at"))
    if published:
        days_since = max((dt.datetime.now(dt.timezone.utc) - published).days, 0)
        recency_score = max(1 - days_since / 1825, 0)
    else:
        recency_score = 0
    return round(view_score * 0.4 + like_score * 0.3 + recency_score * 0.3, 4)


def filter_items(
    items: list[dict[str, Any]],
    args: argparse.Namespace,
    preserve_order: bool,
    apply_channel_limit: bool,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    excluded: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    include = getattr(args, "include", []) or []
    exclude = getattr(args, "exclude", []) or []
    min_views = getattr(args, "min_views", 1000)

    for item in items:
        filters = []
        title_text = f"{item.get('title') or ''} {item.get('text') or ''}".lower()
        if exclude and any(term.lower() in title_text for term in exclude):
            excluded["excluded_term"] += 1
            continue
        if include and not any(term.lower() in title_text for term in include):
            excluded["no_matched_terms"] += 1
            continue
        if not in_period(item.get("published_at"), getattr(args, "period", "30d")):
            excluded["out_of_period"] += 1
            continue
        filters.append("in_period")
        metrics = item.get("metrics") or {}
        views = metrics.get("views")
        if views is not None and views < min_views:
            excluded["below_min_views"] += 1
            continue
        filters.append("min_views_ok")
        lang = item.get("default_audio_language")
        if lang and getattr(args, "language", "") and not str(lang).lower().startswith(args.language.lower()):
            excluded["language_mismatch"] += 1
            continue
        filters.append("language_match" if lang else "language_undetectable")
        item["matched_terms"] = matched_terms(item, getattr(args, "query", None), include)
        item["quality_score"] = quality_score(item)
        item["selection_filters"] = filters
        kept.append(item)

    if not preserve_order:
        kept.sort(key=lambda item: item.get("quality_score") or 0, reverse=True)

    if apply_channel_limit and getattr(args, "max_per_channel", 2) > 0:
        limited = []
        seen: defaultdict[str, int] = defaultdict(int)
        for item in kept:
            channel_id = item.get("channel_id") or item.get("author")
            if channel_id and seen[channel_id] >= args.max_per_channel:
                excluded["same_channel_limit"] += 1
                continue
            if channel_id:
                seen[channel_id] += 1
            item["selection_filters"].append("channel_diversity_ok")
            limited.append(item)
        kept = limited

    final = kept[: getattr(args, "limit", 15)]
    for item in final:
        metrics = item.get("metrics") or {}
        item["why_selected"] = (
            f"views={metrics.get('views')} comments={metrics.get('comments')} "
            f"quality_score={item.get('quality_score')} filters={','.join(item.get('selection_filters') or [])}"
        )
    return final, excluded


def fetch_videos(video_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not video_ids:
        return [], {
            "endpoint": "videos.list",
            "query": "",
            "quota_cost": 0,
            "result_count_before_filter": 0,
            "result_count_after_filter": 0,
        }
    data, log = youtube_get(
        "videos",
        {
            "part": "snippet,contentDetails,statistics,status",
            "id": ",".join(video_ids[:50]),
            "maxResults": 50,
        },
        quota_cost=1,
    )
    items = data.get("items") or []
    log["query"] = f"{len(video_ids[:50])} video ids"
    log["result_count_before_filter"] = len(video_ids[:50])
    log["result_count_after_filter"] = len(items)
    return items, log


def fetch_channels(channel_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ids = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not ids:
        return [], {
            "endpoint": "channels.list",
            "query": "",
            "quota_cost": 0,
            "result_count_before_filter": 0,
            "result_count_after_filter": 0,
        }
    data, log = youtube_get(
        "channels",
        {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(ids[:50]),
            "maxResults": 50,
        },
        quota_cost=1,
    )
    items = data.get("items") or []
    log["query"] = f"{len(ids[:50])} channel ids"
    log["result_count_before_filter"] = len(ids[:50])
    log["result_count_after_filter"] = len(items)
    return items, log


def resolve_channels(inputs: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    for raw in inputs:
        parsed = parse_channel_input(raw)
        if parsed["channel_id"]:
            channels, log = fetch_channels([parsed["channel_id"]])
            resolved.extend(channels)
            logs.append(log)
            continue
        handle = parsed["handle"]
        if not handle:
            continue
        data, log = youtube_get(
            "channels",
            {
                "part": "snippet,contentDetails,statistics",
                "forHandle": handle,
                "maxResults": 1,
            },
            quota_cost=1,
        )
        items = data.get("items") or []
        log["query"] = f"@{handle}"
        log["result_count_before_filter"] = 1
        log["result_count_after_filter"] = len(items)
        resolved.extend(items)
        logs.append(log)
    return resolved, logs


def run_diag(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "diag", "diagnostic")
    key = api_key()
    if not key:
        result["limitations"].append(message("missing_api_key", "YOUTUBE_API_KEY is not set."))
        result["next_human_actions"].append(message("set_youtube_api_key", "Inject YOUTUBE_API_KEY before API searches."))
    else:
        try:
            data, log = youtube_get("videos", {"part": "id", "id": "dQw4w9WgXcQ"}, quota_cost=1)
            log["result_count_before_filter"] = 1
            log["result_count_after_filter"] = len(data.get("items") or [])
            result["queries_tried"].append(log)
            result["quota_estimate"] += log["quota_cost"]
        except SearchError as exc:
            result["limitations"].append(message(exc.code, exc.message))
    try:
        response = requests.get(RSS_URL, params={"channel_id": "UCBR8-60-B28hp2BmDPdntcQ"}, timeout=DEFAULT_TIMEOUT)
        result["queries_tried"].append(
            {
                "endpoint": "rss",
                "query": "UCBR8-60-B28hp2BmDPdntcQ",
                "quota_cost": 0,
                "result_count_before_filter": 1,
                "result_count_after_filter": 1 if response.ok else 0,
                "memo": f"HTTP {response.status_code}",
            }
        )
        if not response.ok:
            result["limitations"].append(message("rss_unreachable", f"RSS returned HTTP {response.status_code}."))
    except requests.RequestException as exc:
        result["limitations"].append(message("rss_unreachable", f"RSS check failed: {exc}"))
    return result


def run_lookup(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "lookup", "oembed")
    value = args.url or args.id
    video_id = parse_video_id(value)
    if not video_id:
        result["limitations"].append(message("url_invalid", "Could not normalize a YouTube video URL or ID."))
        result["next_human_actions"].append(message("provide_video_url", "Provide a YouTube watch URL or 11-character video ID."))
        return result

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = requests.get(OEMBED_URL, params={"url": url, "format": "json"}, timeout=DEFAULT_TIMEOUT)
        result["queries_tried"].append(
            {
                "endpoint": "oembed",
                "query": url,
                "quota_cost": 0,
                "result_count_before_filter": 1,
                "result_count_after_filter": 1 if response.ok else 0,
            }
        )
        if response.ok:
            data = response.json()
            result["items"].append(
                {
                    "url": url,
                    "source_id": video_id,
                    "author": data.get("author_name"),
                    "author_url": data.get("author_url"),
                    "published_at": None,
                    "title": data.get("title"),
                    "text": None,
                    "metrics": {"views": None, "likes": None, "comments": None, "subscribers": None, "video_count": None},
                    "matched_terms": [],
                    "quality_score": None,
                    "selection_filters": ["oembed_ok"],
                    "why_selected": "oEmbed confirmed the video URL and title.",
                    "limitations": [],
                }
            )
        else:
            result["limitations"].append(message("oembed_failed", f"oEmbed returned HTTP {response.status_code}."))
    except requests.RequestException as exc:
        result["limitations"].append(message("oembed_failed", f"oEmbed failed: {exc}"))

    if api_key():
        videos, video_log = fetch_videos([video_id])
        result["queries_tried"].append(video_log)
        result["quota_estimate"] += video_log["quota_cost"]
        if videos:
            channels, channel_log = fetch_channels([videos[0].get("snippet", {}).get("channelId")])
            result["queries_tried"].append(channel_log)
            result["quota_estimate"] += channel_log["quota_cost"]
            metrics = channel_metrics_from_response(channels)
            item = item_from_video(videos[0], metrics)
            item["why_selected"] = "videos.list enriched the oEmbed lookup with public metrics."
            result["items"] = [item]
    else:
        result["limitations"].append(message("missing_api_key", "Metrics were not enriched because YOUTUBE_API_KEY is not set."))
    return result


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "search", "youtube_data_api")
    start, end = period_bounds(args.period)
    fetch_count = min(args.max_fetch or args.limit * 3, 50)
    search_params = {
        "part": "snippet",
        "q": args.query,
        "type": args.type if args.type != "video+channel" else "video",
        "order": args.order,
        "regionCode": args.region,
        "relevanceLanguage": args.language,
        "publishedAfter": start,
        "publishedBefore": end,
        "videoDuration": args.video_duration,
        "videoEmbeddable": "true",
        "videoDimension": None if args.allow_shorts else "2d",
        "safeSearch": "moderate",
        "maxResults": fetch_count,
    }
    data, search_log = youtube_get("search", search_params, quota_cost=100)
    search_items = data.get("items") or []
    video_ids = []
    for item in search_items:
        video_id = (item.get("id") or {}).get("videoId")
        if video_id and video_id not in video_ids:
            video_ids.append(video_id)
    search_log["result_count_before_filter"] = len(search_items)
    search_log["result_count_after_filter"] = len(video_ids)
    result["queries_tried"].append(search_log)
    result["quota_estimate"] += 100

    videos, video_log = fetch_videos(video_ids)
    result["queries_tried"].append(video_log)
    result["quota_estimate"] += video_log["quota_cost"]
    channel_ids = [video.get("snippet", {}).get("channelId") for video in videos]
    channel_metrics: dict[str, dict[str, int | None]] = {}
    if not args.no_channel_enrich:
        channels, channel_log = fetch_channels([cid for cid in channel_ids if cid])
        result["queries_tried"].append(channel_log)
        result["quota_estimate"] += channel_log["quota_cost"]
        channel_metrics = channel_metrics_from_response(channels)
    items = [item_from_video(video, channel_metrics) for video in videos]
    filtered, excluded = filter_items(
        items,
        args,
        preserve_order=args.order in {"date", "viewCount"},
        apply_channel_limit=True,
    )
    result["items"] = filtered
    result["excluded_summary"] = compact_counts(excluded)
    if not filtered:
        result["limitations"].append(message("result_empty", "No videos remained after filtering."))
    return result


def run_channel(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "channel", "youtube_data_api")
    channels, logs = resolve_channels(args.channels)
    result["queries_tried"].extend(logs)
    result["quota_estimate"] += sum(log["quota_cost"] for log in logs)
    if not channels:
        result["limitations"].append(message("channel_id_unknown", "Could not resolve any channel inputs."))
        result["next_human_actions"].append(message("provide_channel", "Provide a channel ID, channel URL, or @handle."))
        return result

    upload_playlist_ids = []
    channel_lookup = {}
    for channel in channels:
        channel_id = channel.get("id")
        uploads = (((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
        if uploads:
            upload_playlist_ids.append(uploads)
            channel_lookup[uploads] = channel
        else:
            result["limitations"].append(message("uploads_playlist_missing", f"Uploads playlist missing for channel {channel_id}."))

    video_ids: list[str] = []
    playlist_logs: list[dict[str, Any]] = []
    for playlist_id in upload_playlist_ids:
        data, log = youtube_get(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(args.max_fetch or args.limit * 3, 50),
            },
            quota_cost=1,
        )
        items = data.get("items") or []
        ids = [((item.get("contentDetails") or {}).get("videoId")) for item in items]
        ids = [video_id for video_id in ids if video_id]
        video_ids.extend(ids)
        log["query"] = playlist_id
        log["result_count_before_filter"] = len(items)
        log["result_count_after_filter"] = len(ids)
        playlist_logs.append(log)
    result["queries_tried"].extend(playlist_logs)
    result["quota_estimate"] += sum(log["quota_cost"] for log in playlist_logs)

    videos, video_log = fetch_videos(list(dict.fromkeys(video_ids)))
    result["queries_tried"].append(video_log)
    result["quota_estimate"] += video_log["quota_cost"]
    channel_metrics = channel_metrics_from_response(channels)
    items = [item_from_video(video, channel_metrics) for video in videos]
    filtered, excluded = filter_items(items, args, preserve_order=True, apply_channel_limit=False)
    for item in filtered:
        item["why_selected"] = f"Selected from the channel uploads playlist. {item['why_selected']}"
    result["items"] = filtered
    result["excluded_summary"] = compact_counts(excluded)
    if not filtered:
        result["limitations"].append(message("result_empty", "No channel videos remained after filtering."))
    return result


def run_monitor(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "monitor", "rss")
    inputs = args.channels
    channel_ids: list[str] = []
    unresolved: list[str] = []
    for value in inputs:
        parsed = parse_channel_input(value)
        if parsed["channel_id"]:
            channel_ids.append(parsed["channel_id"])
        else:
            unresolved.append(value)
    if unresolved and api_key():
        channels, logs = resolve_channels(unresolved)
        result["queries_tried"].extend(logs)
        result["quota_estimate"] += sum(log["quota_cost"] for log in logs)
        channel_ids.extend([channel.get("id") for channel in channels if channel.get("id")])
    elif unresolved:
        result["limitations"].append(message("channel_id_unknown", "RSS monitor needs channel IDs unless YOUTUBE_API_KEY is available for handle resolution."))

    for channel_id in channel_ids:
        try:
            response = requests.get(RSS_URL, params={"channel_id": channel_id}, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            result["limitations"].append(message("rss_unreachable", f"RSS failed for {channel_id}: {exc}"))
            continue
        result["queries_tried"].append(
            {
                "endpoint": "rss",
                "query": channel_id,
                "quota_cost": 0,
                "result_count_before_filter": None,
                "result_count_after_filter": None,
            }
        )
        if not response.ok:
            result["limitations"].append(message("rss_unreachable", f"RSS returned HTTP {response.status_code} for {channel_id}."))
            continue
        root = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        entries = root.findall("atom:entry", ns)
        for entry in entries[: args.limit]:
            video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
            title = entry.findtext("atom:title", default="", namespaces=ns)
            author = entry.findtext("atom:author/atom:name", default="", namespaces=ns)
            published = entry.findtext("atom:published", default="", namespaces=ns)
            if not in_period(published, args.period):
                continue
            result["items"].append(
                {
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "source_id": video_id,
                    "author": author,
                    "author_url": f"https://www.youtube.com/channel/{channel_id}",
                    "published_at": published,
                    "title": title,
                    "text": None,
                    "metrics": {"views": None, "likes": None, "comments": None, "subscribers": None, "video_count": None},
                    "matched_terms": [],
                    "quality_score": None,
                    "selection_filters": ["rss_entry", "in_period"],
                    "why_selected": "Selected from the channel RSS feed.",
                    "limitations": [message("metric_missing", "RSS does not include public metrics.")],
                    "channel_id": channel_id,
                }
            )
    if not result["items"]:
        result["limitations"].append(message("result_empty", "No RSS entries were found for the provided channels and period."))
    return result


def run_comments(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "comments", "youtube_data_api")
    video_ids = []
    for value in args.video_ids:
        video_id = parse_video_id(value) or value
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            video_ids.append(video_id)
    if not video_ids:
        result["limitations"].append(message("url_invalid", "No valid video IDs were provided."))
        return result

    for video_id in video_ids[: args.comment_top_n]:
        try:
            data, log = youtube_get(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": min(args.limit, 100),
                    "order": args.comment_order,
                    "textFormat": "plainText",
                },
                quota_cost=1,
            )
        except SearchError as exc:
            code = "comments_disabled" if exc.code in {"forbidden", "api_error"} else exc.code
            result["limitations"].append(message(code, f"Could not fetch comments for {video_id}: {exc.message}"))
            continue
        items = data.get("items") or []
        log["query"] = video_id
        log["result_count_before_filter"] = len(items)
        log["result_count_after_filter"] = len(items)
        result["queries_tried"].append(log)
        result["quota_estimate"] += log["quota_cost"]
        for item in items:
            snippet = (((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {})
            text = truncate(snippet.get("textDisplay"), MAX_COMMENT_TEXT)
            if not text:
                continue
            result["comments"].append(
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
    if not result["comments"]:
        result["limitations"].append(message("result_empty", "No comments were returned. Comments may be disabled or unavailable."))
    return result


def pytrends_timeframe(period: str) -> str:
    if period.endswith("d"):
        days = safe_int(period[:-1]) or 30
        if days <= 1:
            return "now 1-d"
        if days <= 7:
            return "now 7-d"
        if days <= 31:
            return "today 1-m"
        if days <= 90:
            return "today 3-m"
        return "today 12-m"
    if period.endswith("m"):
        months = safe_int(period[:-1]) or 3
        if months <= 1:
            return "today 1-m"
        if months <= 3:
            return "today 3-m"
        return "today 12-m"
    return "today 3-m"


def run_trends(args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result(args, "trends", "google_trends")
    try:
        from pytrends.request import TrendReq  # type: ignore
    except ImportError:
        result["limitations"].append(message("trends_unavailable", "pytrends is not installed."))
        result["next_human_actions"].append(message("install_pytrends", "Install pytrends or run in the declared environment."))
        return result

    try:
        trend = TrendReq(hl=f"{args.language}-{args.region}", tz=0)
        trend.build_payload(
            [args.query],
            cat=0,
            timeframe=pytrends_timeframe(args.period),
            geo=args.region,
            gprop="youtube",
        )
        related = trend.related_queries()
        queries = []
        related_for_query = related.get(args.query) or {}
        for section_name in ("top", "rising"):
            table = related_for_query.get(section_name)
            if table is None:
                continue
            for row in table.head(args.limit).to_dict("records"):
                queries.append(
                    {
                        "keyword": row.get("query"),
                        "score": safe_int(row.get("value")),
                        "rising": section_name == "rising",
                        "related_queries": [],
                        "region": args.region,
                        "period": args.period,
                    }
                )
        seen = set()
        for item in queries:
            key = item.get("keyword")
            if not key or key in seen:
                continue
            seen.add(key)
            result["trends"].append(item)
        result["queries_tried"].append(
            {
                "endpoint": "google_trends_youtube_search",
                "query": args.query,
                "quota_cost": 0,
                "result_count_before_filter": len(queries),
                "result_count_after_filter": len(result["trends"]),
                "memo": "Values are relative 0-100 scores, not absolute search volume.",
            }
        )
        result["limitations"].append(message("relative_score", "Google Trends scores are relative and sampled, not absolute search volume."))
    except Exception as exc:  # noqa: BLE001
        result["limitations"].append(message("trends_unavailable", f"Google Trends request failed: {exc}"))
    if not result["trends"]:
        result["limitations"].append(message("result_empty", "No related Trends queries were returned."))
    return result


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# YouTube Search Result",
        "",
        f"- Purpose: {result.get('purpose')}",
        f"- Subcommand: {result.get('subcommand')}",
        f"- Query: {result.get('query') or ''}",
        f"- Fetched at: {result.get('fetched_at')}",
        f"- Quota estimate: {result.get('quota_estimate')}",
        "",
        "## Items",
    ]
    for idx, item in enumerate(result.get("items") or [], 1):
        lines.extend(
            [
                f"### {idx}. {item.get('title') or item.get('source_id')}",
                f"- URL: {item.get('url')}",
                f"- Author: {item.get('author')}",
                f"- Published: {item.get('published_at')}",
                f"- Metrics: {json.dumps(item.get('metrics'), ensure_ascii=False)}",
                f"- Why selected: {item.get('why_selected')}",
                "",
            ]
        )
    if result.get("comments"):
        lines.append("## Comments")
        for comment in result["comments"]:
            lines.append(f"- {comment.get('video_id')}: {comment.get('text')}")
    if result.get("trends"):
        lines.append("## Trends")
        for trend in result["trends"]:
            lines.append(f"- {trend.get('keyword')}: {trend.get('score')}")
    if result.get("limitations"):
        lines.append("## Limitations")
        for item in result["limitations"]:
            lines.append(f"- {item.get('code')}: {item.get('message')}")
    return "\n".join(lines) + "\n"


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--purpose", default="social_marketing_research")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--region", default="JP")
    parser.add_argument("--period", default="30d")
    parser.add_argument("--query")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--max-fetch", type=int)
    parser.add_argument("--min-views", type=int, default=1000)
    parser.add_argument("--max-per-channel", type=int, default=2)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    diag = subparsers.add_parser("diag")
    add_common_arguments(diag)

    lookup = subparsers.add_parser("lookup")
    add_common_arguments(lookup)
    lookup.add_argument("--url")
    lookup.add_argument("--id")

    search = subparsers.add_parser("search")
    add_common_arguments(search)
    search.add_argument("--order", choices=["relevance", "viewCount", "date"], default="relevance")
    search.add_argument("--type", choices=["video", "channel", "video+channel"], default="video")
    search.add_argument("--video-duration", choices=["short", "medium", "long"])
    search.add_argument("--no-channel-enrich", action="store_true")
    search.add_argument("--allow-shorts", action="store_true")
    search.add_argument("--allow-live", action="store_true")

    channel = subparsers.add_parser("channel")
    add_common_arguments(channel)
    channel.add_argument("--channels", action="append", required=True)

    monitor = subparsers.add_parser("monitor")
    add_common_arguments(monitor)
    monitor.add_argument("--channels", action="append", required=True)

    comments = subparsers.add_parser("comments")
    add_common_arguments(comments)
    comments.add_argument("--video-ids", action="append", required=True)
    comments.add_argument("--comment-top-n", type=int, default=5)
    comments.add_argument("--comment-order", choices=["relevance", "time"], default="relevance")

    trends = subparsers.add_parser("trends")
    add_common_arguments(trends)

    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.subcommand == "diag":
        return run_diag(args)
    if args.subcommand == "lookup":
        return run_lookup(args)
    if args.subcommand == "search":
        if not args.query:
            raise SearchError("query_missing", "--query is required for search.")
        return run_search(args)
    if args.subcommand == "channel":
        return run_channel(args)
    if args.subcommand == "monitor":
        return run_monitor(args)
    if args.subcommand == "comments":
        return run_comments(args)
    if args.subcommand == "trends":
        if not args.query:
            raise SearchError("query_missing", "--query is required for trends.")
        return run_trends(args)
    raise SearchError("unknown_subcommand", f"Unknown subcommand: {args.subcommand}")


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except SearchError as exc:
        result = empty_result(args, args.subcommand, "youtube_data_api")
        result["limitations"].append(message(exc.code, exc.message))
        result["next_human_actions"].append(message("resolve_error", exc.message))
    if args.format == "markdown":
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
