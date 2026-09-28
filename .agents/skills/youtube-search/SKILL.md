---
name: youtube-search
description: Execute YouTube research through YouTube Data API, RSS, oEmbed, and Google Trends. Use after a focused plan exists. Returns normalized JSON with selected videos, channels, comments, trends, query logs, quota estimate, limitations, and next human actions.
---

# YouTube Search

## Overview

Use `scripts/search.py` to collect public YouTube facts and normalize them into a small JSON shape. Do not return raw API responses to the user. Always preserve search conditions, endpoint usage, quota estimates, selection reasons, limitations, and failed searches.

## Required Secret Names

- `YOUTUBE_API_KEY`: required for YouTube Data API routes (`search`, `channel`, API-enriched `lookup`, `comments`).

Secrets must be injected as environment variables. Never write secret values to files, logs, reports, or debug output.

## Quick Commands

Diagnose environment:

```bash
python3 {skill_dir}/scripts/search.py diag --format json
```

Keyword search:

```bash
python3 {skill_dir}/scripts/search.py search --purpose trend_discovery --language ja --region JP --query "生成AI 勉強法" --period 30d --order date --limit 15 --format json
```

Known channel research:

```bash
python3 {skill_dir}/scripts/search.py channel --purpose competitor_research --language ja --region JP --channels "@example" --period 90d --limit 15 --format json
```

Important-video comments:

```bash
python3 {skill_dir}/scripts/search.py comments --purpose content_planning --language ja --region JP --video-ids VIDEO_ID --comment-order relevance --limit 20 --format json
```

Trends helper:

```bash
python3 {skill_dir}/scripts/search.py trends --purpose trend_discovery --language ja --region JP --query "生成AI 勉強法" --period 90d --format json
```

Replace `{skill_dir}` with the directory containing this `SKILL.md`.

## Subcommands

| subcommand | Use When | API key |
|---|---|---|
| `diag` | Check API key presence, API reachability, and RSS reachability | Optional |
| `lookup` | Validate a URL / video ID / channel handle and fetch metadata | Optional, enriched with key |
| `search` | Discover videos from keywords | Required |
| `channel` | Research known channels via uploads playlist | Required |
| `monitor` | Fetch recent uploads from known channel RSS | Optional |
| `comments` | Fetch representative comments for important videos | Required |
| `trends` | Get related queries and seasonality from Google Trends YouTube Search | Optional |

## Selection Rules

- `search.list` is expensive. Use it for discovery only and keep calls limited.
- Fetch more candidates than the final limit, then filter and explain why items were selected.
- Enrich video IDs with `videos.list` before ranking.
- Enrich channel IDs with `channels.list` unless `--no-channel-enrich` is explicitly used.
- Respect `--max-per-channel` for broad searches; do not apply it to direct channel research.
- If `order` is `relevance`, calculate a local quality score. If `order` is `date` or `viewCount`, respect the API order and only filter obvious noise.
- Comments are sampled evidence, not exhaustive audience research.
- Trends values are relative 0-100 scores, not absolute search volume.

## Output Contract

The script returns one JSON object matching `schemas/result.schema.json`. Important fields:

- `items[]`: selected videos or channels with metrics and `why_selected`.
- `comments[]`: representative comments, when requested.
- `trends[]`: related queries and scores, when requested.
- `queries_tried[]`: endpoint, query, quota cost, before/after counts.
- `excluded_summary[]`: filtering reasons and counts.
- `limitations[]`: missing API key, quota, unavailable metrics, region restrictions, disabled comments, or other constraints.
- `next_human_actions[]`: what a human must do before deeper research can continue.

## Report Handoff

After execution, pass the JSON to `youtube-search-report`. Do not mix interpretation or recommendations into the JSON. If insight is needed, generate a fact report first, then call `youtube-search-insight`.