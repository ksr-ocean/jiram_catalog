"""Reproduce the bounded PDS calibrated-collection inspection (about 8 MB).

Run with the project interpreter. Optional tifffile adds independent GeoTIFF
tag verification; PDS4 array labels suffice for the image and value checks.
Source samples stay in <mirror>/junocam/calibration_review, never the native
image index. This is a reference-data audit, not an observation importer.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import numpy as np

BASE = "https://pds-atmospheres.nmsu.edu/PDS/data/PDS4/junocam_atm-ml-calib/"
NS = {"p": "http://pds.nasa.gov/pds4/pds/v1"}
FILTERS = ("F275W", "F395N", "F502N", "F631N", "FQ889N")
MOSAIC_OFFSETS = (5760, 51894720, 103783680, 155672640, 207561600)


def fetch(root: Path, relative: str, *, byte_range: tuple[int, int] | None = None,
          name: str | None = None) -> Path:
    target = root / (name or relative.replace("/", "_"))
    if target.exists():
        return target
    headers = {} if byte_range is None else {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"}
    cap = 15_000_000 if byte_range is None else byte_range[1] - byte_range[0] + 1
    with urlopen(Request(BASE + relative, headers=headers), timeout=45) as response:
        if byte_range and response.status != 206:
            raise ValueError("server did not honor bounded range request")
        blob = response.read(cap + 1)
        if len(blob) > cap:
            raise ValueError("source exceeds the bounded review download")
    target.write_bytes(blob)
    return target


def cards(blob: bytes) -> dict:
    result = {}
    for i in range(0, len(blob), 80):
        line = blob[i:i + 80].decode("ascii")
        if line[:8].strip() == "END":
            break
        if line[8:10] == "= ":
            result[line[:8].strip()] = line[10:].split(" / ")[0].strip().strip("'").strip()
    return result


def main() -> None:
    from jiram_catalog.config import mirror_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", type=Path)
    args = parser.parse_args()
    root = mirror_root(args.mirror) / "junocam" / "calibration_review"
    root.mkdir(parents=True, exist_ok=True)
    sources = []
    for rel in ("bundle_junocam_atm-ml-calib.xml", "document/user_guide.pdf",
                "data_calibrated/collection_junocam_atm-ml-calib_data_calibrated.xml",
                "data_calibrated/collection_junocam_atm-ml-calib_data_calibrated_inventory.csv",
                "data_mosaics/PJ13/junocam_calibration_PJ13.xml"):
        file = fetch(root, rel)
        sources.append({"url": BASE + rel, "file": file.name,
                        "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    inventory = root / "data_calibrated_collection_junocam_atm-ml-calib_data_calibrated_inventory.csv"
    rows = list(csv.reader(inventory.read_text().splitlines()))
    counts = Counter()
    for row in rows:
        lid = row[-1].split(":")[-3] if len(row[-1].split(":")) > 2 else ""
        # Count identifiers directly, without assuming CSV product order.
        import re
        found = re.search(r"_pj_(\d+)_", row[-1], re.I)
        if found:
            counts[int(found.group(1))] += 1
    tiles, arrays = [], []
    for zone in ("NPR", "EZ", "SPR"):
        stem = f"data_calibrated/PJ13/{zone}_PJ_13_jc_000000"
        label = fetch(root, stem + ".xml")
        image = fetch(root, stem + ".tif")
        xml = ET.parse(label).getroot()
        block = xml.find(".//p:Array_3D_Image", NS)
        offset = int(block.findtext("p:offset", namespaces=NS))
        shape = [int(axis.findtext("p:elements", namespaces=NS)) for axis in block.findall("p:Axis_Array", NS)]
        array = np.fromfile(image, dtype="<f4", offset=offset).reshape(shape)
        arrays.append(array)
        record = {"name": image.name, "source": BASE + stem + ".tif", "shape": shape,
                  "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                  "start_time": xml.findtext(".//p:start_date_time", namespaces=NS),
                  "stop_time": xml.findtext(".//p:stop_date_time", namespaces=NS),
                  "units": block.findtext("p:Element_Array/p:unit", namespaces=NS),
                  "finite_fraction": float(np.isfinite(array).mean()),
                  "range": [float(np.nanmin(array)), float(np.nanmax(array))]}
        try:
            import tifffile
            with tifffile.TiffFile(image) as tif:
                if not np.array_equal(tif.asarray(), array):
                    raise ValueError("TIFF reader and PDS4 layout disagree")
                record["geotiff"] = tif.geotiff_metadata
                record["nodata_tag"] = str(tif.pages[0].tags.get("GDAL_NODATA"))
        except ImportError:
            record["geotiff"] = "install optional tifffile for independent tag validation"
        tiles.append(record)
    mosaic = "data_mosaics/PJ13/junocam_calibration_PJ13.fits"
    header = fetch(root, mosaic, byte_range=(0, 8639), name="data_mosaics_PJ13_header_sample.bin").read_bytes()
    sampled = []
    for band, offset in zip(FILTERS, MOSAIC_OFFSETS):
        first = offset + 900 * 3601 * 8
        file = fetch(root, mosaic, byte_range=(first, first + 3601 * 8 - 1), name=f"mosaic_{band}_row900.bin")
        array = np.fromfile(file, dtype=">f8")
        sampled.append({"filter": band, "row": 900, "byte_range": [first, first + 3601 * 8 - 1],
                        "finite_fraction": float(np.isfinite(array).mean()),
                        "zero_fraction": float((array == 0).mean()),
                        "positive_range": [float(array[array > 0].min()), float(array.max())]})
    result = {"reviewed_utc": datetime.now(timezone.utc).isoformat(), "base": BASE,
              "inventory_rows": len(rows), "perijove_counts": dict(sorted(counts.items())),
              "sources": sources, "tiles": tiles, "mosaic_primary": cards(header[:2880]),
              "mosaic_first_extension": cards(header[2880:5760]), "mosaic_sample_rows": sampled,
              "limitations": "Three PJ13 tiles and one row per mosaic band; no complete mosaic or native-scene comparison."}
    (root / "inspection.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 5, figsize=(12, 7.8), constrained_layout=True)
    for row, (zone, array) in enumerate(zip(("NPR", "EZ", "SPR"), arrays)):
        for col, band in enumerate(FILTERS):
            axes[row, col].imshow(array[..., col], cmap="gray", vmin=0, vmax=1, extent=(-8000, 8000, -8000, 8000))
            axes[row, col].set_xticks([])
            axes[row, col].set_yticks([])
            if row == 0:
                axes[row, col].set_title(band)
            if col == 0:
                axes[row, col].set_ylabel(zone + " · PJ13")
    fig.suptitle("PDS ML-derived JunoCam samples · generated HST-equivalent channels\n"
                 "256 × 256 pixels · 62.5 km/pixel · each tile spans 16,000 km · common display range I/F 0–1", fontsize=12)
    fig.savefig(root / "sample_channels.png", dpi=150)
    plt.close(fig)
    print(json.dumps({"output": str(root), "inventory_rows": len(rows), "perijove_counts": dict(sorted(counts.items()))}))


if __name__ == "__main__":
    main()
