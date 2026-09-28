---
name: youtube-search-plan
description: Plan one focused YouTube research turn from a natural-language request. Use before YouTube search execution when the user asks for market research, competitor research, trend discovery, influencer discovery, content planning, or social marketing research on YouTube.
---

# YouTube Search Plan

## Purpose

Turn a broad user request into one executable YouTube research strategy. Keep each turn focused enough to finish in about 10 minutes and to produce a fact report with clear search logs.

## Research Purposes

Classify the request into exactly one primary purpose:

- `market_research`: understand representative videos, supply volume, and audience reactions for a theme.
- `competitor_research`: inspect specified channels, brands, or competitors.
- `trend_discovery`: find recent themes, rising videos, and new related terms.
- `influencer_discovery`: identify channels that fit a topic or brand.
- `content_planning`: collect factual patterns in titles, descriptions, formats, comments, and transcripts.
- `social_marketing_research`: broad YouTube research that does not fit the above cleanly.

If the request contains multiple purposes, choose the most important one for this turn and list the others as deferred.

## Required Inputs

Before executing `youtube-search`, make sure the plan has:

- purpose
- language, such as `ja` or `en`
- region, such as `JP` or `US`
- period, such as `7d`, `30d`, `90d`, or an explicit date range
- main query or target channel / URL / handle
- quota posture: conservative, standard, or expanded

Ask the user only when a missing input changes the search materially. Use safe defaults for minor gaps:

- language: `ja`
- region: `JP`
- period: `30d` for trend discovery, `90d` for market research, `365d` for competitor or influencer discovery
- quota posture: conservative

## Planning Rules

- One turn, one search strategy.
- Do not expand from YouTube to other media unless the user asks.
- Prefer YouTube Data API for repeatable public facts.
- Prefer `channel` or `monitor` when the user gives known channels, because they avoid expensive `search.list` calls.
- Use `comments` only for important videos, not every result.
- Use `trends` for related terms and seasonality, not for video metrics.
- Use `youtube-transcript` only after narrowing to important videos.

## Output Format

```markdown
## YouTube Research Plan

- Purpose:
- Language:
- Region:
- Period:
- Target:
- Primary subcommand: (`search` / `channel` / `lookup` / `monitor` / `comments` / `trends`)
- Query / channels:
- Include terms:
- Exclude terms:
- Limit:
- Quota posture:
- Comments:
- Transcript:

## Why This Strategy

-

## Deferred

-
```