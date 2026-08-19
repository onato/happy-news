/* Theme toggle: auto (follow the OS) → light → dark → auto.
   The chosen mode is stored in localStorage under "theme"; "auto" is stored as
   nothing so a fresh visitor and a reset visitor look the same. An inline
   script in index.html applies the saved value before first paint. */

(function () {
  var root = document.documentElement;
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;

  var glyph = btn.querySelector('.glyph');
  var media = window.matchMedia('(prefers-color-scheme: dark)');
  var ORDER = ['auto', 'light', 'dark'];

  function stored() {
    try {
      var t = localStorage.getItem('theme');
      return t === 'light' || t === 'dark' ? t : 'auto';
    } catch (e) { return 'auto'; }
  }

  function store(mode) {
    try {
      if (mode === 'auto') localStorage.removeItem('theme');
      else localStorage.setItem('theme', mode);
    } catch (e) {}
  }

  function effective(mode) {
    return mode === 'auto' ? (media.matches ? 'dark' : 'light') : mode;
  }

  function render() {
    var mode = stored();
    if (mode === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);

    var eff = effective(mode);
    var next = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];

    btn.dataset.mode = mode;
    glyph.textContent = eff === 'dark' ? '☾' : '☀';
    btn.setAttribute('aria-label',
      'Theme: ' + (mode === 'auto' ? 'auto (' + eff + ')' : mode) + '. Switch to ' + next + '.');
    btn.title = btn.getAttribute('aria-label');
  }

  btn.addEventListener('click', function () {
    var mode = stored();
    store(ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length]);
    render();
  });

  // In auto mode, follow the OS if it changes while the page is open.
  if (media.addEventListener) media.addEventListener('change', render);
  else if (media.addListener) media.addListener(render);

  // Keep multiple tabs in step.
  window.addEventListener('storage', function (e) {
    if (e.key === 'theme' || e.key === null) render();
  });

  render();
})();
