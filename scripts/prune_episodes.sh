#!/usr/bin/env bash
# Delete audio releases older than the retention window.
#
# Releases are unlimited in size and bandwidth, but not in usefulness — a month
# of episodes is plenty for a daily news digest, and pruning keeps the Releases
# page readable.
#
# The cutoff compares the date embedded in the tag rather than the release's
# createdAt, so pruning and RSS regeneration use the same key and stay in
# lockstep by construction. Lexicographic < on YYYY-MM-DD is a correct date
# comparison — that is why the tag carries an ISO date.
#
# Must run BEFORE podcast_rss.py so the feed never links a deleted asset.
set -euo pipefail

days="${RETAIN_DAYS:-30}"

# GNU date on the runners; BSD date locally on macOS.
if cutoff=$(date -u -d "${days} days ago" +%Y-%m-%d 2>/dev/null); then
  :
else
  cutoff=$(date -u -v-"${days}"d +%Y-%m-%d)
fi

echo "Pruning audio releases dated before $cutoff."

pruned=0
while read -r tag; do
  [ -n "$tag" ] || continue
  day="${tag#audio-}"
  if [[ "$day" < "$cutoff" ]]; then
    echo "  deleting $tag"
    # --cleanup-tag so we don't accumulate 365 dangling git tags a year.
    gh release delete "$tag" --yes --cleanup-tag
    pruned=$((pruned + 1))
  fi
done < <(gh release list --limit 200 --json tagName \
           --jq '.[].tagName | select(startswith("audio-"))')

echo "Pruned $pruned release(s)."
