# YouTube Transcript Notes

## Source Strategy

- YouTube Data API caption download is not a general transcript API for arbitrary public videos. `captions.download` requires authorization from a user who can edit the video.
- Prefer `yt-dlp` metadata and caption URLs for public videos. It supports manual subtitles, automatic captions, language filtering, and subtitle formats.
- Use OpenAI speech-to-text only as a fallback for videos without usable captions or when the user explicitly asks to create a transcript from audio.

## Language Selection

- Default Japanese research preference: `ja,en.*,en`.
- Keep `live_chat` excluded.
- Prefer manual captions over automatic captions, then prefer VTT/SRT-like formats over JSON timed text.

## Known Failure Modes

- Private, members-only, age-restricted, region-locked, removed, live, or login-required videos may fail before transcript extraction.
- Some videos expose captions in the YouTube UI but not through public metadata. Try a more specific language pattern or cookies only if the user has rights and explicitly provides that path.
- ASR fallback needs `OPENAI_API_KEY`; long audio may exceed API upload limits even after compression.