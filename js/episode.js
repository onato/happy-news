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
  //
  // Resolved against document.baseURI so a RELATIVE episode url works. The
  // deploy job rewrites data/episodes.json to point at audio/ on this origin
  // (Release assets can't drive an <audio> element — see
  // scripts/stage_web_audio.sh), and a bare `new URL(relative)` throws, which
  // previously left the player hidden. Resolving first keeps the http(s)-only
  // check doing its real job: rejecting javascript: and data: sources.
  function safeUrl(url) {
    try {
      const u = new URL(url, document.baseURI);
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
  const prevBtn = document.getElementById('player-prev');
  const nextBtn = document.getElementById('player-next');
  const ticksEl = document.getElementById('player-ticks');

  if (!box || !audio) return;

  let chapters = new Map();   // story url -> chapter
  let ordered = [];           // same chapters, sorted by start — skip order
  let cards = new Map();      // story url -> card element
  let playingUrl = null;
  let scrubbing = false;

  /* Whether the playing card is currently on screen, and on which side of the
     viewport it sits when it isn't. Drives the "jump to playing" arrow. */
  let watched = null;         // card element the observer is watching
  let observer = null;

  // App-Store-style progress ring: the dash offset of an SVG circle tracks how
  // far through the chapter (or the whole episode) playback has reached.
  const RING_R = 45;                       // viewBox 0 0 100 100, stroke 8
  const RING_C = 2 * Math.PI * RING_R;

  function ring() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ring');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('aria-hidden', 'true');
    const track = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    track.setAttribute('class', 'ring-track');
    const fill = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    fill.setAttribute('class', 'ring-fill');
    for (const c of [track, fill]) {
      c.setAttribute('cx', '50');
      c.setAttribute('cy', '50');
      c.setAttribute('r', String(RING_R));
    }
    fill.style.strokeDasharray = String(RING_C);
    fill.style.strokeDashoffset = String(RING_C);
    svg.append(track, fill);
    return svg;
  }

  /** Set a button's ring to a 0..1 fraction; null hides it. */
  function setRing(button, fraction) {
    const fill = button && button.querySelector('.ring-fill');
    if (!fill) return;
    if (fraction == null) {
      button.classList.remove('has-progress');
      fill.style.strokeDashoffset = String(RING_C);
      return;
    }
    const f = Math.min(1, Math.max(0, fraction));
    button.classList.add('has-progress');
    fill.style.strokeDashoffset = String(RING_C * (1 - f));
  }

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
    toggle.querySelector('.glyph').textContent = playing ? '❚❚' : '▶';
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
        button.querySelector('.glyph').textContent = on ? '❚❚' : '▶';
        button.setAttribute('aria-label',
          on ? 'Pause' : 'Play this story in the digest');
        setRing(button, on
          ? (audio.currentTime - current.start) / current.duration
          : null);
      }
    }

    // Whole-episode progress on the transport button.
    setRing(toggle, playing && audio.duration
      ? audio.currentTime / audio.duration
      : null);

    watch(active || null);
  }

  /* ---- "jump to the playing story" arrow ----
     Shown only while something is playing AND its card is scrolled out of view.
     The arrow points the way; tapping scrolls the card into the middle of the
     screen. */
  const jump = document.getElementById('player-jump');

  function watch(card) {
    if (card === watched) return;
    if (observer) observer.disconnect();
    watched = card;
    if (!card || !jump || !('IntersectionObserver' in window)) {
      if (jump) jump.hidden = true;
      return;
    }
    observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        jump.hidden = true;
        return;
      }
      // Off-screen: is it above or below the viewport?
      const above = entry.boundingClientRect.top < 0;
      jump.hidden = false;
      jump.dataset.dir = above ? 'up' : 'down';
      jump.querySelector('.glyph').textContent = above ? '↑' : '↓';
      jump.setAttribute('aria-label',
        above ? 'Scroll up to the playing story' : 'Scroll down to the playing story');
    }, { threshold: 0.25 });
    observer.observe(card);
  }

  if (jump) {
    jump.addEventListener('click', () => {
      if (watched) watched.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  function playFrom(chapter) {
    audio.currentTime = chapter.start;
    audio.play().catch(() => { /* autoplay blocked; the bar still works */ });
  }

  /* ---- skipping between stories ----
     `ordered` is the chapter list sorted by start, so skipping is a walk along
     it rather than a lookup: the listener may be in a gap between chapters (the
     intro, or a pause), where chapterAt() finds nothing but "next" still has an
     obvious answer. */

  /** Index of the chapter containing `time`, else the one before it; -1 before the first. */
  function indexAt(time) {
    let i = -1;
    for (let n = 0; n < ordered.length; n += 1) {
      if (time >= ordered[n].start) i = n; else break;
    }
    return i;
  }

  /** Seek to the next story, or to the end of the episode past the last one. */
  function skipNext() {
    if (!ordered.length) return;
    const next = ordered[indexAt(audio.currentTime) + 1];
    if (next) {
      seekTo(next.start);
    } else if (isFinite(audio.duration)) {
      // Past the final story: run to the end and stop, like a normal podcast.
      seekTo(audio.duration);
      audio.pause();
    }
  }

  /* Restart the current story, unless playback only just entered it — then step
     back to the previous one. The grace window is the convention every music
     player uses, and it makes a double-tap mean "back one story". */
  const RESTART_WINDOW = 3;

  function skipPrev() {
    if (!ordered.length) return;
    const i = indexAt(audio.currentTime);
    if (i < 0) {
      seekTo(0);
      return;
    }
    const inCurrent = audio.currentTime - ordered[i].start;
    const target = (inCurrent > RESTART_WINDOW || i === 0) ? ordered[i] : ordered[i - 1];
    seekTo(target.start);
  }

  /** Move the playhead and keep the bar in step, without waiting for timeupdate. */
  function seekTo(time) {
    audio.currentTime = Math.max(0, Math.min(time, audio.duration || time));
    seek.value = String(Math.floor(audio.currentTime));
    timeEl.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`;
    paint();
  }

  /* ---- chapter marks on the scrubber ----
     One absolutely-positioned tick per story start, as a percentage of the
     episode duration, so it survives any bar width. Needs the duration, which
     arrives with the metadata, so this runs again on loadedmetadata. */
  function drawTicks(total) {
    if (!ticksEl) return;
    ticksEl.replaceChildren();
    if (!isFinite(total) || total <= 0 || !ordered.length) return;

    for (const chapter of ordered) {
      // A tick at 0 would sit under the thumb's start position; skip it.
      if (chapter.start <= 0) continue;
      const tick = document.createElement('span');
      tick.className = 'tick';
      tick.style.left = `${(chapter.start / total) * 100}%`;
      ticksEl.append(tick);
    }
  }

  /** Redraw from the element's own duration, once the audio has told us. */
  function renderTicks() {
    drawTicks(audio.duration || 0);
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
      const glyph = document.createElement('span');
      glyph.className = 'glyph';
      glyph.textContent = '▶';
      button.append(ring(), glyph);
      button.setAttribute('aria-label', 'Play this story in the digest');
      button.addEventListener('click', (event) => {
        // The whole card is a link — without this, playing navigates away.
        event.preventDefault();
        event.stopPropagation();
        if (!audio.paused && playingUrl === url) {
          audio.pause();
        } else if (audio.paused && chapterAt(audio.currentTime) === chapter) {
          // Paused mid-story: resume where it stopped, don't restart the story.
          audio.play().catch(() => {});
        } else {
          playFrom(chapter);
        }
      });
      card.prepend(button);
    }
    paint();
  }

  window.happyPlayer = { decorate };

  toggle.prepend(ring());

  toggle.addEventListener('click', () => {
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });

  if (prevBtn) prevBtn.addEventListener('click', skipPrev);
  if (nextBtn) nextBtn.addEventListener('click', skipNext);

  audio.addEventListener('play', paint);
  audio.addEventListener('pause', paint);
  audio.addEventListener('ended', paint);

  audio.addEventListener('loadedmetadata', () => {
    seek.max = String(Math.floor(audio.duration || 0));
    timeEl.textContent = `${fmt(0)} / ${fmt(audio.duration)}`;
    renderTicks();
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

      const valid = (episode.chapters || [])
        .filter((c) => c && typeof c.url === 'string' && isFinite(c.start));
      chapters = new Map(valid.map((c) => [c.url, c]));
      ordered = valid.slice().sort((a, b) => a.start - b.start);

      // preload="none" means duration is unknown until playback starts, so the
      // ticks are drawn from the manifest first and redrawn on loadedmetadata.
      if (!audio.duration && isFinite(episode.duration) && episode.duration > 0) {
        seek.max = String(Math.floor(episode.duration));
        drawTicks(episode.duration);
      } else {
        renderTicks();
      }

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
