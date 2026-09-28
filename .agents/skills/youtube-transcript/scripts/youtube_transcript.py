#!/usr/bin/env python3
"""Fetch YouTube captions or generate a transcript with OpenAI ASR."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


FORMAT_PRIORITY = ["vtt", "srt", "ttml", "srv3", "srv2", "srv1", "json3", "json"]
MAX_ASR_BYTES = 25 * 1024 * 1024
SAFE_ASR_BYTES = 24 * 1024 * 1024


class TranscriptError(RuntimeError):
    pass


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def load_yt_dlp():
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:
        raise TranscriptError(
            "yt-dlp is not installed. Install it or declare it in environment.yaml packages.pip."
        ) from exc
    return YoutubeDL


def normalize_video_input(value: str) -> tuple[str, str]:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.netloc:
        host = parsed.netloc.lower()
        if host.endswith("youtu.be"):
            video_id = parsed.path.strip("/").split("/")[0]
        elif "youtube.com" in host:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
                video_id = parsed.path.strip("/").split("/")[1]
            else:
                video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            video_id = ""
    else:
        video_id = value

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise TranscriptError(f"Could not determine a YouTube video ID from: {value}")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def parse_lang_patterns(patterns: str) -> list[str]:
    return [item.strip() for item in patterns.split(",") if item.strip()]


def lang_matches(lang: str, patterns: list[str]) -> bool:
    if lang == "live_chat":
        return False
    if not patterns:
        return True
    for pattern in patterns:
        if pattern == "all":
            return True
        if pattern.startswith("-"):
            continue
        if lang == pattern:
            return True
        try:
            if re.fullmatch(pattern, lang):
                return True
        except re.error:
            continue
    return False


def excluded_lang(lang: str, patterns: list[str]) -> bool:
    if lang == "live_chat":
        return True
    for pattern in patterns:
        if not pattern.startswith("-"):
            continue
        raw = pattern[1:]
        if lang == raw:
            return True
        try:
            if re.fullmatch(raw, lang):
                return True
        except re.error:
            continue
    return False


def pattern_matches_lang(lang: str, pattern: str) -> bool:
    if pattern == "all":
        return True
    if lang == pattern:
        return True
    try:
        return re.fullmatch(pattern, lang) is not None
    except re.error:
        return False


def choose_caption(info: dict[str, Any], lang_patterns: list[str]) -> tuple[str, str, dict[str, Any]] | None:
    sources = [
        ("manual_caption", info.get("subtitles") or {}),
        ("automatic_caption", info.get("automatic_captions") or {}),
    ]
    positive_patterns = [pattern for pattern in lang_patterns if not pattern.startswith("-")] or ["all"]
    for source_name, tracks_by_lang in sources:
        for pattern in positive_patterns:
            for lang in sorted(tracks_by_lang):
                if excluded_lang(lang, lang_patterns) or not pattern_matches_lang(lang, pattern):
                    continue
                tracks = tracks_by_lang.get(lang) or []
                sorted_tracks = sorted(
                    tracks,
                    key=lambda track: FORMAT_PRIORITY.index(track.get("ext", ""))
                    if track.get("ext", "") in FORMAT_PRIORITY
                    else len(FORMAT_PRIORITY),
                )
                for track in sorted_tracks:
                    if track.get("url"):
                        return source_name, lang, track
    return None


def fetch_caption_bytes(ydl: Any, url: str) -> bytes:
    try:
        response = ydl.urlopen(url)
        return response.read()
    except Exception as exc:  # noqa: BLE001
        raise TranscriptError(f"Could not download caption track: {exc}") from exc


def parse_timestamp(value: str) -> float | None:
    value = value.strip().replace(",", ".")
    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def clean_caption_text(text: str) -> str:
    text = re.sub(r"<\d{1,2}:\d{2}:\d{2}\.\d{3}>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    last_text = ""
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text or text == last_text:
            continue
        item = dict(segment)
        item["text"] = text
        deduped.append(item)
        last_text = text
    return deduped


def parse_vtt_or_srt(raw: str) -> list[dict[str, Any]]:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION", "Kind:", "Language:")):
            i += 1
            continue
        if "-->" not in line and i + 1 < len(lines) and "-->" in lines[i + 1]:
            i += 1
            line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in line.split("-->", 1)]
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = clean_caption_text(" ".join(text_lines))
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return dedupe_segments(segments)


def parse_json3(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    segments: list[dict[str, Any]] = []
    for event in data.get("events", []):
        segs = event.get("segs") or []
        text = clean_caption_text("".join(seg.get("utf8", "") for seg in segs))
        if not text:
            continue
        start_ms = event.get("tStartMs")
        duration_ms = event.get("dDurationMs")
        start = float(start_ms) / 1000 if isinstance(start_ms, int) else None
        end = start + (float(duration_ms) / 1000) if start is not None and isinstance(duration_ms, int) else None
        segments.append({"start": start, "end": end, "text": text})
    return dedupe_segments(segments)


def parse_caption(raw_bytes: bytes, ext: str) -> list[dict[str, Any]]:
    raw = raw_bytes.decode("utf-8", errors="replace")
    if ext in {"json3", "json"} or raw.lstrip().startswith("{"):
        return parse_json3(raw)
    return parse_vtt_or_srt(raw)


def transcript_text(segments: list[dict[str, Any]], include_timestamps: bool) -> str:
    lines: list[str] = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue
        if include_timestamps and segment.get("start") is not None:
            lines.append(f"[{format_timestamp(segment.get('start'))}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def safe_filename(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.ASCII).strip("._")
    return value[:80] or "youtube-transcript"


def extract_info(url: str) -> tuple[Any, dict[str, Any]]:
    YoutubeDL = load_yt_dlp()
    ydl = YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
    )
    try:
        info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise TranscriptError(f"Could not read YouTube metadata: {exc}") from exc
    return ydl, info


def build_metadata(info: dict[str, Any], video_id: str, url: str) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def download_audio(url: str, temp_dir: Path) -> Path:
    YoutubeDL = load_yt_dlp()
    outtmpl = str(temp_dir / "%(id)s.%(ext)s")
    ydl = YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": outtmpl,
        }
    )
    try:
        info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise TranscriptError(f"Could not download audio for ASR: {exc}") from exc
    expected = Path(ydl.prepare_filename(info))
    if expected.exists():
        return expected
    files = sorted(temp_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if files:
        return files[0]
    raise TranscriptError("Audio download finished but no audio file was found.")


def compress_audio_if_needed(path: Path, temp_dir: Path) -> Path:
    if path.stat().st_size <= SAFE_ASR_BYTES:
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TranscriptError(
            "Downloaded audio is over 25 MB and ffmpeg is unavailable for compression."
        )
    output = temp_dir / f"{path.stem}.asr.mp3"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        str(output),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if output.stat().st_size > MAX_ASR_BYTES:
        raise TranscriptError("Audio remains over 25 MB after compression; split it manually.")
    return output


def generate_asr(url: str, model: str, language: str | None) -> list[dict[str, Any]]:
    if not os.environ.get("OPENAI_API_KEY"):
        raise TranscriptError("OPENAI_API_KEY is required for ASR fallback.")
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise TranscriptError("The OpenAI Python SDK is required for ASR fallback.") from exc

    with tempfile.TemporaryDirectory(prefix="youtube-asr-") as tmp:
        temp_dir = Path(tmp)
        audio_path = compress_audio_if_needed(download_audio(url, temp_dir), temp_dir)
        client = OpenAI()
        with audio_path.open("rb") as audio_file:
            kwargs: dict[str, Any] = {
                "model": model,
                "file": audio_file,
                "response_format": "text",
            }
            if language:
                kwargs["language"] = language
            response = client.audio.transcriptions.create(**kwargs)
    text = response if isinstance(response, str) else getattr(response, "text", str(response))
    text = clean_caption_text(text)
    return [{"start": None, "end": None, "text": text}] if text else []


def build_transcript(args: argparse.Namespace) -> dict[str, Any]:
    video_id, url = normalize_video_input(args.video)
    ydl, info = extract_info(url)
    metadata = build_metadata(info, video_id, url)
    patterns = parse_lang_patterns(args.langs)
    selected = None if args.asr == "always" else choose_caption(info, patterns)
    source = "none"
    language = None
    track_ext = None
    segments: list[dict[str, Any]] = []

    if selected:
        source, language, track = selected
        track_ext = track.get("ext")
        raw = fetch_caption_bytes(ydl, track["url"])
        segments = parse_caption(raw, track_ext or "")

    if args.asr == "always" or (not segments and args.asr == "fallback"):
        source = "asr"
        language = args.asr_language
        track_ext = "text"
        segments = generate_asr(url, args.asr_model, args.asr_language)

    if not segments:
        raise TranscriptError(
            "No usable transcript was found. Try another language pattern or run with --asr fallback."
        )

    text = transcript_text(segments, include_timestamps=args.timestamps and source != "asr")
    return {
        "metadata": metadata,
        "source": source,
        "language": language,
        "track_ext": track_ext,
        "segments": segments,
        "text": text,
    }


def to_markdown(result: dict[str, Any]) -> str:
    meta = result["metadata"]
    lines = [
        f"# {meta.get('title') or meta.get('video_id')}",
        "",
        f"- Video ID: {meta.get('video_id')}",
        f"- URL: {meta.get('url')}",
        f"- Channel: {meta.get('channel') or ''}",
        f"- Source: {result.get('source')}",
        f"- Language: {result.get('language') or ''}",
        f"- Fetched at: {meta.get('fetched_at')}",
        "",
        "## Transcript",
        "",
        result.get("text") or "",
        "",
    ]
    return "\n".join(lines)


def write_or_print(result: dict[str, Any], args: argparse.Namespace) -> None:
    if args.format == "json":
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        suffix = "json"
    elif args.format == "text":
        rendered = result.get("text") or ""
        suffix = "txt"
    else:
        rendered = to_markdown(result)
        suffix = "md"

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        title = result["metadata"].get("title") or result["metadata"].get("video_id")
        filename = f"{safe_filename(str(title))}.{suffix}"
        output_path = out_dir / filename
        output_path.write_text(rendered, encoding="utf-8")
        print(str(output_path))
    else:
        print(rendered)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="YouTube URL or 11-character video ID")
    parser.add_argument("--langs", default="ja,en.*,en", help="Comma-separated language patterns")
    parser.add_argument("--format", choices=["markdown", "json", "text"], default="markdown")
    parser.add_argument("--output-dir", help="Directory for the rendered transcript")
    parser.add_argument("--asr", choices=["never", "fallback", "always"], default="never")
    parser.add_argument("--asr-model", default="gpt-4o-transcribe")
    parser.add_argument("--asr-language", help="Optional ISO language hint for ASR")
    parser.add_argument("--no-timestamps", action="store_false", dest="timestamps")
    parser.set_defaults(timestamps=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        result = build_transcript(args)
        write_or_print(result, args)
    except TranscriptError as exc:
        eprint(f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
