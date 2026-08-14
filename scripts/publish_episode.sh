#!/usr/bin/env bash
# Upload the day's mp3 as a GitHub Release asset, then record it in the manifest.
#
# Releases are the blob store: no size limit, no bandwidth limit, and deleting an
# asset actually reclaims the space (a committed mp3 would live in every clone
# forever). The repo is public, so the asset URL resolves without auth — which
# podcast apps require.
#
# Ordering matters: the manifest entry is appended ONLY after a confirmed upload,
# so data/episodes.json can never describe an asset that doesn't exist.
#
# Usage: publish_episode.sh <episode.json>   (as emitted by digest.py)
set -euo pipefail

episode_json="${1:?usage: publish_episode.sh <episode.json>}"
episodes="${EPISODES_FILE:-data/episodes.json}"

field() {
  python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" \
    "$episode_json" "$1"
}

if [ "$(field episode)" = "none" ]; then
  echo "No episode to publish."
  exit 0
fi

date=$(field date)
title=$(field title)
mp3=$(field file)
tag="audio-${date}"

if [ ! -f "$mp3" ]; then
  echo "::error::mp3 not found at $mp3"
  exit 1
fi

# Release notes double as a browsable episode archive on the Releases page.
notes_file="$(dirname "$episode_json")/release-notes.md"
python3 - "$episode_json" >"$notes_file" <<'PY'
import json, sys

episode = json.load(open(sys.argv[1]))
print(f"{episode['title']} — {episode['duration'] / 60:.0f} min, "
      f"{episode['storyCount']} stories.\n")
for chapter in episode.get("chapters", []):
    stamp = int(chapter["start"])
    print(f"- `{stamp // 60}:{stamp % 60:02d}` [{chapter['headline']}]({chapter['url']})")
print("\nAudio is pruned after 30 days. Subscribe via podcast.xml on the site.")
PY

# --latest=false keeps the daily audio release from hijacking the repo's
# "Latest release" badge. --clobber makes a same-day re-dispatch idempotent.
if gh release view "$tag" >/dev/null 2>&1; then
  echo "Release $tag exists — replacing the asset."
  gh release upload "$tag" "$mp3" --clobber
else
  gh release create "$tag" "$mp3" \
    --title "$title" \
    --notes-file "$notes_file" \
    --latest=false
fi

asset=$(basename "$mp3")
url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-onato/happy-news}/releases/download/${tag}/${asset}"

# Confirm the asset is really there and publicly fetchable before describing it
# in the feed.
if ! curl -sfIL "$url" >/dev/null; then
  echo "::error::uploaded asset is not fetchable at $url"
  exit 1
fi

echo "Published $url"

python3 - "$episode_json" "$episodes" "$url" <<'PY'
import json, sys
from datetime import datetime, timezone

episode_path, manifest_path, url = sys.argv[1], sys.argv[2], sys.argv[3]

episode = json.load(open(episode_path))
episode["url"] = url
episode.pop("file", None)          # local path, not for the manifest

try:
    manifest = json.load(open(manifest_path))
except FileNotFoundError:
    manifest = {"episodes": []}

kept = [e for e in manifest.get("episodes", []) if e.get("guid") != episode["guid"]]
kept.append(episode)
kept.sort(key=lambda e: e.get("date", ""), reverse=True)   # newest first

manifest["episodes"] = kept
manifest["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(manifest_path, "w") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")

print(f"{manifest_path}: {len(kept)} episode(s).")
PY
