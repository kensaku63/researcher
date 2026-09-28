---
name: youtube-search-insight
description: Create an interpretation and hypothesis report from a YouTube fact report. Use only after youtube-search-report has produced a fact report. Do not fetch new data or invent facts.
---

# YouTube Search Insight

## Purpose

Turn a YouTube fact report into a separate interpretation and hypothesis report. The input is the fact report only. Do not call external APIs, run scripts, search the web, or add new facts.

## Rules

- Every interpretation must cite the fact report item it depends on.
- If the fact report does not contain enough evidence, write the gap under "不足している事実".
- Use confidence levels only as `low`, `medium`, or `high`.
- Hypotheses must be falsifiable.
- Do not output marketing execution plans, campaign priorities, ad settings, title ideas, thumbnails, or creative copy.
- Do not treat sampled comments as representative of all viewers.

## Output Structure

Use `templates/insight-report.md` as the canonical structure:

- 前提
- 解釈
- 仮説
- リスク・懸念
- 次に調べるべき論点
- 不足している事実

## Good Evidence References

Use references such as:

- `代表動画 #1`
- `検索ログ: query="..." endpoint=search.list`
- `コメント・反応の事実: video_id=...`
- `Trends: keyword=...`
- `Transcript: video_id=... source=manual_caption`