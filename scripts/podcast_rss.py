#!/usr/bin/env python3
"""Generate podcast.xml from data/episodes.json.

A pure function of the manifest — no network calls — so it can be run and
diffed locally. The manifest is the source of truth for both this feed and the
site's player; GitHub Releases are just blob storage.

Every URL is written as an explicit absolute https:// URL. The Pages site does
not enforce HTTPS and plain HTTP does not redirect, so a relative or
protocol-relative URL would leave podcast apps fetching cleartext.

Usage:
    python3 scripts/podcast_rss.py --site https://www.onato.com/happy-news/
    python3 scripts/podcast_rss.py --verify        # HEAD-check every enclosure
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"

RETAIN_DAYS = 30  # must match scripts/prune_episodes.sh

TITLE = "Happy News"
DESCRIPTION = (
    "A short daily audio digest of genuinely good news, read aloud each morning. "
    "Collected and narrated automatically. "
    "Artwork: smile icon by Culai Lai from Noun Project (CC BY 3.0)."
)
AUTHOR = "Happy News"
OWNER_EMAIL = "onato.com@gmail.com"
COVER = "assets/podcast-cover.jpg"


def rfc2822(iso: str) -> str:
    """RSS requires RFC-2822 dates. strftime would be locale-dependent."""
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return format_datetime(parsed.astimezone(timezone.utc))


def hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}"


def absolute(site: str, path: str) -> str:
    return site.rstrip("/") + "/" + path.lstrip("/")


def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    """ElementTree handles escaping, so untrusted headline text is safe here."""
    node = ET.SubElement(parent, tag, {k: v for k, v in attrs.items()})
    if text is not None:
        node.text = text
    return node


def build(episodes: list[dict], site: str) -> ET.ElementTree:
    ET.register_namespace("itunes", ITUNES)
    ET.register_namespace("atom", ATOM)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    cover_url = absolute(site, COVER)
    now = datetime.now(timezone.utc)

    sub(channel, "title", TITLE)
    sub(channel, "link", site)
    sub(channel, "description", DESCRIPTION)
    sub(channel, "language", "en")
    sub(channel, "lastBuildDate", format_datetime(now))
    sub(channel, "generator", "happy-news")
    ET.SubElement(channel, f"{{{ATOM}}}link", {
        "href": absolute(site, "podcast.xml"),
        "rel": "self",
        "type": "application/rss+xml",
    })

    sub(channel, f"{{{ITUNES}}}author", AUTHOR)
    sub(channel, f"{{{ITUNES}}}summary", DESCRIPTION)
    sub(channel, f"{{{ITUNES}}}explicit", "false")
    sub(channel, f"{{{ITUNES}}}type", "episodic")
    ET.SubElement(channel, f"{{{ITUNES}}}image", {"href": cover_url})

    owner = ET.SubElement(channel, f"{{{ITUNES}}}owner")
    sub(owner, f"{{{ITUNES}}}name", AUTHOR)
    sub(owner, f"{{{ITUNES}}}email", OWNER_EMAIL)

    category = ET.SubElement(channel, f"{{{ITUNES}}}category", {"text": "News"})
    ET.SubElement(category, f"{{{ITUNES}}}category", {"text": "Daily News"})

    # RSS 2.0 image, for readers that ignore the iTunes namespace.
    image = ET.SubElement(channel, "image")
    sub(image, "url", cover_url)
    sub(image, "title", TITLE)
    sub(image, "link", site)

    for episode in episodes:
        item = ET.SubElement(channel, "item")
        sub(item, "title", episode["title"])
        sub(item, "description", episode.get("summary", ""))
        sub(item, f"{{{ITUNES}}}summary", episode.get("summary", ""))
        sub(item, "link", site)
        # isPermaLink=false with a stable synthetic id: re-uploading an asset
        # must not create a duplicate episode in subscribers' apps.
        sub(item, "guid", episode["guid"], isPermaLink="false")
        sub(item, "pubDate", rfc2822(episode["published"]))
        ET.SubElement(item, "enclosure", {
            "url": episode["url"],
            # Must be the true byte count — a wrong value breaks scrubbing in
            # several clients.
            "length": str(episode["bytes"]),
            "type": "audio/mpeg",
        })
        sub(item, f"{{{ITUNES}}}duration", hms(episode["duration"]))
        sub(item, f"{{{ITUNES}}}episodeType", "full")
        sub(item, f"{{{ITUNES}}}explicit", "false")

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    return tree


def within_retention(episodes: list[dict], days: int = RETAIN_DAYS) -> list[dict]:
    """Drop episodes whose assets the prune step has deleted (or will delete)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [e for e in episodes if e.get("date", "") >= cutoff]


def verify(episodes: list[dict]) -> int:
    """HEAD-check every enclosure. Catches manifest/Releases drift."""
    problems = 0
    for episode in episodes:
        url = episode["url"]
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) != episode["bytes"]:
                    print(f"MISMATCH {episode['date']}: manifest {episode['bytes']} "
                          f"vs actual {length}")
                    problems += 1
                else:
                    print(f"ok       {episode['date']}  {url}")
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code} {episode['date']}: {url}")
            problems += 1
        except urllib.error.URLError as exc:
            print(f"UNREACHABLE {episode['date']}: {exc}")
            problems += 1
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=Path, default=Path("data/episodes.json"))
    parser.add_argument("--out", type=Path, default=Path("podcast.xml"))
    parser.add_argument("--site", default="https://www.onato.com/happy-news/")
    parser.add_argument("--verify", action="store_true",
                        help="HEAD-check every enclosure URL and exit")
    args = parser.parse_args()

    if not args.episodes.exists():
        print(f"{args.episodes} does not exist — nothing to generate.", file=sys.stderr)
        return 0

    manifest = json.loads(args.episodes.read_text())
    episodes = manifest.get("episodes") or []

    if args.verify:
        return 1 if verify(episodes) else 0

    kept = within_retention(episodes)
    dropped = len(episodes) - len(kept)

    if dropped:
        # Never silently truncate — say what left the feed.
        print(f"Dropped {dropped} episode(s) older than {RETAIN_DAYS} days.", file=sys.stderr)
        manifest["episodes"] = kept
        manifest["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        args.episodes.write_text(json.dumps(manifest, indent=2) + "\n")

    if not args.site.startswith("https://"):
        print(f"error: --site must be an absolute https URL (got {args.site!r})",
              file=sys.stderr)
        return 1

    tree = build(kept, args.site)
    tree.write(args.out, encoding="UTF-8", xml_declaration=True)
    print(f"{args.out}: {len(kept)} episode(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
