#!/usr/bin/env bash
# Download the retained episodes into audio/ so Pages serves them same-origin.
#
# Why this exists: a GitHub Release asset cannot drive an <audio> element. The
# download URL 302s to release-assets.githubusercontent.com, which sends no
# access-control-allow-origin and labels the file
# `content-type: application/octet-stream` with `content-disposition: attachment`.
# Desktop browsers frequently sniff past that and play it anyway; mobile Safari
# honours the attachment header and refuses, so the site's play button worked on
# a laptop and never on a phone.
#
# Served from Pages instead, the same bytes arrive as an ordinary same-origin
# audio/mpeg file with range support, which every browser plays.
#
# Nothing here is committed. The deploy job runs this immediately before
# upload-pages-artifact, which packages the WORKING TREE — so these files reach
# the site without ever entering git history (a year of episodes would be
# ~0.75 GB of permanent history, which is why Releases is still the blob store).
# Rebuilt from data/episodes.json on every deploy, so retention needs no separate
# pruning: an episode drops out of the site the moment it leaves the manifest.
set -euo pipefail

manifest="${EPISODES_FILE:-data/episodes.json}"
out="${WEB_AUDIO_DIR:-audio}"

if [ ! -f "$manifest" ]; then
  echo "No $manifest — nothing to stage."
  exit 0
fi

mkdir -p "$out"

# `url` is the Release asset; `guid` gives the local filename, matching what
# rewrite_manifest_urls (below) will point the player at.
while read -r guid url; do
  [ -n "$guid" ] && [ -n "$url" ] || continue
  target="$out/${guid}.mp3"
  if [ -f "$target" ]; then
    echo "  have $target"
    continue
  fi
  echo "  fetching $guid"
  # -L follows the Release 302; --fail so a missing asset doesn't write a
  # zero-byte file the player would choke on.
  if ! curl -sfL "$url" -o "$target"; then
    echo "::warning::could not fetch $url — the player will fall back to the Release URL."
    rm -f "$target"
  fi
done < <(python3 -c "
import json
for e in json.load(open('$manifest')).get('episodes', []):
    if e.get('guid') and e.get('url'):
        print(e['guid'], e['url'])
")

# Point the PLAYER at the local copies, leaving the podcast enclosures alone.
# Podcast apps are not browsers: they ignore CORS and handle the attachment
# header fine, and Releases has no bandwidth limit, so the RSS feed keeps
# linking the Release asset. Only data/episodes.json — which just the web
# player reads — is rewritten, and only for episodes actually staged.
python3 - "$manifest" "$out" <<'PY'
import json, os, sys

manifest_path, out_dir = sys.argv[1], sys.argv[2]
manifest = json.load(open(manifest_path))

staged = 0
for episode in manifest.get("episodes", []):
    local = os.path.join(out_dir, f"{episode.get('guid')}.mp3")
    if os.path.exists(local) and os.path.getsize(local) > 0:
        # Relative, so it works on any Pages base path without knowing FEED_URL.
        episode["url"] = local
        staged += 1

with open(manifest_path, "w") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")

print(f"Staged {staged} episode(s) for same-origin playback.")
PY

du -sh "$out" 2>/dev/null || true

# Cache-bust the site's own assets.
#
# Pages serves css/js with `cache-control: max-age=600` and no way to override
# it, so a browser that loaded the page before a fix can keep serving the stale
# file for ten minutes — mobile Safari, in practice, for considerably longer.
# That is not a hypothetical: a stale episode.js is exactly what kept the player
# hidden after the relative-url fix had already deployed.
#
# Appending the commit sha makes each deploy a new URL, so a fix reaches phones
# immediately instead of whenever the cache happens to expire. Done here in the
# artifact rather than committed, so index.html stays clean in git.
stamp="${GITHUB_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
stamp="${stamp:0:8}"
python3 - index.html "$stamp" <<'PY'
import re, sys

path, stamp = sys.argv[1], sys.argv[2]
html = open(path).read()

# Only local assets; leave any absolute URL alone. Strip an existing ?v= first
# so re-running is idempotent rather than accumulating query strings.
def stamp_asset(match):
    quote, url = match.group(2), match.group(3)
    if "//" in url:
        return match.group(0)
    url = url.split("?", 1)[0]
    return f"{match.group(1)}{quote}{url}?v={stamp}{quote}"

html = re.sub(r'(src=|href=)(["\'])((?:js|css)/[^"\']+)\2', stamp_asset, html)
open(path, "w").write(html)
print(f"Cache-busted local assets with ?v={stamp}")
PY

grep -oE '(js|css)/[^"]+' index.html | head -5
