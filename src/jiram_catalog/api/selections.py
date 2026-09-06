"""Named frame selections, shared by everyone who opens the server.

A selection is the unit of work the rest of the tool consumes: the set of
product ids a stack build is restricted to, the set a note refers to.  It
is stored as one small JSON document under
``<mirror>/gui_cache/selections/`` rather than in a database, because the
mirror is the group's shared object and a JSON file is something a
collaborator can read, mail, or commit without this server running.

The identifier is a slug of the name plus a short hash of the contents,
so two people who both save "north pole PJ4" get two records instead of
one overwriting the other, and a name that is already taken picks up a
numeric suffix rather than failing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import mirror_root
from .catalog import catalog_frame
from .data import gui_cache_dir

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/selections", tags=["selections"])

#: Fields of the summary record the list endpoint returns.
SUMMARY_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "created",
    "n_frames",
    "n_orbits",
    "lat_min",
    "lat_max",
    "note",
)


class SelectionRequest(BaseModel):
    """The body of ``POST /api/selections``."""

    name: str = Field(min_length=1, max_length=200)
    product_ids: list[str]
    halves: list[str] | None = None
    note: str | None = None


def _safe_identifier(identifier: str) -> str:
    """Reject anything that is not a plain record name.

    The identifier reaches the filesystem, and a URL-encoded ``..`` would
    otherwise let a request name a file outside the selections directory.
    """
    text = str(identifier)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", text) or text.startswith("."):
        raise HTTPException(status_code=404, detail=f"unknown selection: {identifier}")
    return text


def selections_dir(mirror: str | Path | None = None) -> Path:
    """``<mirror>/gui_cache/selections``, created."""
    path = gui_cache_dir(mirror) / "selections"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug(name: str) -> str:
    """A filesystem-safe stem: ASCII, lower case, hyphens, never empty."""
    folded = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return cleaned[:60] or "selection"


def _identifier(name: str, product_ids: list[str], taken: set[str]) -> str:
    """``<slug>-<hash>``, with a numeric suffix when that is already taken."""
    digest = hashlib.sha256("\n".join(product_ids).encode()).hexdigest()[:6]
    base = f"{slug(name)}-{digest}"
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise HTTPException(status_code=409, detail="too many selections with that name")


def _latitudes(mirror: str | Path | None, product_ids: list[str], halves: list[str] | None) -> dict[str, Any]:
    """Row count, orbit count and latitude span of a selection's frames.

    The catalog is consulted rather than trusted: a product id the
    catalog does not carry contributes to ``n_frames`` (it is what the
    user selected) but not to the latitude span, which would otherwise be
    a span over nothing.
    """
    table = catalog_frame(mirror)
    wanted = pd.Index(pd.unique(pd.Series(product_ids, dtype=object)))
    rows = table.loc[table["product_id"].astype(object).isin(set(wanted))]
    if halves:
        upper = {str(value).upper() for value in halves}
        rows = rows.loc[rows["half"].astype(str).str.upper().isin(upper)]
    if rows.empty:
        return {"n_orbits": 0, "lat_min": None, "lat_max": None}
    latitudes = pd.to_numeric(rows["bore_lat"], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    finite = latitudes[np.isfinite(latitudes)]
    return {
        "n_orbits": int(pd.to_numeric(rows["orbit"], errors="coerce").dropna().nunique()),
        "lat_min": float(finite.min()) if finite.size else None,
        "lat_max": float(finite.max()) if finite.size else None,
    }


def _read(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOGGER.warning("unreadable selection %s: %s", path, exc)
        return None
    return record if isinstance(record, dict) and "id" in record else None


def load_all(mirror: str | Path | None = None) -> list[dict[str, Any]]:
    """Every stored selection, newest first."""
    records = [record for path in sorted(selections_dir(mirror).glob("*.json")) if (record := _read(path))]
    return sorted(records, key=lambda record: str(record.get("created", "")), reverse=True)


def load_one(mirror: str | Path | None, identifier: str) -> dict[str, Any]:
    """One stored selection, or 404."""
    path = selections_dir(mirror) / f"{_safe_identifier(identifier)}.json"
    record = _read(path) if path.exists() else None
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown selection: {identifier}")
    return record


def create(mirror: str | Path | None, request: SelectionRequest) -> dict[str, Any]:
    """Write a new selection and return its record."""
    product_ids = [str(value) for value in request.product_ids]
    if not product_ids:
        raise HTTPException(status_code=400, detail="a selection needs at least one product id")
    root = mirror_root(mirror)
    directory = selections_dir(root)
    taken = {path.stem for path in directory.glob("*.json")}
    identifier = _identifier(request.name, product_ids, taken)
    record = {
        "id": identifier,
        "name": str(request.name),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # The user's list, not a deduplicated one: a frame contributes one
        # row per band half and the count has to match what was sent.
        "n_frames": len(product_ids),
        "note": request.note,
        "product_ids": product_ids,
        "halves": [str(value).upper() for value in (request.halves or [])],
        **_latitudes(root, product_ids, request.halves),
    }
    path = directory / f"{identifier}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temporary.replace(path)
    LOGGER.info("selection %s: %d product id(s)", identifier, len(product_ids))
    return record


def summary_of(record: dict[str, Any]) -> dict[str, Any]:
    """The record without its two long lists."""
    return {name: record.get(name) for name in SUMMARY_FIELDS}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.get("")
def list_selections(request: Request) -> list[dict[str, Any]]:
    return [summary_of(record) for record in load_all(request.app.state.mirror)]


@router.post("")
def post_selection(request: Request, body: SelectionRequest) -> dict[str, Any]:
    return summary_of(create(request.app.state.mirror, body))


@router.get("/{identifier}")
def get_selection(request: Request, identifier: str) -> dict[str, Any]:
    record = load_one(request.app.state.mirror, identifier)
    payload = summary_of(record)
    payload["product_ids"] = list(record.get("product_ids", []))
    payload["halves"] = list(record.get("halves", []))
    return payload


@router.delete("/{identifier}")
def delete_selection(request: Request, identifier: str) -> dict[str, str]:
    path = selections_dir(request.app.state.mirror) / f"{_safe_identifier(identifier)}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown selection: {identifier}")
    path.unlink()
    return {"deleted": identifier}
