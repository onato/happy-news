#!/usr/bin/env python3
"""Turn the newest batch of stories in data/news.json into a spoken digest.

Stdlib only, by design: happy-news is a zero-dependency static site and CI has no
Python packaging step. The only external tool is ffmpeg (installed by the
workflow — it is not on the runner image).

Two constraints shape this script:

1. **The whole digest is ONE API request.** The Gemini free tier allows about 3
   TTS requests a minute and only ~10 a day, and every attempt counts — including
   failed ones. Narrating story-by-story would spend most of a day's budget on a
   single digest and leave nothing for a retry.

2. **Chapter offsets come from detecting the pauses in the returned audio.** The
   model is asked to leave a beat between stories; we find those silences in the
   raw PCM and use them as chapter boundaries. Working on the pre-encode stream
   keeps offsets exact — mp3 encoder padding would otherwise drift them ~30 ms
   per story, compounding, and progressively desync the player's seek buttons.

Usage:
    python3 scripts/digest.py --dry-run          # no API calls, prints the script
    python3 scripts/digest.py --out build/audio  # needs GEMINI_API_KEY
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Gemini TTS returns headerless PCM in this format. ffmpeg has to be told.
SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE

MP3_BITRATE = "64k"  # transparent for 24 kHz speech; ~0.5 MB per minute

MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")
API = ("https://generativelanguage.googleapis.com/v1beta/models/"
       "{model}:generateContent")

# generateContent is a chat endpoint, so without this it sometimes prefixes the
# audio with "Sure, here's that read aloud:".
#
# The pause instruction is load-bearing, not cosmetic: the chapter offsets that
# drive per-story seeking are recovered by finding those silences in the audio
# (see find_pauses).
SYSTEM_INSTRUCTION = (
    "You are a news narrator. Read the supplied text aloud verbatim in a warm, "
    "clear, unhurried broadcast voice. Add no commentary, greeting, or "
    "acknowledgement of these instructions. "
    "Leave a clear one-second silent pause between paragraphs."
)

# The free tier allows only ~10 TTS requests per DAY, and every attempt counts
# against it — including failed ones. So retries are deliberately few: burning
# five attempts on one bad segment would eat half a day's budget and leave
# nothing for the remaining stories.
MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0  # 2, 4s


# Measured narration rate (chars per minute) — used only for --dry-run estimates.
CHARS_PER_MINUTE = 950

# Exit code meaning "temporary failure" — the workflow treats it as "skip audio
# today" rather than a hard error, so a quota blip never fails the run.
EX_TEMPFAIL = 75

CATEGORY_ORDER = ["Science", "Health", "Environment", "Community", "Culture", "Animals"]

NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}

ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh",
    12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
    16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth",
    20: "twentieth", 21: "twenty-first", 22: "twenty-second",
    23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
    26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth",
    29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first",
}


class TTSError(RuntimeError):
    """A hard failure — bad request, bad key, malformed response."""


class QuotaExceeded(TTSError):
    """Rate limited or out of quota. Retryable in principle, skippable today."""


# ---------------------------------------------------------------- text

# Read aloud, these are silent or wrong far more often than they're right.
# Keep this table short — over-normalising mangles meaning, and the voice
# handles ordinary prose well on its own.
SPOKEN = [
    (re.compile(r"&amp;"), " and "),
    (re.compile(r"&#39;|&apos;"), "'"),
    (re.compile(r"&quot;"), '"'),
    (re.compile(r"&nbsp;"), " "),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\bU\.S\.\b"), "US"),
    (re.compile(r"\bU\.K\.\b"), "UK"),
    # Relay distances: "4x100m" reads as letters otherwise.
    (re.compile(r"\b(\d+)\s*[xX×]\s*(\d+)\s*m\b"), r"\1 by \2 metres"),
    # Units attached to a number. Spelled out, these are read correctly.
    (re.compile(r"(\d)\s*GW\b"), r"\1 gigawatts"),
    (re.compile(r"(\d)\s*MW\b"), r"\1 megawatts"),
    (re.compile(r"(\d)\s*kg\b"), r"\1 kilograms"),
    (re.compile(r"(\d)\s*km\b"), r"\1 kilometres"),
    (re.compile(r"(\d)\s*cm\b"), r"\1 centimetres"),
    (re.compile(r"(\d)\s*ha\b"), r"\1 hectares"),
]


def speakable(text: str) -> str:
    """Normalise story text into something a TTS voice reads cleanly."""
    out = text.strip()
    for pattern, replacement in SPOKEN:
        out = pattern.sub(replacement, out)
    # Typographic punctuation confuses some voices and destabilises cache keys.
    out = (out.replace("’", "'").replace("‘", "'")
              .replace("“", '"').replace("”", '"')
              .replace("—", ", ").replace("–", "-")
              .replace("…", "..."))
    out = re.sub(r"\s+", " ", out).strip()
    # A headline without terminal punctuation runs straight into the summary.
    if out and out[-1] not in ".!?":
        out += "."
    return out


def spoken_date(when: datetime) -> str:
    """'Friday, the fifteenth of August' — '15th' is often read as 'one five th'."""
    return f"{when.strftime('%A')}, the {ORDINALS.get(when.day, str(when.day))} of {when.strftime('%B')}"


def spoken_count(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


# ---------------------------------------------------------------- selection


def todays_stories(feed: dict) -> list[dict]:
    """The stories written by the most recent collector run.

    Every story from one run shares an identical `added` timestamp, which makes
    the newest `added` value an exact run key. This beats a 24h window on
    `published` (that's the article's date, not ours, and the collector's window
    is 30h) and beats diffing git (the audio job runs on a fresh checkout).
    """
    stories = feed.get("stories") or []
    if not stories:
        return []
    latest = max(s.get("added", "") for s in stories)
    if not latest:
        return []
    batch = [s for s in stories if s.get("added") == latest]

    def sort_key(story: dict) -> tuple[int, str]:
        category = story.get("category", "")
        rank = CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER)
        # Newest first within a category.
        return (rank, _invert(story.get("published", "")))

    batch.sort(key=sort_key)
    return batch


def _invert(text: str) -> str:
    """Sort key that reverses a string's natural order (for descending dates)."""
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in text)


def run_key(batch: list[dict]) -> str:
    return batch[0].get("added", "") if batch else ""


# ---------------------------------------------------------------- script


def build_segments(batch: list[dict], when: datetime) -> list[dict]:
    """The narration, as a list of {text, story} segments.

    One segment per TTS request, which is also one chapter. Keeping requests
    short sidesteps the documented quality drift on long generations and gives
    per-story retry and per-story seek offsets.
    """
    count = len(batch)
    intro = (
        f"Happy News for {spoken_date(when)}. "
        f"Here {'is' if count == 1 else 'are'} today's {spoken_count(count)} "
        f"good thing{'' if count == 1 else 's'}."
    )
    segments = [{"text": intro, "story": None}]

    for story in batch:
        source = speakable(story.get("source", "")).rstrip(".")
        headline = speakable(story.get("headline", ""))
        summary = speakable(story.get("summary", ""))
        # Provenance before claim, the way radio news does it.
        text = f"From {source}. {headline} {summary}".strip()
        segments.append({"text": text, "story": story})

    segments.append({
        "text": "That's the good news for today. See you tomorrow.",
        "story": None,
    })
    return segments


# ---------------------------------------------------------------- synthesis


def _retry_delay(detail: str) -> float | None:
    """Seconds the API asked us to wait, from its retryDelay or message text."""
    match = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', detail)
    if not match:
        match = re.search(r"retry in ([\d.]+)\s*(ms|s)", detail)
        if match:
            value = float(match.group(1))
            return value / 1000 if match.group(2) == "ms" else value
        return None
    return float(match.group(1))


def cache_path(cache_dir: Path, text: str) -> Path:
    key = hashlib.sha256(f"gemini|{MODEL}|{VOICE}|pcm|{text}".encode()).hexdigest()[:20]
    return cache_dir / f"{key}.pcm"


def synthesize(text: str, api_key: str) -> bytes:
    """One segment of narration -> raw PCM (s16le, 24 kHz, mono)."""
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}
            },
        },
    }).encode()

    request = urllib.request.Request(
        API.format(model=MODEL),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )

    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:800].decode("utf-8", "replace")
            retryable = exc.code == 429 or exc.code >= 500
            if retryable and attempt < MAX_ATTEMPTS - 1:
                # The API tells us how long to wait; trust it over blind backoff
                # and add a margin, since it reports the minimum.
                wait = BACKOFF_BASE ** (attempt + 1)
                hinted = _retry_delay(detail)
                if hinted is not None:
                    wait = max(wait, hinted + 2.0)
                # Print the body: on the free tier every attempt costs daily
                # quota, so a silent retry loop is expensive guesswork.
                print(f"    HTTP {exc.code}; retrying in {wait:.0f}s — {detail[:300]}",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if exc.code == 429:
                raise QuotaExceeded(f"HTTP 429 after {attempt + 1} tries: {detail}") from exc
            raise TTSError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise TTSError(f"network error: {exc}") from exc
            time.sleep(BACKOFF_BASE ** (attempt + 1))
    else:  # pragma: no cover - loop always breaks or raises
        raise TTSError("exhausted attempts")

    try:
        part = payload["candidates"][0]["content"]["parts"][0]
        audio = base64.b64decode(part["inlineData"]["data"])
    except (KeyError, IndexError, TypeError) as exc:
        raise TTSError(f"unexpected response shape: {json.dumps(payload)[:400]}") from exc

    if not audio:
        raise TTSError("empty audio returned")
    return audio


# ---------------------------------------------------------------- assembly


def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise TTSError(f"{tool} is required but not available") from exc


def duration_of(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def encode(pcm_path: Path, mp3_path: Path, title: str, date_iso: str, comment: str) -> None:
    """Single encode of the whole digest, with ID3 tags applied in the same pass.

    ID3v2.3 rather than 2.4 — older podcast clients handle it more reliably.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", str(pcm_path),
         "-c:a", "libmp3lame", "-b:a", MP3_BITRATE,
         "-id3v2_version", "3", "-write_id3v1", "1",
         "-metadata", f"title={title}",
         "-metadata", "artist=Happy News",
         "-metadata", "album=Happy News",
         "-metadata", "album_artist=Happy News",
         "-metadata", "genre=News",
         "-metadata", f"date={date_iso[:10]}",
         "-metadata", f"comment={comment}",
         str(mp3_path)],
        check=True, capture_output=True,
    )


def find_pauses(pcm: bytes, want: int) -> list[int]:
    """Byte offsets of the `want` longest silences — the gaps between stories.

    The narrator is asked to pause between stories, so the longest quiet stretches
    are the story boundaries. Returns offsets at the END of each chosen silence,
    i.e. where the next story's first word begins, sorted in playback order.
    """
    if want <= 0:
        return []

    frame = int(SAMPLE_RATE * 0.02) * BYTES_PER_SAMPLE   # 20 ms
    threshold = 500          # |sample| below this is effectively silence
    min_run = int(BYTES_PER_SECOND * 0.35)               # ignore breath-length gaps

    runs: list[tuple[int, int]] = []                     # (length, end offset)
    run_start = None

    for offset in range(0, len(pcm) - frame, frame):
        chunk = pcm[offset:offset + frame]
        peak = max(abs(int.from_bytes(chunk[i:i + 2], "little", signed=True))
                   for i in range(0, len(chunk), 2))
        if peak < threshold:
            if run_start is None:
                run_start = offset
        else:
            if run_start is not None:
                length = offset - run_start
                if length >= min_run:
                    runs.append((length, offset))
                run_start = None

    # Longest silences are the deliberate story breaks; then back to time order.
    runs.sort(reverse=True)
    return sorted(end for _, end in runs[:want])


def render(segments: list[dict], out_dir: Path, api_key: str,
           title: str, date_iso: str, comment: str) -> tuple[Path, list[dict]]:
    """Narrate the whole digest in one request, then locate the story boundaries.

    One request keeps us inside the free tier's ~10-per-day ceiling with room to
    retry. Offsets are measured on the raw PCM, before encoding, so they stay
    exact.
    """
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    script = "\n\n".join(segment["text"] for segment in segments)

    cached = cache_path(cache_dir, script)
    if cached.exists():
        pcm = cached.read_bytes()
        print("  (using cached audio)", file=sys.stderr)
    else:
        pcm = synthesize(script, api_key)
        cached.write_bytes(pcm)

    total = len(pcm) / BYTES_PER_SECOND
    print(f"  {total:.1f}s of audio", file=sys.stderr)

    stories = [segment["story"] for segment in segments if segment["story"] is not None]

    # Boundaries: one before each story, plus one before the sign-off.
    boundaries = find_pauses(pcm, len(stories) + 1)

    chapters: list[dict] = []
    if len(boundaries) == len(stories) + 1:
        for index, story in enumerate(stories):
            start = boundaries[index]
            end = boundaries[index + 1]
            chapters.append({
                # Keyed by story url — the feed's unique key, already enforced
                # unique by the workflow's "Validate the feed" step. The player's
                # seek correctness depends on that validation.
                "url": story.get("url", ""),
                "headline": story.get("headline", ""),
                "start": round(start / BYTES_PER_SECOND, 3),
                "duration": round((end - start) / BYTES_PER_SECOND, 3),
            })
    else:
        # Couldn't find a clean break per story. Ship the audio anyway — the
        # digest still plays end to end; only the per-story buttons are lost.
        print(f"  WARNING: found {len(boundaries)} pauses for {len(stories)} "
              f"stories — per-story seeking disabled for this episode.",
              file=sys.stderr)

    pcm_path = out_dir / "digest.pcm"
    pcm_path.write_bytes(pcm)

    mp3_path = out_dir / f"happy-news-{date_iso[:10]}.mp3"
    encode(pcm_path, mp3_path, title, date_iso, comment)
    pcm_path.unlink(missing_ok=True)

    return mp3_path, chapters


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--news", type=Path, default=Path("data/news.json"))
    parser.add_argument("--episodes", type=Path, default=Path("data/episodes.json"))
    parser.add_argument("--out", type=Path, default=Path("build/audio"))
    parser.add_argument("--dry-run", action="store_true",
                        help="print the narration script and exit; no API calls")
    args = parser.parse_args()

    feed = json.loads(args.news.read_text())
    batch = todays_stories(feed)
    if not batch:
        print("No stories in the newest batch — nothing to narrate.", file=sys.stderr)
        print(json.dumps({"episode": "none"}))
        return 0

    key = run_key(batch)

    # Idempotence: a re-dispatch must not produce a second episode for one batch.
    if args.episodes.exists():
        existing = json.loads(args.episodes.read_text()).get("episodes") or []
        if any(e.get("runKey") == key for e in existing):
            print(f"Episode for run {key} already exists — nothing to do.", file=sys.stderr)
            print(json.dumps({"episode": "none"}))
            return 0

    when = datetime.now(timezone.utc)
    date_iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_label = when.strftime("%-d %B %Y")
    title = f"Happy News — {date_label}"
    headlines = [s.get("headline", "") for s in batch]
    comment = "; ".join(headlines)[:400]

    segments = build_segments(batch, when)

    if args.dry_run:
        total = 0
        print(f"\n{title}\n{'=' * len(title)}\n")
        for index, segment in enumerate(segments):
            chars = len(segment["text"])
            total += chars
            label = "intro" if index == 0 else ("outro" if segment["story"] is None else f"story {index}")
            print(f"[{label}] ({chars} chars)")
            print(f"  {segment['text']}\n")
        print(f"{len(segments)} segments, {total} chars, "
              f"~{total / CHARS_PER_MINUTE:.1f} min estimated.")
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set.", file=sys.stderr)
        return EX_TEMPFAIL

    check_ffmpeg()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Narrating {len(batch)} stories as {len(segments)} segments…", file=sys.stderr)
    try:
        mp3_path, chapters = render(segments, args.out, api_key, title, date_iso, comment)
    except QuotaExceeded as exc:
        print(f"Gemini quota exhausted: {exc}", file=sys.stderr)
        return EX_TEMPFAIL

    total = duration_of(mp3_path)
    size = mp3_path.stat().st_size

    # The PCM-concat approach should make this near-exact; a large drift means
    # the offsets and the audio have diverged and the seek buttons would be wrong.
    if chapters:
        last = chapters[-1]
        if last["start"] + last["duration"] > total + 1.0:
            print("WARNING: chapters run past the end of the audio — "
                  "seek offsets may be off.", file=sys.stderr)

    episode = {
        "date": date_iso[:10],
        "title": title,
        "runKey": key,
        "published": date_iso,
        "duration": round(total, 2),
        "bytes": size,
        "storyCount": len(batch),
        "guid": f"happy-news-{date_iso[:10]}",
        "summary": f"{spoken_count(len(batch)).capitalize()} good things: "
                   + "; ".join(headlines[:4]),
        "chapters": chapters,
        "file": str(mp3_path),
    }

    print(f"\n{mp3_path}  {total / 60:.1f} min  {size / 1e6:.2f} MB", file=sys.stderr)
    print(json.dumps(episode))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TTSError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
