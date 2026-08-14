/* Renders data/news.json as a Google-News-style feed.
   Story text comes from an automated collector, so every value is inserted as a
   text node — never innerHTML — and links are restricted to http(s). */

const CATEGORIES = ['Science', 'Health', 'Environment', 'Community', 'Culture', 'Animals'];

const feedEl = document.getElementById('feed');
const filtersEl = document.getElementById('filters');
const updatedEl = document.getElementById('updated');

let stories = [];
let active = 'All';

/** Relative time, in the style of a news aggregator. */
function timeAgo(iso) {
  const then = new Date(iso);
  if (isNaN(then)) return '';
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function dayLabel(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return 'Earlier';
  const today = new Date();
  const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diff = Math.round((midnight(today) - midnight(d)) / 86400000);
  if (diff <= 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  if (diff < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
}

/** Only allow real web links — a non-http scheme (javascript:, data:) is dropped. */
function safeUrl(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : null;
  } catch {
    return null;
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function card(story) {
  const href = safeUrl(story.url);

  // Whole card is the link, like a Google News result.
  const a = el('a', 'card');
  if (href) {
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    // The audio player matches cards to chapters by url (see js/episode.js).
    a.dataset.url = story.url;
  }

  const meta = el('div', 'meta');
  meta.append(el('span', 'source', story.source || 'Unknown source'));

  const when = timeAgo(story.published);
  if (when) {
    meta.append(el('span', 'dot', '·'), el('span', null, when));
  }
  if (CATEGORIES.includes(story.category)) {
    meta.append(el('span', 'tag', story.category));
  }

  a.append(meta);

  const body = el('div', 'body');
  body.append(el('h2', null, story.headline || 'Untitled'));
  if (story.summary) body.append(el('p', 'summary', story.summary));

  const img = thumbnail(story);
  if (img) {
    // Thumbnail floats right so short headlines let the summary wrap beside it
    // instead of leaving a gap under the image.
    const row = el('div', 'has-thumb');
    row.append(img, body);
    a.append(row);
  } else {
    a.append(body);
  }
  return a;
}

/** Thumbnail element, or null when there's no usable image.
    Publisher images are hotlinked, so any that 404 or block us are hidden. */
function thumbnail(story) {
  const src = story.image && safeUrl(story.image);
  if (!src) return null;

  const wrap = el('div', 'thumb');
  const img = document.createElement('img');
  img.src = src;
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  img.addEventListener('error', () => wrap.remove());
  wrap.append(img);
  return wrap;
}

function render() {
  const shown = active === 'All'
    ? stories
    : stories.filter((s) => s.category === active);

  feedEl.replaceChildren();

  if (!shown.length) {
    feedEl.append(el('p', 'state', stories.length
      ? `No ${active.toLowerCase()} stories yet — check back tomorrow.`
      : 'No stories yet. The collector runs each morning.'));
    return;
  }

  let lastDay = null;
  for (const story of shown) {
    const label = dayLabel(story.published);
    if (label !== lastDay) {
      feedEl.append(el('h2', 'daybreak', label));
      lastDay = label;
    }
    feedEl.append(card(story));
  }

  // Re-add the per-story play buttons: this rebuilds the DOM on every filter
  // click, so the player has to decorate the new cards each time.
  window.happyPlayer?.decorate(feedEl);
}

function buildFilters() {
  const present = CATEGORIES.filter((c) => stories.some((s) => s.category === c));
  if (!present.length) return;

  for (const name of ['All', ...present]) {
    const b = el('button', null, name);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(name === active));
    b.addEventListener('click', () => {
      active = name;
      for (const other of filtersEl.children) {
        other.setAttribute('aria-pressed', String(other.textContent === name));
      }
      render();
    });
    filtersEl.append(b);
  }
}

fetch('data/news.json', { cache: 'no-cache' })
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((data) => {
    stories = Array.isArray(data.stories) ? data.stories : [];
    // Newest first, regardless of the order they were written in.
    stories.sort((a, b) => new Date(b.published) - new Date(a.published));

    buildFilters();
    render();

    if (data.updated) {
      const d = new Date(data.updated);
      if (!isNaN(d)) updatedEl.textContent = `Feed updated ${d.toLocaleString()}`;
    }
  })
  .catch((err) => {
    feedEl.replaceChildren(el('p', 'state', "Couldn't load the feed. Please try again later."));
    console.error('Failed to load data/news.json:', err);
  });
