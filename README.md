# 🌻 Happy News

A daily feed of genuinely good news, collected automatically by Claude Code and
rendered as a static news feed. Every story links out to its original publisher.

## How it works

1. **`.github/workflows/happy-news.yml`** runs daily at 18:00 UTC (~6am NZ).
   Claude Code searches for uplifting stories published in the last 48 hours
   across six categories, deduplicates them against the existing feed, and
   prepends the new ones to `data/news.json`.
2. The run validates the JSON, commits it, and sends a Telegram ping with the
   top headline and a link to the feed.
3. **`.github/workflows/pages.yml`** publishes the site to GitHub Pages on every
   push to `main`.

The feed is capped at the 200 most recent stories.

## Setup

### Secrets

Set these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | What it is |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | Generate with `claude setup-token` |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/botfather) |

```sh
printf '%s' '<token>' | gh secret set CLAUDE_CODE_OAUTH_TOKEN
printf '%s' '<token>' | gh secret set TELEGRAM_BOT_TOKEN
```

### Variables

Under the **Variables** tab:

| Variable | What it is |
| --- | --- |
| `TELEGRAM_CHAT_ID` | Your chat ID with the bot |
| `FEED_URL` | Optional — where the Telegram ping links; defaults to the repo |

```sh
gh variable set TELEGRAM_CHAT_ID --body '<id>'
gh variable set FEED_URL --body 'https://<user>.github.io/happy-news/'
```

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

Preview the site locally:

```sh
python3 -m http.server 8000   # then open http://localhost:8000
```

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
  is treated as untrusted.
