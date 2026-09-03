#!/usr/bin/env bash
# Append a content hash to the css/js URLs in index.html, so a deploy can never
# be served stale from a cache.
#
# GitHub Pages sends `cache-control: max-age=600` on static assets, which meant a
# freshly pushed stylesheet could keep losing to a 10-minute-old copy — including
# inside in-app browsers, which keep their own cache. The HTML itself is served
# with a much shorter TTL, so changing the query string in it is enough to pull
# the new asset through immediately.
#
# Runs at deploy time against the checkout, never committed: index.html in git
# stays clean, with plain unversioned hrefs.
set -euo pipefail

cd "$(dirname "$0")/.."

for asset in css/styles.css js/theme.js js/episode.js js/feed.js; do
  [ -f "$asset" ] || { echo "stamp: missing $asset, skipping" >&2; continue; }
  # Short content hash: changes exactly when the file does.
  if command -v sha256sum >/dev/null; then
    hash=$(sha256sum "$asset" | cut -c1-8)      # Linux (the CI runner)
  else
    hash=$(shasum -a 256 "$asset" | cut -c1-8)  # macOS (local testing)
  fi
  # Only touch the exact href/src, and only when it isn't already stamped.
  python3 - "$asset" "$hash" <<'PY'
import re, sys
asset, hash_ = sys.argv[1], sys.argv[2]
html = open('index.html').read()
pattern = re.compile(r'((?:href|src)=")' + re.escape(asset) + r'(\?v=[0-9a-f]+)?(")')
html, n = pattern.subn(lambda m: f'{m.group(1)}{asset}?v={hash_}{m.group(3)}', html)
if not n:
    print(f'stamp: no reference to {asset} in index.html', file=sys.stderr)
open('index.html', 'w').write(html)
PY
  echo "stamped $asset -> ?v=$hash"
done
