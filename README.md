# 🌻 Happy News

A daily feed of genuinely good news, collected automatically by Claude Code and
rendered as a static news feed. Every story links out to its original publisher.
Each day's stories are also narrated into a short audio digest you can play on
the site or subscribe to as a podcast.

## How it works

1. **`.github/workflows/happy-news.yml`** runs daily at 18:00 UTC (~6am NZ).
   Claude Code searches for uplifting stories published in the last 30 hours
   across six categories, deduplicates them against the existing feed, and
   prepends the new ones to `data/news.json`.
2. The run validates the JSON, commits it, and sends a Telegram ping with the
   top headline and a link to the feed.
3. The `audio` job narrates the new stories with Gemini TTS, uploads the mp3 as a
   GitHub Release asset, and regenerates `podcast.xml`.
4. The `deploy` job publishes the site to GitHub Pages.
5. **`.github/workflows/pages.yml`** covers ordinary pushes to `main` (human
   commits); the daily run deploys itself, because commits made with
   `GITHUB_TOKEN` don't trigger other workflows.

The feed is capped at the 200 most recent stories.

## Audio

Each run turns the day's stories into a roughly 8–15 minute digest: a short
intro, then each story read as "From *source*. *Headline*." followed by its
`narrative` — the collector's own 4–6 paragraph retelling, written to be
listened to rather than skimmed.

The site and the audio deliberately carry different text. Story cards show the
short `summary` so the feed stays scannable; the narrator reads the longer
`narrative`. Stories written before this split, or any the collector judged too
thin to expand, have no `narrative` and fall back to being read from `summary`.

`narrative` is always the collector's **own prose**, not the publisher's. The
feed's premise is that it sends people to the original article, so the digest
retells each story rather than reproducing the copy that outlets host and
monetise themselves.

- **Narration** uses the [Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation)
  free tier — the same speech technology behind NotebookLM's Audio Overviews.
  It returns audio in seconds and costs no CI compute.
- **Storage** is GitHub Releases, one per day, tagged `audio-YYYY-MM-DD`.
  Releases have no size or bandwidth limit, and deleting an asset actually
  reclaims the space — a committed mp3 would live in every clone forever.
- **Retention** is one month. `scripts/prune_episodes.sh` deletes older releases
  and `podcast.xml` is regenerated from the surviving episodes, so the feed can
  never link to a deleted file.
- **Playback**: a sticky bar plays the whole digest, and each story card gets a
  play button that seeks to that story's offset in the same mp3.
- **Subscribe** in any podcast app via `podcast.xml` at the site root.

`data/episodes.json` is the manifest the player and the RSS feed both read. It
records each episode's duration, byte size, and per-story chapter offsets.

Audio is deliberately **best-effort**: every step in the `audio` job continues on
error, and `deploy` runs regardless, so a TTS outage costs the audio only — the
news feed still publishes.

### Two implementation details worth knowing

`scripts/digest.py` synthesizes each story separately but concatenates the **raw
PCM** and encodes to mp3 **once**. Encoding per story and joining the mp3s
accumulates encoder padding (~30 ms each, compounding), which would progressively
desync the per-story seek offsets. Joining PCM also makes those offsets exact
arithmetic on byte counts rather than a sum of rounded durations.

**Two pause lengths, and the difference is load-bearing.** Chapter offsets are
recovered by finding the silences in the finished audio — but now that a story
runs to several paragraphs, there are pauses *inside* a story too. So the digest
uses two: `PARAGRAPH_PAUSE` (0.7s) between paragraphs of one story, and
`STORY_PAUSE` (2.2s) between stories, with `find_pauses` ignoring anything
shorter than `PAUSE_FLOOR` (1.4s). Move those three values closer together and
the per-story seek buttons start landing mid-story. Every engine implements the
same contract: paragraphs within a story are joined by `PARAGRAPH_BREAK`, stories
by a blank line.

## Setup

### Secrets

Set these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | What it is |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | Generate with `claude setup-token` |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/botfather) |
| `GEMINI_API_KEY` | From [Google AI Studio](https://aistudio.google.com/apikey) — free tier is ample for one digest a day |

```sh
printf '%s' '<token>' | gh secret set CLAUDE_CODE_OAUTH_TOKEN
printf '%s' '<token>' | gh secret set TELEGRAM_BOT_TOKEN
printf '%s' '<key>'   | gh secret set GEMINI_API_KEY
```

Without `GEMINI_API_KEY` the audio job logs a warning and skips; everything else
runs as normal.

### Variables

Under the **Variables** tab:

| Variable | What it is |
| --- | --- |
| `TELEGRAM_CHAT_ID` | Your chat ID with the bot |
| `FEED_URL` | Where the Telegram ping links, and the base URL for `podcast.xml` |

```sh
gh variable set TELEGRAM_CHAT_ID --body '<id>'
gh variable set FEED_URL --body 'https://www.onato.com/happy-news/'
```

`FEED_URL` must be an absolute **`https://`** URL with a trailing slash. Podcast
apps require HTTPS, and Pages here does not enforce it — plain HTTP will not
redirect, so every URL in the feed is written out in full.

The chat ID is a variable rather than a secret: it's an address, not a
credential — it does nothing without the bot token. Note that Actions masks
secrets in logs but does **not** mask variables, so avoid adding verbose curl
output to the Telegram steps.

### Pages

**Settings → Pages → Source: GitHub Actions.** The first deploy runs on the next
push to `main`.

## Testing

Check the Telegram wiring without running a collection:

```sh
gh workflow run happy-news.yml -f test_telegram=true
```

Run a real collection on demand:

```sh
gh workflow run happy-news.yml
```

Rebuild only the audio, against the stories already in the feed — no Claude spend:

```sh
gh workflow run happy-news.yml -f audio_only=true
```

Preview the narration script without calling the API or spending anything:

```sh
python3 scripts/digest.py --dry-run
```

Render a real episode locally (needs `GEMINI_API_KEY` and `ffmpeg`):

```sh
GEMINI_API_KEY=… python3 scripts/digest.py --out build/audio
```

### Voices

The Gemini free tier allows only about **3 TTS requests a minute and 10 a day**,
and failed attempts count too. Three other engines render the identical pipeline
for free:

```sh
python3 scripts/digest.py --engine piper  --out build/audio   # also runs on CI
python3 scripts/digest.py --engine kokoro --out build/audio   # ~25s, best voice
python3 scripts/digest.py --engine say    --out build/audio   # instant, robotic
```

- **`piper`** is the fallback the workflow uses automatically when the Gemini
  quota is gone — neural but ONNX-based, so it installs in about a minute with no
  PyTorch and needs no API key. `pip install piper-tts && python3 -m
  piper.download_voices en_GB-alba-medium`. Voice via `PIPER_VOICE`.
  Note the macOS wheel has a hardcoded espeak-ng data path from its build machine
  and cannot synthesize locally; it works on Linux, so use kokoro or say on a Mac.
- **`kokoro`** reuses the venv from the sibling [earful](../earful) project
  (`cd ../earful && uv sync --extra kokoro`). Best quality of the free options,
  but 865 MB of venv plus a 314 MB model, so it stays local. Voice via
  `KOKORO_VOICE` (default `bm_george`), interpreter via `KOKORO_PYTHON`.
- **`say`** is macOS's built-in voice. No setup, renders a digest in ~6 seconds.
  Handy for quickly re-testing the player.

Every engine returns PCM in the same format, so everything downstream — silence
detection, chapters, encoding, the manifest — is exercised identically. Only the
call to the voice differs. `gemini` stays the default.

**On a day the Gemini quota is exhausted the workflow narrates with piper
instead**, so an episode still publishes. You'll hear the difference; you won't
miss a day.

Check every published episode is still downloadable and the sizes still match:

```sh
python3 scripts/podcast_rss.py --verify
```

Preview the site locally:

```sh
python3 -m http.server 8000   # then open http://localhost:8000
```

Note that `http.server` does not support HTTP range requests, so seeking within
the audio won't work locally even though it works on the real site.

## Notes

- `anthropics/claude-code-action@v1` exits green even when Claude Code errors
  internally, so the workflow inspects the execution log itself and fails the
  run loudly. A `failure()` step sends a Telegram alert.
- Add `show_full_output: true` to the action temporarily when diagnosing a
  failing collection — error details are hidden otherwise.
- Runs finishing in under a minute usually mean the Claude step died instantly,
  almost always auth. Regenerate the token with `claude setup-token`.
- Story text is written into the page as text nodes, never `innerHTML`, and
  links are restricted to `http(s)` — the feed content is model-generated, so it
  is treated as untrusted. The audio player applies the same rule to the episode
  URL before it reaches the `<audio>` element.
- `deploy` depends on `audio` so the podcast feed is committed before Pages
  publishes. Its `if:` uses `always()` on purpose: without it, a failed or
  skipped audio job would cascade and stop the **news feed** from deploying.
  Change that condition carefully.
- Chapter offsets are keyed by story URL, which the feed-validation step already
  enforces as unique. Per-story seeking depends on that guarantee.
- Gemini TTS models are all `-preview` and may be renamed or retired at short
  notice. Override with the `GEMINI_TTS_MODEL` / `GEMINI_TTS_VOICE` environment
  variables; a failure is contained to the audio job.
- A listener part-way through an expired episode gets a 404 once it is pruned.
  Acceptable for a daily digest.

## Credits

Podcast artwork: smile icon by Culai Lai from
[Noun Project](https://thenounproject.com/browse/icons/term/smile/), CC BY 3.0.
The original download is kept at `assets/icon-source.svg`; the cover is that path
recoloured onto the site's accent green and rasterised to 3000×3000:

```sh
rsvg-convert -w 3000 -h 3000 assets/podcast-cover.svg -o /tmp/cover.png
sips -s format jpeg -s formatOptions 82 /tmp/cover.png --out assets/podcast-cover.jpg
```

Apple requires 1400–3000px square, RGB, under 500 KB.
