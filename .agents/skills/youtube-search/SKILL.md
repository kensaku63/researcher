---
name: youtube-search
description: Execute YouTube research through YouTube web endpoints (no key needed), YouTube Data API, RSS, and Google Trends. Use after a focused plan exists. Returns normalized JSON (or a Markdown table) with selected videos, channels, comments, trends, derived metrics, query logs, quota estimate, limitations, and next human actions.
---

# YouTube Search

## Overview

Use `scripts/search.py` to collect public YouTube facts and normalize them into a small JSON shape. Do not return raw API responses to the user. Always preserve search conditions, endpoint usage, quota estimates, selection reasons, limitations, and failed searches.

Works **without an API key**. `--backend auto` (default) uses the Data API when `YOUTUBE_API_KEY` is set and the web backend otherwise, and falls back to web when the API quota is exhausted.

| backend | Discovery | Detail per video | Cost |
|---|---|---|---|
| `web` | YouTube web search (InnerTube `search`, same filters as the site) | InnerTube `next` + `player` (exact date, original title, views, likes, comment count, subscribers, duration, tags, category, transcript availability) | no quota; 2 requests per enriched video |
| `api` | `search.list` | `videos.list` + `channels.list` | 100 quota per query |

## Required Secret Names

- `YOUTUBE_API_KEY`: optional. Enables the `api` backend. Never write secret values to files, logs, reports, or debug output.

## Quick Commands

Run from the agent repo root with the Python environment from `environment.yaml` (`requests`, `yt-dlp`, `pytrends`).

```bash
S=.agents/skills/youtube-search/scripts/search.py

# Environment check (API key, web search/detail, yt-dlp, RSS)
python3 $S diag --format markdown

# Keyword discovery. --query is repeatable; results are merged and de-duplicated.
python3 $S search --query "生成AI 勉強法" --query "AI 勉強 効率化" --period 90d --limit 15 --sort velocity --format markdown --output /tmp/yt.json

# Find videos that beat their channel size (views / subscribers)
python3 $S search --query "Claude Code 使い方" --period 30d --sort outlier --min-views 5000

# Shorts only / videos + shorts
python3 $S search --query "生成AI 勉強法" --shorts only --period 30d --sort views

# Channel discovery (influencer research)
python3 $S search --query "生成AI 解説" --type channel --min-subscribers 10000 --limit 20

# Known channels (uploads, enriched)
python3 $S channel --channels @takedajuku --channels https://www.youtube.com/@usutaku --period 30d --sort views

# Cheap recent-upload monitor (RSS, latest 15 per channel)
python3 $S monitor --channels @usutaku --period 14d

# Comments for important videos (creator's own comments are skipped by default)
python3 $S comments --video-ids VIDEO_ID --video-ids https://youtu.be/VIDEO_ID --limit 30 --comment-order relevance

# URL / ID lookup (adds yt-dlp: spoken language, caption languages, chapters)
python3 $S lookup --url https://www.youtube.com/shorts/VIDEO_ID --id VIDEO_ID

# Related searches on YouTube (Google Trends, gprop=youtube)
python3 $S trends --query "生成AI" --query "ChatGPT" --period 90d
```

`--format markdown` prints a ranked table for quick reading; `--output FILE` always saves the full JSON for `youtube-search-report`.

## Subcommands

| subcommand | Use When | Key |
|---|---|---|
| `diag` | Check which routes work right now | – |
| `search` | Discover videos (`--type video`) or channels (`--type channel`) from keywords | – |
| `channel` | Research known channels' uploads with full metrics | – |
| `monitor` | Latest uploads of known channels via RSS (fast, date/views/likes only) | – |
| `comments` | Representative comments for important videos | – |
| `lookup` | Validate URLs / IDs and fetch full metadata | – |
| `trends` | Related queries (top and rising) from Google Trends YouTube Search | – |

## Key Options

- `--period`: `24h`, `7d`, `4w`, `3m`, `1y`, or `2026-08-01..2026-08-31`.
- `--sort` (local ranking after filtering): `quality` (default for relevance), `views`, `velocity` (views/day), `recent`, `engagement` (like rate), `outlier` (views/subscribers), `youtube` (keep YouTube order).
- `--order`: `relevance` / `viewCount` sent to YouTube. `date` is not supported by YouTube web search, so it is sorted locally.
- `--shorts exclude|include|only` (default `exclude`), `--allow-live`.
- `--min-views` (default 1000), `--min-subscribers`, `--max-per-channel` (default 2), `--include` / `--exclude` terms.
- `--max-fetch`: usable candidates per query before filtering; `--enrich-top`: how many pre-ranked candidates get detail (default 30).
- `--deep`: also run yt-dlp per video (spoken language, caption languages, chapters). Slower and the first thing YouTube rate-limits.
- `--cookies cookies.txt`: passed to yt-dlp when YouTube asks to "confirm you're not a bot".

## Output Contract

One JSON object matching `schemas/result.schema.json`:

- `summary`: candidates vs selected, content types, view median/max/total, publish range, top channels.
- `items[]`: selected videos or channels. Besides the base fields: `content_type` (video/short/live/channel), `rank`, `search_rank`, `found_by_queries`, `published_at_precision` (exact/day/approx), `title_localized` (YouTube's auto-translated title when it differs from the original `title`), `duration_seconds`, `tags`, `category`, `has_transcript`, `channel_handle`, and `metrics.{views_per_day, like_rate, comment_rate, views_per_subscriber, age_days}`.
- `comments[]`: include `is_pinned`, `author_is_uploader`, `published_text`.
- `trends[]`: include `seed_query`; rising scores are growth %, not 0-100.
- `queries_tried[]`, `excluded_summary[]`, `limitations[]`, `next_human_actions[]`.

## Facts to Keep in Mind (verified 2026-09)

- **YouTube auto-translates titles** in search results to the UI language (e.g. English videos appear with Japanese titles under `hl=ja`). The web backend restores the original title from `player`; with `--language ja/zh/ko`, videos whose original title has no matching script are excluded as `title_script_mismatch`. With `--no-enrich`, titles may still be translations.
- Web search results vary between runs and are not a stable sample. Use several `--query` values for coverage and keep the JSON for reproducibility.
- The web search upload-date filter only has hour/today/week/month/year. Other periods are filtered locally, so more pages are read.
- yt-dlp watch-page requests get a "Sign in to confirm you're not a bot" block after a few dozen calls. The script stops calling after the first block, reports `rate_limited`, and keeps the InnerTube results. InnerTube `search`/`next`/`player` kept working during that block.
- YouTube RSS sometimes returns 404/500 for existing channels; use `channel` instead.
- Comments are sampled evidence. Trends values are relative, not search volume.

## Report Handoff

After execution, pass the JSON to `youtube-search-report`. Do not mix interpretation or recommendations into the JSON. If insight is needed, generate a fact report first, then call `youtube-search-insight`.
