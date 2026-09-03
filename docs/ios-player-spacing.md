# The mobile player bar and iOS bottom chrome

Unresolved. Six attempts, none successful; all were reverted in full. This is
what was measured, what was ruled out, and how to test it properly next time.

## The problem

On a phone the audio player is `position: fixed; bottom: 0` (see the
`max-width: 640px` block in `css/styles.css`). In **SFSafariViewController** —
the in-app browser Telegram opens links in, and therefore the one used every
morning when following the daily ping — the browser's own floating toolbar sits
over the bottom of the page, and the strip between the player bar and the
bottom of the screen does not belong to the bar.

Full-screen **Safari is fine** and always was. Every attempt to fix
SFSafariViewController with hardcoded padding made Safari worse, because the
two behave differently (see the measurements below).

## Measurements

Taken with a probe page on an **iPhone 15, 852 css px tall, dpr 3**, with
`viewport-fit=cover` set on the probe.

| | Safari | SFSafariViewController |
| --- | --- | --- |
| `screen.height` | 852 | 852 |
| `innerHeight` | 695 | 651 |
| `visualViewport.height` | 695 | 651 |
| `100vh` | **735** | 651 |
| `100svh` | 695 | 651 |
| `100lvh` | **735** | 651 |
| `100dvh` | 695 | 651 |
| `env(safe-area-inset-bottom)` | **0** | **0** |
| bar `getBoundingClientRect().bottom` | 695 | 651 |
| gap to `screen.height` | 157 | 201 |

Read off a screenshot of the same probe: the fixed bar **renders at 742–782 css**
in SFSafariViewController, i.e. well below the reported `innerHeight` of 651 and
about 70px above the glass. The toolbar pill occupies roughly 787–830.

## What this rules out

- **`env(safe-area-inset-bottom)` is 0 in both browsers**, with and without
  `viewport-fit=cover`. It cannot drive a fix, and the original code's reliance
  on it is why there is no gap to begin with.
- **No viewport unit detects the toolbar in SFSafariViewController**: `vh`,
  `svh`, `lvh` and `dvh` are all exactly `innerHeight`. (In Safari `lvh` is
  taller than `innerHeight`, which distinguishes the two — but only in Safari,
  the browser that does not need fixing.)
- **Nothing paints below the layout viewport.** A probe rendering three
  candidates at once — a pseudo-element on the bar, an independent
  `position: fixed` filler, and the bar's own padding — showed **only the bar**.
  The other two were absent from the screenshot entirely (verified by sampling
  pixels, not by eye). Anything positioned into that strip is clipped.
- **A canvas background does paint there** (a background on `html`/`body`
  propagates to the canvas, which covers the screen rather than the viewport),
  but it only colours the strip; it cannot put the bar's controls in it. That
  was tried and reverted too, since it changes nothing visible in light mode —
  `--bg` and `--surface` are both white — and only tints the strip in dark mode.

## Why it has been hard to test

Every round has been: change CSS, push, deploy, open Telegram, screenshot, send.
That is slow, and the GitHub Pages `cache-control: max-age=600` on static assets
means a stale stylesheet can be served for up to ten minutes after a push — some
early rounds were probably testing old CSS. Deploy-time cache busting is now in
place (`scripts/stamp_assets.sh`), which removes that particular confusion.

## The plan: a test harness app

Build a minimal iOS app whose only job is to open a URL in an
`SFSafariViewController`. That makes the target environment reachable in
seconds, on a simulator, without Telegram in the loop.

- A single view with a text field for the URL and a "Open" button, or just a
  hardcoded URL and a button.
- Present `SFSafariViewController(url:)`; no configuration is needed, the
  default is what Telegram uses.
- Point it at a **local dev server** (`python3 -m http.server`, reachable from
  the simulator at `http://localhost:8000`) so there is no deploy step and no
  cache between an edit and a test.
- Optional but valuable: a variant that also presents `WKWebView` and
  `ASWebAuthenticationSession` side by side, since other apps embed links
  differently and the fix should not be tuned to one of them.

With that, candidate fixes can be tried in a tight loop, and a page with
on-screen toggles can compare several approaches in a single run rather than
one per deploy.

## Ideas not yet tried

- Reading `window.visualViewport.offsetTop` / `.height` and `pageTop` and
  setting a CSS variable from JS on `visualViewport`'s `resize` and `scroll`
  events. `visualViewport.height` was 651 (equal to `innerHeight`) in the
  measurement above, so this may go nowhere, but `offsetTop`/`pageTop` were not
  captured and might expose the offset the renderer is clearly applying — the
  bar is drawn at 742 while JS reports 651.
- Not pinning the bar to the bottom on phones at all: dock it under the masthead
  at the top, where there is no chrome to fight. Sidesteps the problem entirely
  at the cost of thumb reach.
- Accepting it. Safari is correct, and the only cost in SFSafariViewController
  is that the toolbar sits close to the transport controls. Nothing is
  unreadable or unusable.

## Reproducing

1. `python3 -m http.server 8000` in the repo root.
2. Open the site in the harness app (once it exists), or push and open the daily
   Telegram ping.
3. The probe pages used for the measurements above are in the history — see
   commits `a8dddb5` and `da55e05` on the `backup-before-squash` branch, which
   also holds every reverted attempt if any of them is worth revisiting.
