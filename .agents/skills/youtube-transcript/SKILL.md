---
name: youtube-transcript
description: Fetch or generate transcripts for selected YouTube videos. Use when a user provides a YouTube URL or video ID and asks for a transcript, captions, subtitles, timestamps, quotes, or a summary grounded in the transcript. In broader YouTube research, use this only for important videos after search results have been narrowed. Prefer existing captions first and use OpenAI speech-to-text only when explicitly needed and OPENAI_API_KEY is available.
---

# YouTube Transcript

## Overview

Use the bundled Python CLI to retrieve YouTube transcript data in a repeatable way. Prefer existing caption tracks because they are timestamped and avoid paid ASR calls; use ASR only when captions are missing or the user explicitly asks to create a transcript from audio.

In YouTube research workflows, this is a supporting skill. First use `youtube-search` to identify important videos, then use `youtube-transcript` only for the small set where spoken content, timestamped quotes, or transcript-grounded summary is necessary.

## Quick Start

```bash
python3 {skill_dir}/scripts/youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --langs "ja,en.*,en" --format markdown
```

Write files instead of printing:

```bash
python3 {skill_dir}/scripts/youtube_transcript.py VIDEO_ID --langs "ja,en.*,en" --format json --output-dir transcripts
```

Allow ASR fallback when no caption track is usable:

```bash
python3 {skill_dir}/scripts/youtube_transcript.py VIDEO_ID --langs "ja,en.*,en" --asr fallback --format markdown
```

Replace `{skill_dir}` with the directory containing this `SKILL.md`.

## Workflow

1. Extract or normalize the YouTube video ID from the user input.
2. Run the CLI with the user's preferred languages. For Japanese research, default to `ja,en.*,en` unless the user asked for another language.
3. Inspect the `source` field:
   - `manual_caption` is usually the highest-confidence source.
   - `automatic_caption` is useful but may contain recognition errors.
   - `asr` was generated from downloaded audio and may lack timestamps.
4. If no transcript is found, report the specific failure reason from the CLI output instead of guessing.
5. When summarizing or quoting, keep the transcript output available and cite timestamped lines when present.
6. When feeding a transcript back into a YouTube fact report, include transcript source, language, fetched time, and any limitations.

## Research Rules

- Do not fetch transcripts for every result in a broad search. Narrow to important videos first.
- Do not use ASR just to make a search result richer. Use it only when the user needs content-level evidence and accepts the extra cost.
- Keep transcript-grounded claims separate from title, description, metrics, and comment facts.
- If timestamps are unavailable, say so before quoting or summarizing.

## ASR Rules

- Use `--asr fallback` only when caption retrieval fails or when the user asks to create a transcript.
- Require `OPENAI_API_KEY` for ASR. Never store the key in the repo or transcript files.
- Default ASR model is `gpt-4o-transcribe`, which returns plain text. Use caption tracks when timestamps matter.
- Audio uploads are limited; the script tries to compress with `ffmpeg` if available and otherwise fails with a clear message.

## Outputs

The CLI can emit:

- `markdown`: metadata plus timestamped transcript lines when available.
- `json`: metadata, source details, raw text, and parsed segments.
- `text`: transcript text only.

## References

- Read `references/youtube-transcript-notes.md` when debugging API limitations, package choices, or failure modes.
