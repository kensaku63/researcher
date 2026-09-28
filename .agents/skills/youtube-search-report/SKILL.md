---
name: youtube-search-report
description: Convert normalized YouTube research JSON into a Markdown fact report. Use after youtube-search returns structured results. Keep facts, limitations, failed searches, and human next actions separate from interpretation.
---

# YouTube Search Report

## Purpose

Create a Markdown fact report from `youtube-search` JSON. This report must contain verifiable facts only. Do not include hypotheses, interpretations, marketing recommendations, title ideas, thumbnail ideas, or next strategy suggestions beyond required human actions.

## Required Inputs

- Normalized JSON from `youtube-search`.
- Research plan or user request, if available.

## Required Sections

Use `templates/fact-report.md` as the canonical structure:

- Research conditions
- Search log
- Selected videos or channels
- Comment and reaction facts
- Trends facts
- Transcript facts, if `youtube-transcript` was used
- Information not obtained
- Failed searches
- Next required human actions

## Rules

- Preserve source URLs, channel names, publication dates, metrics, selection reasons, and fetched time.
- State missing metrics as missing; do not infer them.
- Treat comments as sampled public comments, not statistically representative sentiment.
- Treat Trends scores as relative scores, not search volume.
- Keep transcript source and language visible when transcript facts are included.
- If a fact cannot be tied to an item, query log, comment, trend entry, or transcript source, do not include it.

## Output

Write a concise Markdown report. If the JSON has no items, still produce a failure-oriented fact report with attempted queries, limitations, and next human actions.