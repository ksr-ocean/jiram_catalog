"""Regenerate the screenshots in ``docs/gui_guide/`` from the live v2 app.

Starts ``jiram-catalog gui`` (the React + deck.gl front end; this script
never passes ``--legacy``) in a subprocess on a free loopback port,
drives it with headless Chromium (Playwright), saves PNGs into this
directory, and stops the server again. Run it as::

    uv run --with playwright python docs/gui_guide/take_screenshots.py

Playwright and its Chromium build must already be installed (they are,
under ``~/.cache/ms-playwright``, wherever this was developed). The
default mirror (``JIRAM_MIRROR``, or the built-in default) supplies the
data; nothing here writes anywhere except this directory and the
mirror's own ``gui_cache/`` (one selection is saved to demonstrate the
tray, then deleted again at the end so reruns do not accumulate them).

Unlike GUI v1, every state this script waits on is read from the app's
own hidden ``#debug-state`` element (``{n_points, n_filtered,
selection_n, view, stack_id, level, t, cmap, stats_visible}``,
`docs/gui_v2_notes.md`) rather than guessed from a fixed sleep or
scraped from on-screen text -- v2 was built with that element
specifically so a test (or this script) has a number to poll instead of
a screenshot to eyeball; ``level`` and ``stats_visible`` are what this
script polls for the Poles mode selector and the Strips statistics
toggle. Every interaction below is one Playwright can drive headlessly
and is also exercised by `frontend/e2e/*.spec.ts` against a live
backend; none had to be described in words instead of captured, unlike
GUI v1's second-strip click.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
#: Width is what the deliverable requires (<= 1400 px); the height is taller
#: than a browser window needs to be because the Catalog tab's `.view` is a
#: column flexbox with no `min-height: 0` on the map -- at a short viewport
#: (900 px, tried first) the coverage charts and table below the map starve
#: it, and the deck.gl canvas settles at well under its intended 420 px
#: (confirmed empirically: full height only from about 1280 px of viewport
#: height up). No code here is in scope to fix that, so the honest way to
#: get a screenshot that actually shows the map is a taller viewport, not a
#: smaller one; a normal maximized browser window clears this easily.
VIEWPORT = {"width": 1400, "height": 1300}
MAX_BYTES = 300_000
MAX_WIDTH = 1400
DEMO_SELECTION_NAME = "gui guide demo selection"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url: str, timeout: float = 180.0) -> None:
    """Poll ``GET /`` until it answers 200 (the built front end is served)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"server at {url} did not come up within {timeout:.0f}s")


def compress(path: Path) -> None:
    """Downscale to <= 1400 px wide and quantize until the file fits."""
    image = Image.open(path).convert("RGB")
    if image.width > MAX_WIDTH:
        ratio = MAX_WIDTH / image.width
        image = image.resize((MAX_WIDTH, round(image.height * ratio)), Image.LANCZOS)
    image.save(path, format="PNG", optimize=True)
    if path.stat().st_size <= MAX_BYTES:
        return
    for colors in (256, 192, 128, 96, 64):
        quantized = image.convert("P", palette=Image.ADAPTIVE, colors=colors)
        quantized.save(path, format="PNG", optimize=True)
        if path.stat().st_size <= MAX_BYTES:
            return
    print(f"warning: {path.name} still {path.stat().st_size} bytes after quantizing")


def save(page: Page, name: str) -> None:
    path = OUT_DIR / name
    page.screenshot(path=str(path), full_page=False)
    compress(path)
    print(f"wrote {path} ({path.stat().st_size} bytes)")


# ---------------------------------------------------------------------------
# The debug-state contract (see frontend/e2e/helpers.ts, the TypeScript twin
# of these two functions).
# ---------------------------------------------------------------------------
def debug_state(page: Page) -> dict[str, Any]:
    text = page.locator("#debug-state").text_content()
    return json.loads(text or "{}")


def wait_for_state(
    page: Page,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 90.0,
    interval_ms: int = 400,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    state = debug_state(page)
    while time.time() < deadline:
        state = debug_state(page)
        if predicate(state):
            return state
        page.wait_for_timeout(interval_ms)
    raise RuntimeError(f"debug state never matched; last saw {state!r}")


def open_app(page: Page, url: str) -> None:
    """Load the app and wait for the catalog Arrow table to be parsed."""
    page.goto(url)
    page.wait_for_selector("#debug-state", state="attached", timeout=60_000)
    wait_for_state(page, lambda s: s.get("n_points", 0) > 0, timeout=120.0)


def centre_of(locator: Any) -> dict[str, float]:
    box = locator.bounding_box()
    if box is None:
        raise RuntimeError("element has no box")
    return {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2, "width": box["width"], "height": box["height"]}


def video_ready_state(page: Page) -> int:
    return page.evaluate(
        "() => { const v = document.querySelector('[data-testid=\"stack-movie\"]'); return v ? v.readyState : -1; }"
    )


# ---------------------------------------------------------------------------
# The screenshot groups. Each function owns one browser page (one fresh
# session with an empty localStorage) so that the interactions building
# toward one screenshot cannot be thrown off by state left behind by another
# group -- the same reasoning GUI v1's script used, even though v2's state
# management does not actually drift the way v1's did.
# ---------------------------------------------------------------------------
def shot_first_loads(browser, url: str) -> None:
    # 1. Catalog tab at first load (the default tab).
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    page.wait_for_timeout(1500)
    save(page, "01_catalog_overview.png")
    page.close()

    # 2. Poles tab at first load: no stack chosen yet, so this also shows
    # the "no stack is open" placeholder and the stack chooser.
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    page.locator('[data-testid="tab-poles"]').click()
    page.locator('[data-testid="stack-select"] option').nth(1).wait_for(state="attached", timeout=60_000)
    page.wait_for_timeout(800)
    save(page, "02_poles_overview.png")
    page.close()

    # 3. Strips tab at first load: the library table and centres map, no
    # strip opened yet (v2 does not auto-select one the way v1 did).
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    page.locator('[data-testid="tab-strips"]').click()
    page.locator('[data-testid="strips-table"] tbody tr').first.wait_for(state="attached", timeout=60_000)
    page.wait_for_timeout(800)
    save(page, "03_strips_overview.png")
    page.close()


def shot_catalog_workflow(browser, url: str) -> None:
    """Filter -> polar view -> hover -> box-select -> save to the tray.

    One session, in this order, because it is also the real workflow the
    guide describes: narrow the map, look at one hemisphere, read a point,
    select a cluster, and keep it.
    """
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    total = debug_state(page)["n_points"]

    # 4. Filtered: band M, pixel <= 20 km, N polar.
    page.locator('[data-testid="filter-half"]').select_option("M")
    page.wait_for_timeout(300)
    page.locator('[data-testid="filter-pixel-max"]').fill("20")
    page.wait_for_timeout(300)
    page.locator('[data-testid="filter-lat-band"]').select_option("N polar")
    wait_for_state(page, lambda s: 0 < s["n_filtered"] < total, timeout=30.0)
    page.wait_for_timeout(500)
    save(page, "04_catalog_filtered.png")

    # 5. Polar-view toggle, fit to the filtered set.
    page.locator('[data-testid="view-mode-N"]').click()
    wait_for_state(page, lambda s: s["view"] == "N")
    page.locator('[data-testid="zoom-to-data"]').click()
    page.wait_for_timeout(1500)
    save(page, "05_catalog_polar_view.png")

    # 6. Hover tooltip: switch to the pan tool (GPU picking, as
    # frontend/e2e/catalog.spec.ts does) and probe a few points near the
    # centre of the now-dense polar cluster.
    page.locator('[data-testid="tool-pan"]').click()
    page.wait_for_timeout(300)
    map_box = centre_of(page.locator('[data-testid="catalog-map"]'))
    tooltip = page.locator('[data-testid="catalog-map"] .tooltip')
    shown = False
    for dx, dy in [(0, 0), (10, 0), (-10, 8), (0, -14), (22, 18), (-28, -12), (36, 4), (0, 36)]:
        page.mouse.move(map_box["x"] + dx, map_box["y"] + dy)
        page.wait_for_timeout(250)
        if tooltip.is_visible():
            shown = True
            break
    if not shown:
        print("warning: no tooltip appeared while probing the polar cluster")
    save(page, "06_catalog_hover_tooltip.png")

    # 7. Box selection: back to the box tool, drag over the same cluster.
    page.locator('[data-testid="tool-box"]').click()
    page.wait_for_timeout(200)
    before_n = debug_state(page)["selection_n"]
    page.mouse.move(map_box["x"] - map_box["width"] * 0.28, map_box["y"] - map_box["height"] * 0.28)
    page.mouse.down()
    page.mouse.move(map_box["x"], map_box["y"], steps=8)
    page.mouse.move(map_box["x"] + map_box["width"] * 0.28, map_box["y"] + map_box["height"] * 0.28, steps=8)
    page.mouse.up()
    wait_for_state(page, lambda s: s["selection_n"] > before_n, timeout=15.0)
    page.wait_for_timeout(400)
    save(page, "07_catalog_box_selection.png")

    # 8. The selection tray with that selection named and saved.
    page.locator('[data-testid="selection-name"]').fill(DEMO_SELECTION_NAME)
    page.locator('[data-testid="save-selection"]').click()
    page.locator('[data-testid="saved-selections"]', has_text=DEMO_SELECTION_NAME).wait_for(
        state="visible", timeout=20_000
    )
    page.wait_for_timeout(400)
    save(page, "08_selection_tray_saved.png")
    page.close()


def shot_poles(browser, url: str, stack_id: str) -> None:
    """Open the paper region's stack and its two named siblings.

    ``stack_id`` is always the sequence-level ("Region snapshots") stack
    (see ``pick_movie_stack``), so this one session can show all three
    mode-selector states without reopening the tab: snapshots first (12),
    the stepped/magma/cumulative shots next (09, 10, 13), then back to
    snapshots for the movie (11), which is rendered only for that sibling.
    """
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    page.locator('[data-testid="tab-poles"]').click()

    select = page.locator('[data-testid="stack-select"]')
    select.locator(f'option[value="{stack_id}"]').wait_for(state="attached", timeout=60_000)
    select.select_option(stack_id)
    wait_for_state(page, lambda s: s.get("stack_id") == stack_id, timeout=60.0)
    page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first.wait_for(
        state="attached", timeout=90_000
    )

    # 12. The mode selector with "Region snapshots" active -- the sibling
    # this script always opens first, per pick_movie_stack.
    state = wait_for_state(page, lambda s: s.get("level") is not None, timeout=30.0)
    if state.get("level") != "sequence":
        print(f"warning: expected to open a 'sequence' stack, debug-state says {state.get('level')!r}")
    page.locator('[data-testid="mode-bar"]').scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    save(page, "12_poles_mode_snapshots.png")

    # 9. Step to a non-zero time with the graticule on (it defaults on).
    slider = page.locator('[data-testid="time-slider"]')
    slider.fill("10")
    wait_for_state(page, lambda s: s.get("t") == 10, timeout=30.0)
    page.wait_for_timeout(1200)
    save(page, "09_poles_stepped_graticule.png")

    # 10. Colour map changed to magma -- a LUT redraw, no refetch.
    page.locator('[data-testid="cmap-select"]').select_option("magma")
    wait_for_state(page, lambda s: s.get("cmap") == "magma", timeout=15.0)
    page.wait_for_function(
        "document.querySelector('[data-testid=\"frame-canvas\"]')?.dataset.cmap === 'magma'",
        timeout=15_000,
    )
    page.wait_for_timeout(500)
    save(page, "10_poles_colormap_magma.png")

    # 13. Switch to "Accumulating sweep" (the cumulative sibling this
    # mirror carries for the paper region) and step mid-sweep so the
    # "sweep k, frame i of n" readout has something to say.
    cumulative_option = select.locator('option[value$="_cumulative"]')
    if cumulative_option.count() > 0:
        page.locator('[data-testid="mode-cumulative"]').click()
        wait_for_state(page, lambda s: s.get("level") == "cumulative", timeout=60.0)
        page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first.wait_for(
            state="attached", timeout=90_000
        )
        slider.fill("5")
        wait_for_state(page, lambda s: s.get("t") == 5, timeout=30.0)
        page.wait_for_timeout(800)
        page.locator('[data-testid="time-readout"]').scroll_into_view_if_needed()
        save(page, "13_poles_mode_cumulative_sweep.png")

        # Back to "Region snapshots" -- the sibling with the rendered movie.
        page.locator('[data-testid="mode-sequence"]').click()
        wait_for_state(page, lambda s: s.get("stack_id") == stack_id and s.get("level") == "sequence", timeout=60.0)
        page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first.wait_for(
            state="attached", timeout=90_000
        )
    else:
        print("warning: no cumulative sibling for this stack; skipping screenshot 13")

    # 11. The movie player: this stack has a rendered movie, so the native
    # <video> element is present without starting any render job. Play it
    # (rather than just showing it paused at 0:00, indistinguishable from
    # screenshot 10) so the controls' current-time and pause icon prove it
    # actually plays.
    video = page.locator('[data-testid="stack-movie"]')
    video.scroll_into_view_if_needed()
    video.wait_for(state="visible", timeout=30_000)
    deadline = time.time() + 60.0
    while time.time() < deadline and video_ready_state(page) < 1:
        page.wait_for_timeout(500)
    page.evaluate("document.querySelector('[data-testid=\"stack-movie\"]').play()")
    page.wait_for_timeout(1500)
    save(page, "11_poles_movie.png")
    page.close()


def shot_strips(browser, url: str) -> None:
    page = browser.new_page(viewport=VIEWPORT)
    open_app(page, url)
    page.locator('[data-testid="tab-strips"]').click()

    rows = page.locator('[data-testid="strips-table"] tbody tr')
    rows.first.wait_for(state="attached", timeout=60_000)
    rows.first.click()
    page.locator('[data-testid="current-strip"]').wait_for(state="visible", timeout=90_000)
    page.locator('[data-testid="strip-image"] canvas').first.wait_for(state="visible", timeout=120_000)

    # 14. The statistics start hidden behind "Show statistics" (a fresh
    # session's localStorage has no stats_visible yet, so it defaults to
    # false); the image viewer has the panel's space in the meantime.
    wait_for_state(page, lambda s: s.get("stats_visible") is False, timeout=15.0)
    page.wait_for_timeout(800)
    save(page, "14_strips_stats_hidden.png")

    # 15. Toggling "Show statistics" fetches nothing new -- the stats were
    # already requested when the strip opened -- it just draws the three
    # Plotly figures and gives the image back its normal, smaller height.
    page.locator('[data-testid="toggle-stats"]').click()
    wait_for_state(page, lambda s: s.get("stats_visible") is True, timeout=15.0)
    page.locator('[data-testid="plot-isotropic"] .js-plotly-plot').wait_for(state="visible", timeout=180_000)
    page.locator('[data-testid="plot-structure"] .js-plotly-plot').wait_for(state="visible", timeout=30_000)
    page.locator('[data-testid="current-strip"]').scroll_into_view_if_needed()
    page.wait_for_timeout(1000)
    save(page, "15_strips_stats_shown.png")
    page.close()


def cleanup_demo_selection(base_url: str) -> None:
    """Delete every saved selection this script created, so reruns are idempotent."""
    try:
        with urllib.request.urlopen(f"{base_url}api/selections", timeout=20) as response:
            records = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"warning: could not list selections for cleanup: {exc}")
        return
    for record in records:
        if str(record.get("name", "")).startswith(DEMO_SELECTION_NAME):
            request = urllib.request.Request(f"{base_url}api/selections/{record['id']}", method="DELETE")
            try:
                urllib.request.urlopen(request, timeout=20)
                print(f"cleaned up saved selection {record['id']!r}")
            except (OSError, urllib.error.URLError) as exc:
                print(f"warning: could not delete selection {record['id']!r}: {exc}")


def pick_movie_stack(base_url: str) -> str:
    """The paper region's sequence stack, or any stack with a rendered movie.

    The paper region (`north_pole_paper/M_orbits4_*`) is the one this
    repository's own gates build, so preferring it -- the same choice
    `frontend/e2e/poles.spec.ts`'s `chooseStack` makes -- gives a
    deterministic session where the cumulative and frame siblings are also
    known to exist, which screenshots 12 and 13 need.
    """
    with urllib.request.urlopen(f"{base_url}api/stacks", timeout=30) as response:
        stacks = json.load(response)
    if not stacks:
        raise RuntimeError("no stacks under <mirror>/regions/ -- nothing for the Poles tab to show")
    by_id = {str(s["id"]): s for s in stacks}
    preferred = "north_pole_paper/M_orbits4_sequence"
    if preferred in by_id and by_id[preferred].get("has_movie"):
        return preferred
    with_movie = [s for s in stacks if s.get("has_movie")]
    chosen = with_movie[0] if with_movie else stacks[0]
    if not with_movie:
        print("warning: no stack on this mirror has a rendered movie; screenshot 11 will show the placeholder text")
    return str(chosen["id"])


def main() -> int:
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    server = subprocess.Popen(
        ["uv", "run", "jiram-catalog", "gui", "--port", str(port), "--no-browser"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_ready(url)
        movie_stack = pick_movie_stack(url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            shot_first_loads(browser, url)
            shot_catalog_workflow(browser, url)
            shot_poles(browser, url, movie_stack)
            shot_strips(browser, url)
            browser.close()
        cleanup_demo_selection(url)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        if server.stdout is not None:
            tail = server.stdout.read()
            if tail:
                print("--- server output (tail) ---")
                print(tail[-4000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
