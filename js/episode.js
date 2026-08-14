/* Sticky player for the daily audio digest (data/episodes.json).

   Same posture as feed.js: episode text comes from an automated pipeline, so
   every value is inserted as a text node — never innerHTML — and the audio
   source is restricted to http(s) exactly as story links are.

   The whole digest is one mp3. Per-story buttons seek to that story's offset
   rather than loading separate files, so playback is continuous and there is
   only ever one download. Offsets are computed at generation time from the raw
   PCM stream (see scripts/digest.py), which is why they land exactly on the
   first word of a headline.

   Chapters are keyed by story url — the feed's unique key, enforced unique by
   the workflow's "Validate the feed" step. Seek correctness depends on that. */

(function () {
  'use strict';

  // Duplicated from feed.js rather than shared: keeps both files independently
  // loadable, with no load-order coupling and no module conversion.
  function safeUrl(url) {
    try {
      const u = new URL(url);
      return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : null;
    } catch {
      return null;
    }
  }

  const box = document.getElementById('player');
  const audio = document.getElementById('player-audio');
  const toggle = document.getElementById('player-toggle');
  const seek = document.getElementById('player-seek');
  const titleEl = document.getElementById('player-title');
  const timeEl = document.getElementById('player-time');

  if (!box || !audio) return;

  let chapters = new Map();   // story url -> chapter
  let cards = new Map();      // story url -> card element
  let playingUrl = null;
  let scrubbing = false;

  function fmt(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    const total = Math.floor(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  function chapterAt(time) {
    let found = null;
    for (const chapter of chapters.values()) {
      if (time >= chapter.start && time < chapter.start + chapter.duration) {
        found = chapter;
        break;
      }
    }
    return found;
  }

  /** Reflect play state on the bar and on whichever card is currently sounding. */
  function paint() {
    const playing = !audio.paused && !audio.ended;
    toggle.textContent = playing ? '❚❚' : '▶';
    toggle.setAttribute('aria-label', playing ? 'Pause' : 'Play the digest');
    toggle.setAttribute('aria-pressed', String(playing));

    const current = playing ? chapterAt(audio.currentTime) : null;
    const url = current ? current.url : null;

    if (url !== playingUrl) {
      const previous = cards.get(playingUrl);
      if (previous) previous.classList.remove('is-playing');
      playingUrl = url;
    }
    const active = cards.get(playingUrl);
    if (active) active.classList.add('is-playing');

    for (const [cardUrl, card] of cards) {
      const button = card.querySelector('.card-play');
      if (button) {
        const on = playing && cardUrl === playingUrl;
        button.textContent = on ? '❚❚' : '▶';
        button.setAttribute('aria-label',
          on ? 'Pause' : 'Play this story in the digest');
      }
    }
  }

  function playFrom(chapter) {
    audio.currentTime = chapter.start;
    audio.play().catch(() => { /* autoplay blocked; the bar still works */ });
  }

  /* Called by feed.js after every render. render() rebuilds the DOM on each
     filter click, so this must be safe to run repeatedly. */
  function decorate(root) {
    cards = new Map();
    if (!chapters.size) return;

    for (const card of root.querySelectorAll('.card[data-url]')) {
      const url = card.dataset.url;
      const chapter = chapters.get(url);
      if (!chapter) continue;         // older stories aren't in today's episode
      cards.set(url, card);

      if (card.querySelector('.card-play')) continue;   // already decorated

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'card-play';
      button.textContent = '▶';
      button.setAttribute('aria-label', 'Play this story in the digest');
      button.addEventListener('click', (event) => {
        // The whole card is a link — without this, playing navigates away.
        event.preventDefault();
        event.stopPropagation();
        if (!audio.paused && playingUrl === url) {
          audio.pause();
        } else {
          playFrom(chapter);
        }
      });
      card.prepend(button);
    }
    paint();
  }

  window.happyPlayer = { decorate };

  toggle.addEventListener('click', () => {
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });

  audio.addEventListener('play', paint);
  audio.addEventListener('pause', paint);
  audio.addEventListener('ended', paint);

  audio.addEventListener('loadedmetadata', () => {
    seek.max = String(Math.floor(audio.duration || 0));
    timeEl.textContent = `${fmt(0)} / ${fmt(audio.duration)}`;
  });

  audio.addEventListener('timeupdate', () => {
    if (!scrubbing) seek.value = String(Math.floor(audio.currentTime));
    timeEl.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`;
    paint();
  });

  // Track scrubbing so timeupdate doesn't fight the thumb mid-drag.
  seek.addEventListener('pointerdown', () => { scrubbing = true; });
  seek.addEventListener('pointerup', () => { scrubbing = false; });
  seek.addEventListener('input', () => {
    audio.currentTime = Number(seek.value);
    timeEl.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`;
  });

  fetch('data/episodes.json', { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => {
      const episode = Array.isArray(data.episodes) ? data.episodes[0] : null;
      const src = episode && safeUrl(episode.url);
      if (!src) return;             // no episode today — the bar stays hidden

      audio.src = src;
      titleEl.textContent = episode.title || 'Today’s digest';

      const mins = Math.max(1, Math.round((episode.duration || 0) / 60));
      const count = episode.storyCount || 0;
      titleEl.title = `${mins} min · ${count} stor${count === 1 ? 'y' : 'ies'}`;
      timeEl.textContent = `0:00 / ${fmt(episode.duration || 0)}`;

      chapters = new Map(
        (episode.chapters || [])
          .filter((c) => c && typeof c.url === 'string' && isFinite(c.start))
          .map((c) => [c.url, c])
      );

      box.hidden = false;
      // The feed may have rendered before this resolved.
      const feed = document.getElementById('feed');
      if (feed) decorate(feed);
    })
    .catch((err) => {
      // Audio is best-effort; the story feed must never break because of it.
      console.error('Failed to load data/episodes.json:', err);
    });
})();
