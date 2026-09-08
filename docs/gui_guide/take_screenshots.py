"""Capture the delivered five-view GUI at 1440 x 900 from real local data.

Run from the repository with::

    uv run --with playwright python docs/gui_guide/take_screenshots.py

Install Playwright's Chromium separately if it is absent. The committed
production bundle is served on a free loopback port. JIRAM_MIRROR (or the
configured default) supplies existing stacks and strips. No stack, export,
movie or saved selection is created; normal GUI read caches are permitted.
The selection screenshot uses only an ephemeral browser-context selection.

Every capture waits on application state and rendered data. Frame captures
also require nontransparent image pixels. PNG optimization is lossless: no
resizing, colour conversion or palette quantization. Historical numbered
screenshots are left untouched. The guide reuses existing JunoCam readiness evidence; this helper captures
only the six required views and does not repeat that calculation.

Server logs and a JSON capture record are retained under the configured
Lustre TMPDIR. The browser and the complete owned server process group are
closed on success or failure. This helper does not exercise save/build/
export workflows; screenshots must not be described as evidence for them.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator
import urllib.request

from PIL import Image
import numpy as np
import pyarrow as pa
from playwright.sync_api import Browser, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
VIEWPORT = {"width": 1440, "height": 900}
WAIT_MS = 180_000
expect.set_options(timeout=WAIT_MS)
REQUIRED = (
    "current_explore.png", "current_selection.png", "current_time_series.png",
    "current_image_library.png", "current_compare.png", "current_coverage.png",
)


def scratch_directory() -> Path:
    raw = os.environ.get("TMPDIR")
    if not raw:
        raise RuntimeError("TMPDIR must point to the configured Lustre runtime directory")
    scratch = Path(raw).resolve()
    if scratch == Path("/tmp") or Path("/tmp") in scratch.parents:
        raise RuntimeError("System /tmp is not permitted for this capture")
    scratch.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="gui-guide-capture-", dir=scratch))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read()


def get_json(url: str) -> Any:
    return json.loads(fetch(url))


@contextmanager
def production_server(runtime: Path) -> Iterator[str]:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        env[name] = str(runtime)
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    log_path = runtime / "server.log"
    # A regular file continuously drains stdout/stderr; a PIPE can fill and
    # block the server before the browser gets its first response.
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "jiram_catalog.gui_cmd", "--address", "127.0.0.1",
             "--port", str(port), "--no-browser"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print(f"Server PID {server.pid}: {url}; log {log_path}", flush=True)
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"Server exited {server.returncode}; see {log_path}")
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.25)  # Polling interval, never screenshot readiness.
            else:
                raise RuntimeError(f"Server startup timed out; see {log_path}")
            assert b"/assets/" in fetch(url + "/"), "Committed production bundle is missing"
            yield url
        finally:
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait(timeout=10)


def debug_state(page: Page) -> dict[str, Any]:
    return json.loads(page.locator("#debug-state").text_content() or "{}")


def wait_for_state(page: Page, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_MS / 1000
    while time.monotonic() < deadline:
        state = debug_state(page)
        if predicate(state):
            return state
        page.wait_for_timeout(200)
    raise RuntimeError(f"Application state did not become ready: {debug_state(page)!r}")


def next_paint(page: Page) -> None:
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


@contextmanager
def app_page(browser: Browser, url: str) -> Iterator[Page]:
    # A fresh context prevents persistent settings or a prior selection from
    # silently determining another screenshot's scientific source/settings.
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    page = context.new_page()
    page.set_default_timeout(WAIT_MS)
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#debug-state").wait_for(state="attached")
        wait_for_state(page, lambda s: s.get("n_points", 0) > 0)
        yield page
    finally:
        context.close()


def loaded_pixels(page: Page, test_id: str) -> dict[str, int]:
    selector = f'[data-testid="{test_id}"] [data-testid="frame-canvas"][data-loaded="true"]'
    check = """selector => {
      const c = document.querySelector(selector);
      if (!c || c.width === 0 || c.height === 0) return null;
      const ctx = c.getContext('2d', {willReadFrequently:true});
      if (!ctx) return null;
      const a = ctx.getImageData(0,0,c.width,c.height).data;
      let visible = 0;
      for (let p=3; p<a.length; p+=4) if (a[p] > 0) visible++;
      return visible > 0 ? {width:c.width,height:c.height,visible} : null;
    }"""
    page.wait_for_function(check, arg=selector, timeout=WAIT_MS)
    next_paint(page)
    return page.evaluate(check, selector)


def loaded_density(page: Page) -> dict[str, int]:
    """Check data state, the density legend, and rendered blue coverage cells."""
    wait_for_state(page, lambda s: s.get("n_filtered", 0) > 0)
    expect(page.get_by_test_id("density-legend")).to_be_visible()
    panel = page.get_by_test_id("catalog-map")
    expect(panel.locator("canvas").first).to_be_visible()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        next_paint(page)
        with Image.open(BytesIO(panel.screenshot())) as image:
            # Blue fill (69,154,216 with variable alpha) differs from the
            # dark graticule. Ignore edge controls and the legend itself.
            rgb = image.convert("RGB")
            crop = rgb.crop((10, 10, rgb.width - 10, max(11, rgb.height - 65)))
            pixels = np.asarray(crop, dtype=np.int16)
            red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
            count = int(np.count_nonzero((blue > 65) & (green - red > 20) & (blue - green > 20)))
        if count > 100:
            return {"coverage_pixels": count}
    raise RuntimeError("Density state loaded but no rendered coverage cells were found")


def save(page: Page, name: str, record: dict[str, Any], **details: Any) -> None:
    next_paint(page)
    path = OUT_DIR / name
    page.screenshot(path=str(path), full_page=False, animations="disabled")
    with Image.open(path) as image:
        before = (image.mode, image.size, image.tobytes())
        image.save(path, format="PNG", optimize=True)
    with Image.open(path) as image:
        assert (image.mode, image.size, image.tobytes()) == before, "PNG optimization altered pixels"
        assert image.size == (1440, 900)
    record["captures"][name] = dict(bytes=path.stat().st_size, state=debug_state(page), **details)
    print(f"Captured {name}: {details}", flush=True)


def collapse_empty_selection(page: Page) -> None:
    if page.get_by_test_id("selection-tray").is_visible():
        page.get_by_test_id("toggle-selection-tray").click()
        expect(page.get_by_test_id("selection-tray")).to_have_count(0)


def pick_sources(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    stacks = get_json(url + "/api/stacks")
    jiram = [s for s in stacks if s.get("instrument", "JIRAM") == "JIRAM"]
    if not jiram:
        raise RuntimeError("A pre-existing JIRAM stack is required")
    stack = next((s for s in jiram if s["id"] == "north_pole_paper/M_orbits4_sequence"), None)
    stack = stack or min(jiram, key=lambda s: (s["level"] != "sequence", s["size_bytes"]))
    rows = pa.ipc.open_stream(fetch(url + "/api/strips.arrow")).read_all().to_pylist()
    candidates = [r for r in rows if r.get("instrument", "JIRAM") == "JIRAM"
                  and r.get("band") == "M" and (r.get("valid_frac") or 0) > .2
                  and min(r["rows"], r["cols"]) >= 128]
    if not candidates:
        raise RuntimeError("An existing usable JIRAM M-band strip is required")
    strip = min(candidates, key=lambda r: r["rows"] * r["cols"])
    return stack, strip


def capture(browser: Browser, url: str, record: dict[str, Any]) -> None:
    stack, strip = pick_sources(url)
    record["sources"] = {"jiram_stack": stack, "jiram_strip": strip}
    with app_page(browser, url) as page:
        collapse_empty_selection(page)
        save(page, "current_explore.png", record, **loaded_density(page))
        page.get_by_test_id("filter-instrument").select_option("JunoCam")
        wait_for_state(page, lambda s: s.get("instrument_filter") == "JunoCam" and s.get("n_filtered", 0) > 0)
        # One page of eligible observations, browser memory only.
        page.get_by_test_id("add-page-to-selection").click()
        wait_for_state(page, lambda s: s.get("selection_n", 0) > 0)
        if not page.get_by_test_id("selection-tray").is_visible():
            page.get_by_test_id("toggle-selection-tray").click()
        expect(page.get_by_test_id("selection-count")).not_to_have_text("0")
        next_paint(page)
        ids = page.get_by_test_id("catalog-table").locator("tbody tr").evaluate_all(
            "rows => rows.map(row => row.querySelector('input[type=checkbox]')?.getAttribute('aria-label')?.replace(/^select /, '')).filter(Boolean)"
        )
        assert len(ids) == debug_state(page)["selection_n"], "Selected source IDs must be recorded"
        save(page, "current_selection.png", record, selected_visible_ids=ids, **loaded_density(page))
    with app_page(browser, url) as page:
        collapse_empty_selection(page)
        page.get_by_test_id("tab-poles").click()
        page.get_by_test_id("stack-select").select_option(stack["id"])
        wait_for_state(page, lambda s: s.get("stack_id") == stack["id"] and s.get("level") is not None)
        pixels = loaded_pixels(page, "poles-image")
        expect(page.get_by_test_id("frame-meta")).to_contain_text("km")
        save(page, "current_time_series.png", record, source_id=stack["id"], t=0, pixels=pixels)
    with app_page(browser, url) as page:
        collapse_empty_selection(page)
        page.get_by_test_id("tab-strips").click()
        page.get_by_test_id("strip-instrument").select_option("JIRAM")
        page.get_by_test_id("strip-band-filter").select_option("M")
        page.get_by_title(strip["strip_id"], exact=True).click()
        expect(page.get_by_test_id("current-strip")).to_have_text(strip["strip_id"])
        pixels = loaded_pixels(page, "strip-image")
        assert not debug_state(page)["stats_visible"], "Statistics should remain unrequested"
        save(page, "current_image_library.png", record, source_id=strip["strip_id"], band="M", pixels=pixels)
        # Enter Compare with an already loaded strip so its default source is
        # the explicit same-image control, avoiding an unrelated stack read.
        page.get_by_test_id("tab-compare").click()
        for side in ("Left", "Right"):
            expect(page.get_by_label(f"{side} source", exact=True)).to_have_value(strip["strip_id"])
            page.get_by_label(f"{side} band", exact=True).select_option("M")
        expect(page.get_by_test_id("compare-diagnostics")).to_contain_text("Compatible comparison grid")
        expect(page.get_by_test_id("compare-diagnostics")).to_contain_text("measured")
        pixels = [loaded_pixels(page, f"compare-image-{i}") for i in (0, 1)]
        expect(page.get_by_test_id("compare-view").get_by_role("status")).to_have_count(0)
        save(page, "current_compare.png", record, source_ids=[strip["strip_id"]] * 2,
             interpretation="identical-source registration control; not atmospheric motion", pixels=pixels)
    with app_page(browser, url) as page:
        collapse_empty_selection(page)
        page.get_by_test_id("tab-coverage").click()
        expect(page.get_by_test_id("coverage-table").locator("tbody tr").first).to_be_visible()
        expect(page.get_by_test_id("coverage-view").get_by_role("status")).to_have_count(0)
        expect(page.get_by_test_id("coverage-view")).to_contain_text("failure-exclusion-v1")
        save(page, "current_coverage.png", record,
             displayed_stage_rows=page.get_by_test_id("coverage-table").locator("tbody tr").count())
    record["omitted"]["current_junocam_readiness.png"] = "Guide reuses existing verified JunoCam evidence"


def main() -> int:
    started = time.monotonic()
    runtime = scratch_directory()
    record: dict[str, Any] = {
        "captured_utc": datetime.now(timezone.utc).isoformat(), "viewport": VIEWPORT,
        "software_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "captures": {}, "omitted": {},
    }
    print(f"Runtime and capture record: {runtime}", flush=True)
    try:
        with production_server(runtime) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                capture(browser, url, record)
            finally:
                browser.close()
        assert all(name in record["captures"] for name in REQUIRED)
        return 0
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["elapsed_s"] = round(time.monotonic() - started, 3)
        (runtime / "capture.json").write_text(json.dumps(record, indent=2, default=str) + "\n")


if __name__ == "__main__":
    sys.exit(main())
