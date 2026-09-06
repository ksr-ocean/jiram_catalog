"""Reader for JPL VICAR image files.

VICAR files begin with a fixed-length ASCII label (``LBLSIZE`` bytes, the
first key in the file) holding ``KEY=VALUE`` tokens, followed by the image
records.  Everything the reader needs to lay out the data is in that label:
``FORMAT`` gives the sample type, ``INTFMT``/``REALFMT`` the byte order,
``ORG`` the band interleave, ``NL``/``NS``/``NB`` the dimensions, and
``NLB``/``NBB`` the binary header lines and per-record prefix bytes that sit
between the label and the pixels.

The two files this project reads are the Ingersoll et al. (2022) polar maps
(3000 x 3200 ``REAL``, little-endian, one band) and the TRACKER4 ``.tp4``
velocity-vector tables (``NL`` x 8 ``REAL`` with ``EOL=1``), so the reader is
written generically rather than against either layout.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["read_vicar", "read_vicar_label"]

LabelValue = Any
Label = dict[str, LabelValue]

#: VICAR ``FORMAT`` -> (numpy base type, item size in bytes, "int" or "real").
_FORMATS: dict[str, tuple[str, int, str]] = {
    "BYTE": ("u1", 1, "int"),
    "HALF": ("i2", 2, "int"),
    "FULL": ("i4", 4, "int"),
    "REAL": ("f4", 4, "real"),
    "DOUB": ("f8", 8, "real"),
}

#: ``INTFMT``/``REALFMT`` -> numpy byte-order character.
_BYTE_ORDERS: dict[str, str] = {
    "LOW": "<",      # INTFMT, little-endian
    "HIGH": ">",     # INTFMT, big-endian
    "RIEEE": "<",    # REALFMT, byte-reversed IEEE 754
    "IEEE": ">",     # REALFMT, IEEE 754
}

_LBLSIZE_RE = re.compile(rb"LBLSIZE\s*=\s*(\d+)")
_KEY_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_INT_RE = re.compile(r"[+-]?\d+$")


# --------------------------------------------------------------------------
# label parsing
# --------------------------------------------------------------------------
def _convert(token: str) -> LabelValue:
    """Convert one label token to ``int``, ``float`` or ``str``."""
    token = token.strip()
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        return token[1:-1].replace("''", "'")
    if _INT_RE.match(token):
        return int(token)
    try:
        return float(token)
    except ValueError:
        return token


def _read_quoted(text: str, start: int) -> tuple[str, int]:
    """Read a ``'...'`` string starting at ``start``; ``''`` is a literal quote."""
    index = start + 1
    chunks: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "'":
            if index + 1 < len(text) and text[index + 1] == "'":
                chunks.append("'")
                index += 2
                continue
            return "".join(chunks), index + 1
        chunks.append(char)
        index += 1
    raise ValueError("unterminated quoted string in VICAR label")


def _split_tuple(body: str) -> list[str]:
    """Split a parenthesised label value on its top-level commas."""
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    index = 0
    while index < len(body):
        char = body[index]
        if in_quotes:
            current.append(char)
            if char == "'":
                if index + 1 < len(body) and body[index + 1] == "'":
                    current.append("'")
                    index += 1
                else:
                    in_quotes = False
        elif char == "'":
            in_quotes = True
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if current or items:
        items.append("".join(current))
    return items


def _read_value(text: str, start: int) -> tuple[LabelValue, int]:
    """Read one label value (quoted string, parenthesised tuple, or bare token)."""
    index = start
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    if index >= len(text):
        return "", index
    if text[index] == "'":
        return _read_quoted(text, index)
    if text[index] == "(":
        depth = 0
        in_quotes = False
        scan = index
        while scan < len(text):
            char = text[scan]
            if in_quotes:
                if char == "'":
                    in_quotes = False
            elif char == "'":
                in_quotes = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    body = text[index + 1 : scan]
                    return tuple(_convert(item) for item in _split_tuple(body)), scan + 1
            scan += 1
        raise ValueError("unterminated parenthesised value in VICAR label")
    stop = index
    while stop < len(text) and text[stop] not in " \t\r\n":
        stop += 1
    return _convert(text[index:stop]), stop


def _store(label: Label, key: str, value: LabelValue) -> None:
    """Store ``key``; repeated keys (``TASK``, ``USER``, ``DAT_TIM``) become lists."""
    if key not in label:
        label[key] = value
        return
    existing = label[key]
    if isinstance(existing, list):
        existing.append(value)
    else:
        label[key] = [existing, value]


def parse_label_text(text: str) -> Label:
    """Parse the ``KEY=VALUE`` tokens of a VICAR label block."""
    text = text.split("\0", 1)[0]
    label: Label = {}
    index = 0
    while index < len(text):
        match = _KEY_RE.match(text, index)
        if match is None:
            index += 1
            continue
        value, index = _read_value(text, match.end())
        _store(label, match.group(1), value)
    return label


def _label_size(handle) -> int:
    head = handle.read(256)
    match = _LBLSIZE_RE.search(head)
    if match is None:
        raise ValueError("not a VICAR file: no LBLSIZE in the first 256 bytes")
    return int(match.group(1))


def read_vicar_label(path: str | Path) -> Label:
    """Return the parsed main label of a VICAR file."""
    with open(path, "rb") as handle:
        size = _label_size(handle)
        handle.seek(0)
        block = handle.read(size)
    return parse_label_text(block.decode("ascii", errors="replace"))


# --------------------------------------------------------------------------
# data reading
# --------------------------------------------------------------------------
def _dtype(label: Label) -> np.dtype:
    fmt = str(label.get("FORMAT", "")).upper()
    if fmt not in _FORMATS:
        raise ValueError(f"unsupported VICAR FORMAT: {fmt!r}")
    base, _, kind = _FORMATS[fmt]
    if base == "u1":
        return np.dtype("u1")
    key = "INTFMT" if kind == "int" else "REALFMT"
    default = "LOW" if kind == "int" else "RIEEE"
    order = str(label.get(key, default)).upper()
    if order == "VAX":
        raise ValueError(f"unsupported VICAR {key}='VAX' (VAX floats are not IEEE)")
    if order not in _BYTE_ORDERS:
        raise ValueError(f"unsupported VICAR {key}: {order!r}")
    return np.dtype(_BYTE_ORDERS[order] + base)


def _dimensions(label: Label) -> tuple[int, int, int, str]:
    try:
        lines = int(label["NL"])
        samples = int(label["NS"])
        bands = int(label.get("NB", 1))
    except KeyError as exc:  # pragma: no cover - malformed label
        raise ValueError(f"VICAR label is missing {exc.args[0]}") from exc
    org = str(label.get("ORG", "BSQ")).upper()
    if org not in {"BSQ", "BIL", "BIP"}:
        raise ValueError(f"unsupported VICAR ORG: {org!r}")
    return lines, samples, bands, org


def read_vicar(path: str | Path) -> tuple[np.ndarray, Label]:
    """Read a VICAR file.

    Returns ``(array, label)``.  ``array`` has shape ``(NB, NL, NS)``, squeezed
    to ``(NL, NS)`` when ``NB == 1``, in the file's own sample type (native byte
    order).  ``NLB`` binary header lines and ``NBB`` per-record prefix bytes are
    skipped.  Labels appended after the data (``EOL=1``) are parsed and returned
    under the key ``"EOL_LABELS"`` rather than merged, so that keys such as
    ``LBLSIZE`` keep the value that describes the main label.
    """
    path = Path(path)
    label = read_vicar_label(path)
    lines, samples, bands, org = _dimensions(label)
    dtype = _dtype(label)
    lblsize = int(label["LBLSIZE"])
    prefix_bytes = int(label.get("NBB", 0))
    header_lines = int(label.get("NLB", 0))

    # (fastest, middle, slowest) axis lengths and the shape they unpack into.
    if org == "BSQ":
        fastest, middle, slowest = samples, lines, bands
        axes = ("band", "line", "sample")
    elif org == "BIL":
        fastest, middle, slowest = samples, bands, lines
        axes = ("line", "band", "sample")
    else:  # BIP
        fastest, middle, slowest = bands, samples, lines
        axes = ("line", "sample", "band")

    record_bytes = prefix_bytes + fastest * dtype.itemsize
    offset = lblsize + header_lines * record_bytes
    count = middle * slowest

    with open(path, "rb") as handle:
        handle.seek(offset)
        raw = np.fromfile(handle, dtype=np.uint8, count=count * record_bytes)
    if raw.size < count * record_bytes:
        raise ValueError(
            f"{path.name}: truncated data, expected {count * record_bytes} bytes "
            f"after offset {offset}, found {raw.size}"
        )
    raw = raw.reshape(count, record_bytes)[:, prefix_bytes:]
    data = raw.copy().view(dtype).reshape(slowest, middle, fastest)
    data = data.astype(dtype.newbyteorder("="), copy=False)

    order = [axes.index(name) for name in ("band", "line", "sample")]
    cube = np.transpose(data, order)

    if int(label.get("EOL", 0)):
        eol_offset = offset + count * record_bytes
        with open(path, "rb") as handle:
            handle.seek(eol_offset)
            tail = handle.read()
        if tail.strip(b"\0 "):
            label["EOL_LABELS"] = parse_label_text(tail.decode("ascii", errors="replace"))

    return (cube[0] if bands == 1 else cube), label
