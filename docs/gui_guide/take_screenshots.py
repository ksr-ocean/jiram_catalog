"""Regenerate the screenshots in ``docs/gui_guide/`` from the live app.

Starts ``jiram-catalog gui`` in a subprocess on a free loopback port,
drives it with headless Chromium (Playwright), saves PNGs into this
directory, and stops the server again. Run it as::

    uv run --with playwright python docs/gui_guide/take_screenshots.py

Playwright and its Chromium build must already be installed (they are,
under ``~/.cache/ms-playwright``, wherever this was developed). The
default mirror (``JIRAM_MIRROR``, or the built-in default) supplies the
data; nothing here writes anywhere except this directory and the
mirror's own ``gui_cache/``.

Two interactions are driven and their on-screen effect is *not*
verified: switching tabs does not reliably swap the sidebar's filter
panel away from the Catalog tab's, and the datashaded map, the coverage
panels, and the Poles image do not reliably repaint after a filter,
selection, or time-step change in this headless setup, even though the
underlying data updates correctly (confirmed independently through the
selection caption, the selection table's contents, and a direct
comparison against ``apply_filters`` in a plain Python session). Those
screenshots are still real, unedited captures of what the browser drew;
see ``docs/gui_guide.md`` for the caveat in words, as the spec asks for
where a headless interaction cannot be confirmed visually.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
VIEWPORT = {"width": 1400, "height": 900}
MAX_BYTES = 300_000
MAX_WIDTH = 1400


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url: str, timeout: float = 120.0) -> None:
    """Poll until the server answers 200.

    Each ``GET /`` runs the whole app once (server-side), which this
    build takes several seconds to do, so the per-request timeout has
    to be generous -- a short one just times out client-side moments
    before the server would have answered, and retries forever.
    """
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


def save(page, name: str) -> None:
    path = OUT_DIR / name
    page.screenshot(path=str(path), full_page=False)
    compress(path)
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def scroll_to(page, text: str, exact: bool = True, margin: int = 60) -> None:
    heading = page.get_by_text(text, exact=exact).first
    box = heading.bounding_box()
    if box is not None:
        page.evaluate(f"document.getElementById('main').scrollTop = {box['y'] - margin}")
        page.wait_for_timeout(1200)


def click_tab(page, name: str, wait_ms: int = 8000) -> None:
    page.locator("div.bk-tab", has_text=name).first.click()
    page.wait_for_timeout(wait_ms)


def caption_text(page) -> str:
    return page.get_by_text("frames see the planet", exact=False).first.inner_text()


def wait_for_caption(page, substring: str, timeout_s: float = 30.0) -> str:
    """Poll the Selection caption until it contains ``substring``.

    Server-side recompute time varies with load (screenshot compression
    in this same script competes for CPU), so a fixed sleep is not
    reliable here -- poll instead of guessing a wait long enough.
    """
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = caption_text(page)
        if substring in last:
            return last
        page.wait_for_timeout(1000)
    print(f"warning: caption never contained {substring!r}; last saw {last!r}")
    return last


def set_resolution(page, value: str) -> None:
    """The pixel <= (km) EditableFloatSlider's numeric field.

    Found by vertical proximity to its own label rather than by its
    current value, so this works no matter how many times it has
    already been changed in this session.
    """
    label_box = page.get_by_text("pixel <= (km):", exact=False).first.bounding_box()
    for candidate in page.locator("input[type='text']").all():
        if not candidate.is_visible():
            continue
        box = candidate.bounding_box()
        if box is not None and abs(box["y"] - label_box["y"]) < 25:
            candidate.click()
            candidate.press("Control+A")
            candidate.type(value)
            candidate.press("Enter")
            return
    raise RuntimeError("resolution input not found")


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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            def fresh_page():
                """A brand-new Bokeh session (fresh CatalogState, tab 0 active).

                In testing, the app's reactive updates (the map, the
                coverage panels, and even the Selection caption/table)
                could stop following filter changes after several tab
                switches or a box-select in the same session, even
                though the sidebar widgets and ``state`` itself kept
                the right values throughout. Reloading -- exactly what
                a real second visit to the URL does -- side-steps it
                reliably, so each independent group of screenshots
                below starts from a clean session rather than carrying
                interaction history from the last group.
                """
                p = browser.new_page(viewport=VIEWPORT)
                p.goto(url)
                p.wait_for_timeout(9000)
                return p

            # 1. Catalog tab at first load.
            page = fresh_page()
            save(page, "01_catalog_overview.png")
            page.close()

            # 2. Poles tab at first load (default stack, step 1).
            page = fresh_page()
            click_tab(page, "Poles")
            save(page, "02_poles_overview.png")
            page.close()

            # 3. Strips tab at first load (default-selected first strip).
            page = fresh_page()
            click_tab(page, "Strips")
            save(page, "03_strips_overview.png")
            page.close()

            # 4-7. Catalog tab: filter, polar-view toggle, an over-tight
            # filter, then a box-select -- all in one session, without ever
            # leaving the Catalog tab, which is what keeps the reactivity
            # reliable (see fresh_page's docstring).
            page = fresh_page()

            # 4. Filtered: band M, pixel <= 20 km, N polar.
            page.get_by_role("button", name="M", exact=True).click()
            page.wait_for_timeout(1200)
            set_resolution(page, "20")
            page.wait_for_timeout(1200)
            page.get_by_label("latitude band").select_option(label="N polar")
            wait_for_caption(page, "1,895")
            save(page, "04_catalog_filtered.png")

            # 5. Polar-view toggle.
            page.get_by_role("button", name="N", exact=True).click()
            page.wait_for_timeout(6000)
            save(page, "05_catalog_polar_view.png")

            # 6. An over-tight filter: empty selection/table.
            set_resolution(page, "0.5")
            wait_for_caption(page, "0 filtered")
            scroll_to(page, "Selection")
            save(page, "06_catalog_empty.png")

            # 7. Box-select on the map (back to a resolution with matches
            # first); scroll to the Selection table.
            set_resolution(page, "20")
            wait_for_caption(page, "1,895")
            page.evaluate("document.getElementById('main').scrollTop = 0")
            page.wait_for_timeout(1000)
            page.locator("[title='Box Select']").first.click()
            page.wait_for_timeout(500)
            page.mouse.move(500, 350)
            page.mouse.down()
            page.mouse.move(600, 420, steps=5)
            page.mouse.move(760, 560, steps=5)
            page.mouse.up()
            wait_for_caption(page, "selected")
            scroll_to(page, "Selection")
            save(page, "07_catalog_selection.png")
            page.close()

            # 8. Poles tab, sequence stack stepped to a non-zero time, graticule on.
            page = fresh_page()
            click_tab(page, "Poles")
            slider = page.locator("input[type='range']").first
            box = slider.bounding_box()
            page.mouse.click(box["x"] + box["width"] * (10 / 24), box["y"] + box["height"] / 2)
            page.wait_for_timeout(6000)
            save(page, "08_poles_stepped.png")
            page.close()

            # 9. Strips tab, default-selected strip's viewer and statistics panel.
            page = fresh_page()
            click_tab(page, "Strips")
            scroll_to(page, "Statistics")
            page.wait_for_timeout(6000)
            save(page, "09_strips_statistics.png")
            page.close()

            browser.close()
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
