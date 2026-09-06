"""Named map regions on Jupiter and the pixel grids that define them.

A *region* is a rectangular raster plus the rule that ties each of its pixels
to a point on the Jupiter ellipsoid.  Two rules are supported.

``polar_ortho``
    The published perijove-4 maps' rule, generalised to either pole: the map
    *is* the body-fixed equatorial plane, so a surface point with body-fixed
    coordinates ``(X, Y, Z)`` in ``IAU_JUPITER`` lands at a fixed number of
    kilometres from the pole pixel.  With the default central meridian
    ``lon0_east_deg = 90`` and hemisphere ``N`` the mapping is exactly the one
    :data:`jiram_catalog.reproject.PAPER_GRID` implements::

        row = pole_row + X / km_per_px          (X points at   0 E)
        col = pole_col + Y / km_per_px          (Y points at  90 E)

    For a general central meridian ``phi0`` the two axes rotate with it: the
    ``phi0`` meridian runs from the pole along +column and the ``phi0 - 90``
    meridian along +row.  For hemisphere ``S`` the column axis is negated
    (``col = pole_col - Y / km_per_px``), which is the reflection that keeps
    the map an *outside* view of the planet -- the handedness a viewer above
    the south pole sees.  Note that this makes east longitude run clockwise on
    a south-polar map and counter-clockwise on a north-polar one, under one and
    the same display convention; the two senses cannot be made to agree while
    both maps are outside views, so the outside view is what is kept.

``local_ortho``
    Tangent-plane orthographic at an arbitrary centre.  At the centre surface
    point ``c`` build ``up`` (the outward ellipsoid normal), ``north`` (the
    component of ``+Z`` orthogonal to ``up``) and ``east = north x up``.  A
    pixel is placed in the tangent plane at ``p = c + x_km*east + y_km*north``
    and dropped straight down onto the ellipsoid along ``-up``; a surface point
    is read back with ``x_km = (P - c).east``, ``y_km = (P - c).north`` and is
    on the grid only while its own surface normal still faces ``up``, i.e.
    while it is on the near side of the silhouette that an observer at
    infinity along ``up`` would see.

Both rules use ``x_km = (col - col0) * km_per_px`` and
``y_km = (row - row0) * km_per_px`` about the region's reference pixel (the
pole pixel, or the centre pixel).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .geo import longitude_arc
from .reproject import PolarStereo, surface_point

__all__ = [
    "JUPITER_RADII_KM",
    "PROJECTIONS",
    "RegionGrid",
    "default_registry_path",
    "load_registry",
    "region_names",
]

#: Jupiter's triaxial radii (km), the ``pck00010`` values the geometry engine
#: uses.  Hard-coded because a region grid must be usable without SPICE.
JUPITER_RADII_KM: tuple[float, float, float] = (71492.0, 71492.0, 66854.0)

#: Region projection rules.
PROJECTIONS: tuple[str, ...] = ("polar_ortho", "local_ortho")

#: A point this far past the equator still counts as being in the hemisphere a
#: polar grid covers (degrees); the orthographic radius is symmetric about the
#: equator, so the wrong hemisphere has to be excluded explicitly.
POLAR_LAT_TOL_DEG = 1e-9
#: Slack on the near-side test of a local grid (dimensionless, a cosine).
LOCAL_VISIBILITY_TOL = 1e-12
#: Longitude arc at or beyond which :meth:`RegionGrid.bbox_latlon` reports the
#: whole circle; matches ``geo.FULL_ARC_DEG``.
FULL_ARC_DEG = 350.0


def default_registry_path() -> Path:
    """Where ``configs/regions.yaml`` lives, honouring ``JIRAM_REGIONS``."""
    configured = os.environ.get("JIRAM_REGIONS")
    if configured:
        return Path(configured).expanduser()
    packaged = Path(__file__).resolve().parents[2] / "configs" / "regions.yaml"
    if packaged.is_file():
        return packaged
    return Path("configs/regions.yaml")


def _ray_intercepts(
    origins: np.ndarray, direction: np.ndarray, radii: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """:func:`geometry.ellipsoid_intercept` with one origin *per ray*.

    The library version takes a single observer position and a stack of
    directions; a tangent-plane grid is the other way round -- one direction
    (straight down) and one origin per pixel -- so the same quadratic is solved
    here in the transposed layout.  ``tests/test_regions_offline.py`` checks the
    two against each other.
    """
    origins = np.asarray(origins, dtype=np.float64)
    unit = np.asarray(direction, dtype=np.float64).reshape(3)
    semi_axes = np.asarray(radii, dtype=np.float64).reshape(3)

    scaled_origins = origins / semi_axes
    scaled_dir = unit / semi_axes
    quad = float(scaled_dir @ scaled_dir)
    half_linear = scaled_origins @ scaled_dir
    constant = np.sum(scaled_origins * scaled_origins, axis=-1) - 1.0

    discriminant = half_linear**2 - quad * constant
    hit = discriminant >= 0.0
    root = np.sqrt(np.where(hit, discriminant, 0.0))
    near = (-half_linear - root) / quad
    far = (-half_linear + root) / quad
    distance = np.where(near > 0.0, near, far)
    hit = hit & (distance > 0.0)
    distance = np.where(hit, distance, np.nan)
    return origins + distance[..., None] * unit, distance, hit


def _pair(value: Any, name: str) -> tuple[float, float]:
    values = np.asarray(value, dtype=np.float64).reshape(-1)
    if values.size != 2:
        raise ValueError(f"{name} must have two entries, not {values.size}")
    return (float(values[0]), float(values[1]))


@dataclass(frozen=True)
class RegionGrid:
    """One named region: a raster plus its pixel <-> (lat, lon) rule."""

    name: str
    projection: str
    km_per_px: float
    shape: tuple[int, int]
    #: ``polar_ortho`` only.
    hemisphere: str | None = None
    pole_pixel: tuple[float, float] | None = None
    lon0_east_deg: float = 90.0
    #: ``local_ortho`` only: planetocentric ``(lat, lon_east)`` in degrees.
    center: tuple[float, float] | None = None
    center_pixel: tuple[float, float] | None = None
    radii_km: tuple[float, float, float] = JUPITER_RADII_KM
    description: str = ""

    def __post_init__(self) -> None:
        if self.projection not in PROJECTIONS:
            raise ValueError(
                f"{self.name}: projection must be one of {PROJECTIONS}, "
                f"not {self.projection!r}"
            )
        if not self.km_per_px > 0:
            raise ValueError(f"{self.name}: km_per_px must be positive")
        rows, cols = (int(value) for value in self.shape)
        if rows < 1 or cols < 1:
            raise ValueError(f"{self.name}: shape must be positive")
        object.__setattr__(self, "shape", (rows, cols))
        object.__setattr__(self, "km_per_px", float(self.km_per_px))
        object.__setattr__(
            self, "radii_km", tuple(float(value) for value in self.radii_km)
        )
        if self.projection == "polar_ortho":
            if self.hemisphere not in ("N", "S"):
                raise ValueError(f"{self.name}: hemisphere must be 'N' or 'S'")
            if self.pole_pixel is None:
                raise ValueError(f"{self.name}: polar_ortho needs pole_pixel")
            object.__setattr__(self, "pole_pixel", _pair(self.pole_pixel, "pole_pixel"))
            object.__setattr__(self, "lon0_east_deg", float(self.lon0_east_deg))
        else:
            if self.center is None or self.center_pixel is None:
                raise ValueError(
                    f"{self.name}: local_ortho needs center and center_pixel"
                )
            object.__setattr__(self, "center", _pair(self.center, "center"))
            object.__setattr__(
                self, "center_pixel", _pair(self.center_pixel, "center_pixel")
            )

    # -- construction ------------------------------------------------------
    @classmethod
    def from_dict(cls, entry: dict[str, Any]) -> "RegionGrid":
        """Build a region from one registry entry."""
        known = {
            "name",
            "projection",
            "km_per_px",
            "shape",
            "hemisphere",
            "pole_pixel",
            "lon0_east_deg",
            "center",
            "center_pixel",
            "radii_km",
            "description",
        }
        unknown = sorted(set(entry) - known)
        if unknown:
            raise ValueError(f"{entry.get('name')}: unknown keys {unknown}")
        fields = {key: entry[key] for key in known if key in entry}
        fields["shape"] = tuple(int(value) for value in entry["shape"])
        return cls(**fields)

    @classmethod
    def from_yaml(cls, name: str, path: str | Path | None = None) -> "RegionGrid":
        """Look one region up in ``configs/regions.yaml`` (or ``path``)."""
        registry = load_registry(path)
        if name not in registry:
            raise ValueError(
                f"unknown region: {name!r}; known regions: {', '.join(registry)}"
            )
        return registry[name]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable description, round-trippable through ``from_dict``."""
        result: dict[str, Any] = {
            "name": self.name,
            "projection": self.projection,
            "km_per_px": self.km_per_px,
            "shape": [int(self.shape[0]), int(self.shape[1])],
            "radii_km": [float(value) for value in self.radii_km],
        }
        if self.projection == "polar_ortho":
            result["hemisphere"] = self.hemisphere
            result["pole_pixel"] = [float(value) for value in self.pole_pixel]
            result["lon0_east_deg"] = self.lon0_east_deg
        else:
            result["center"] = [float(value) for value in self.center]
            result["center_pixel"] = [float(value) for value in self.center_pixel]
        if self.description:
            result["description"] = self.description
        return result

    # -- derived geometry --------------------------------------------------
    @property
    def rows(self) -> int:
        return int(self.shape[0])

    @property
    def cols(self) -> int:
        return int(self.shape[1])

    @property
    def reference_pixel(self) -> tuple[float, float]:
        """0-based ``(row, col)`` about which ``x_km``/``y_km`` are measured."""
        return self.pole_pixel if self.projection == "polar_ortho" else self.center_pixel

    @cached_property
    def _polar(self) -> PolarStereo:
        """The equivalent :class:`PolarStereo`, exact for ``PAPER_GRID``.

        ``PolarStereo`` writes ``line = pole - y/s`` and ``sample = pole + x/s``
        with ``(x, y) = rho (cos az, sin az)`` and ``az = +-(lon - lon0)``.  For
        hemisphere N and ``az = lon - phi0`` that is ``row = pole_row +
        rho sin(phi0 - lon)/s`` and ``col = pole_col + rho cos(phi0 - lon)/s``,
        i.e. exactly this module's convention.  Negating the column axis for S
        is a 180 degree rotation of that map, which is the same thing as
        reflecting the azimuth (``clockwise=True``) and moving the central
        meridian to ``phi0 + 180``.
        """
        row0, col0 = self.pole_pixel
        if self.hemisphere == "N":
            lon0, clockwise = self.lon0_east_deg, False
        else:
            lon0, clockwise = self.lon0_east_deg + 180.0, True
        return PolarStereo(
            pole_line=row0,
            pole_sample=col0,
            km_per_px=self.km_per_px,
            lon0_deg=lon0,
            clockwise=clockwise,
            hemisphere=self.hemisphere,
            radius_km=self.radii_km[2],
            projection="orthographic",
            equatorial_radius_km=self.radii_km[0],
        )

    @cached_property
    def _local(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(centre point, east, north, up)`` of a tangent-plane region, km."""
        radii = np.asarray(self.radii_km, dtype=np.float64)
        centre = np.asarray(
            surface_point(self.center[0], self.center[1], radii), dtype=np.float64
        ).reshape(3)
        up = centre / radii**2
        up = up / np.linalg.norm(up)
        pole = np.array([0.0, 0.0, 1.0])
        north = pole - float(pole @ up) * up
        norm = float(np.linalg.norm(north))
        if norm < 1e-12:
            raise ValueError(
                f"{self.name}: a local_ortho centre at a pole has no north "
                "direction; use projection polar_ortho instead"
            )
        north = north / norm
        east = np.cross(north, up)
        east = east / np.linalg.norm(east)
        return centre, east, north, up

    # -- axes --------------------------------------------------------------
    def x_km(self, col) -> np.ndarray:
        """Map abscissa in km of a (fractional) column index."""
        _, col0 = self.reference_pixel
        return (np.asarray(col, dtype=np.float64) - col0) * self.km_per_px

    def y_km(self, row) -> np.ndarray:
        """Map ordinate in km of a (fractional) row index."""
        row0, _ = self.reference_pixel
        return (np.asarray(row, dtype=np.float64) - row0) * self.km_per_px

    def extent_km(self) -> tuple[float, float, float, float]:
        """``(x_min, x_max, y_min, y_max)`` of the pixel centres, in km."""
        return (
            float(self.x_km(0)),
            float(self.x_km(self.cols - 1)),
            float(self.y_km(0)),
            float(self.y_km(self.rows - 1)),
        )

    # -- the two mappings --------------------------------------------------
    def pixel_to_latlon(self, row, col) -> tuple[np.ndarray, np.ndarray]:
        """0-based ``(row, col)`` to planetocentric ``(lat, lon_east)``, degrees.

        Pixels that do not see the ellipsoid at all -- past the limb of a polar
        grid, past the silhouette of a local one -- come back as NaN.
        """
        row = np.asarray(row, dtype=np.float64)
        col = np.asarray(col, dtype=np.float64)
        if self.projection == "polar_ortho":
            lat, lon = self._polar.pixel_to_latlon(row, col)
            row0, col0 = self.pole_pixel
            rho = np.hypot(row - row0, col - col0) * self.km_per_px
            off = rho > self.radii_km[0]
            lat = np.where(off, np.nan, lat)
            lon = np.where(off, np.nan, lon)
            return lat, lon

        centre, east, north, up = self._local
        radii = np.asarray(self.radii_km, dtype=np.float64)
        offsets = (
            self.x_km(col)[..., None] * east + self.y_km(row)[..., None] * north
        )
        plane = centre + offsets
        above = plane + 2.0 * float(radii.max()) * up
        point, _, hit = _ray_intercepts(above, -up, radii)
        x, y, z = point[..., 0], point[..., 1], point[..., 2]
        lat = np.degrees(np.arctan2(z, np.hypot(x, y)))
        lon = np.degrees(np.arctan2(y, x)) % 360.0
        return np.where(hit, lat, np.nan), np.where(hit, lon, np.nan)

    def latlon_to_pixel(self, lat, lon_east) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Planetocentric ``(lat, lon_east)`` to 0-based ``(row, col, visible)``.

        ``visible`` says the surface point is on the side of the planet this
        grid draws; it says nothing about the canvas bounds, which the caller
        applies separately.  For ``polar_ortho`` that means the right
        hemisphere, because the orthographic radius is the same on both sides
        of the equator.  For ``local_ortho`` it is the near side of the
        silhouette, tested on the surface normal at the point: the tangent
        plane touches the ellipsoid at ``c``, so ``(P - c) . up`` is at most
        zero for *every* surface point and cannot separate near from far, while
        ``n(P) . up > 0`` is exactly the set of points the downward ray from a
        pixel can reach.
        """
        lat = np.asarray(lat, dtype=np.float64)
        lon_east = np.asarray(lon_east, dtype=np.float64)
        if self.projection == "polar_ortho":
            row, col = self._polar.latlon_to_pixel(lat, lon_east)
            if self.hemisphere == "N":
                facing = lat >= -POLAR_LAT_TOL_DEG
            else:
                facing = lat <= POLAR_LAT_TOL_DEG
            visible = facing & np.isfinite(row) & np.isfinite(col)
            return row, col, visible

        centre, east, north, up = self._local
        radii = np.asarray(self.radii_km, dtype=np.float64)
        point = np.asarray(surface_point(lat, lon_east, radii), dtype=np.float64)
        offset = point - centre
        x = offset @ east
        y = offset @ north
        row0, col0 = self.center_pixel
        row = row0 + y / self.km_per_px
        col = col0 + x / self.km_per_px
        normal = point / radii**2
        normal = normal / np.linalg.norm(normal, axis=-1, keepdims=True)
        visible = (normal @ up) > -LOCAL_VISIBILITY_TOL
        visible = visible & np.isfinite(row) & np.isfinite(col)
        return row, col, visible

    # -- convenience -------------------------------------------------------
    def inside(self, row, col) -> np.ndarray:
        """True where a (fractional) pixel index lies on the canvas."""
        row = np.asarray(row, dtype=np.float64)
        col = np.asarray(col, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            return (
                (row >= 0.0)
                & (row <= self.rows - 1.0)
                & (col >= 0.0)
                & (col <= self.cols - 1.0)
            )

    def lat_lon_grids(
        self, window: tuple[int, int, int, int] | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """2-D latitude and east-longitude of every pixel of (a window of) the grid."""
        row0, row1, col0, col1 = (0, self.rows, 0, self.cols) if window is None else window
        rows = np.arange(row0, row1, dtype=np.float64)
        cols = np.arange(col0, col1, dtype=np.float64)
        grid_rows, grid_cols = np.meshgrid(rows, cols, indexing="ij")
        return self.pixel_to_latlon(grid_rows, grid_cols)

    def bbox_latlon(self) -> tuple[float, float, float, float]:
        """``(lat_min, lat_max, lon_min_east, lon_max_east)`` covering the grid.

        The latitude bounds come from the grid boundary at full resolution plus
        a coarse interior sample; the longitude bounds are the shortest arc
        containing the sampled longitudes, reported as ``(0, 360)`` for a grid
        that rings a pole.  A pole that falls inside the canvas is added
        exactly, because it is the one latitude extremum that is not on the
        boundary.
        """
        rows, cols = self.rows, self.cols
        row_index = np.arange(rows, dtype=np.float64)
        col_index = np.arange(cols, dtype=np.float64)
        edge_rows = np.concatenate(
            [np.zeros(cols), np.full(cols, rows - 1.0), row_index, row_index]
        )
        edge_cols = np.concatenate(
            [col_index, col_index, np.zeros(rows), np.full(rows, cols - 1.0)]
        )
        lat_edge, lon_edge = self.pixel_to_latlon(edge_rows, edge_cols)

        step_row = max(1, rows // 256)
        step_col = max(1, cols // 256)
        inner_rows, inner_cols = np.meshgrid(
            np.arange(0, rows, step_row, dtype=np.float64),
            np.arange(0, cols, step_col, dtype=np.float64),
            indexing="ij",
        )
        lat_inner, lon_inner = self.pixel_to_latlon(inner_rows, inner_cols)

        latitudes = np.concatenate([lat_edge.ravel(), lat_inner.ravel()])
        longitudes = np.concatenate([lon_edge.ravel(), lon_inner.ravel()])
        finite = np.isfinite(latitudes) & np.isfinite(longitudes)
        if not finite.any():
            raise ValueError(f"{self.name}: no pixel of the grid sees the planet")
        latitudes = latitudes[finite]
        longitudes = longitudes[finite]

        lat_min, lat_max = float(latitudes.min()), float(latitudes.max())
        lon_min, lon_max, span = longitude_arc(longitudes)

        for pole_lat in (90.0, -90.0):
            row, col, visible = self.latlon_to_pixel(pole_lat, 0.0)
            if bool(visible) and bool(self.inside(row, col)):
                lat_min = min(lat_min, pole_lat)
                lat_max = max(lat_max, pole_lat)
                span = 360.0
        if span >= FULL_ARC_DEG:
            lon_min, lon_max = 0.0, 360.0
        return lat_min, lat_max, float(lon_min), float(lon_max)

    def canvas_grid(self, row0: int = 0, col0: int = 0) -> "CanvasGrid":
        """Adapter with the two-value interface :mod:`reproject` expects."""
        return CanvasGrid(self, int(row0), int(col0))


@dataclass(frozen=True)
class CanvasGrid:
    """A :class:`RegionGrid` seen as a plain map grid, offset by a crop origin.

    :func:`jiram_catalog.reproject.reproject_frame` wants ``latlon_to_pixel``
    to return two values and to answer NaN where a point is not on the map, and
    it works in the coordinates of the array it fills; both are what this thin
    wrapper provides.
    """

    region: RegionGrid
    row0: int = 0
    col0: int = 0

    def latlon_to_pixel(self, lat, lon_east) -> tuple[np.ndarray, np.ndarray]:
        row, col, visible = self.region.latlon_to_pixel(lat, lon_east)
        return (
            np.where(visible, row - self.row0, np.nan),
            np.where(visible, col - self.col0, np.nan),
        )

    def pixel_to_latlon(self, line, sample) -> tuple[np.ndarray, np.ndarray]:
        return self.region.pixel_to_latlon(
            np.asarray(line, dtype=np.float64) + self.row0,
            np.asarray(sample, dtype=np.float64) + self.col0,
        )


def load_registry(path: str | Path | None = None) -> dict[str, RegionGrid]:
    """Read the region registry, keyed by name and in file order."""
    location = Path(path) if path is not None else default_registry_path()
    if not location.is_file():
        raise FileNotFoundError(f"region registry not found: {location}")
    document = yaml.safe_load(location.read_text(encoding="utf-8")) or {}
    entries = document.get("regions", [])
    if not isinstance(entries, list):
        raise ValueError(f"{location}: 'regions' must be a list of entries")
    registry: dict[str, RegionGrid] = {}
    for entry in entries:
        region = RegionGrid.from_dict(dict(entry))
        if region.name in registry:
            raise ValueError(f"{location}: duplicate region {region.name!r}")
        registry[region.name] = region
    return registry


def region_names(path: str | Path | None = None) -> list[str]:
    """Names in the registry, in file order."""
    return list(load_registry(path))
