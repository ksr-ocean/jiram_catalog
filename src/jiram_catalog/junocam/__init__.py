"""JunoCam layer of the Juno catalog: acquisition, index, and quality.

JunoCam is archived as PDS3 volumes ``JNOJNC_0001`` ... ``JNOJNC_0035`` on
the PDS Imaging node, a different node, node generation, and label dialect
from the PDS4 JIRAM bundle the rest of this package reads.  Everything
JunoCam-specific therefore lives in this subpackage and mirrors the JIRAM
module names one level down: :mod:`~jiram_catalog.junocam.pds` (manifest),
:mod:`~jiram_catalog.junocam.mirror` (wget), :mod:`~jiram_catalog.junocam.labels`
(PDS3 label -> row), :mod:`~jiram_catalog.junocam.index` (parquet index) and
:mod:`~jiram_catalog.junocam.quality` (epoch flags and measured metrics).

Everything this layer writes lives under ``<mirror>/junocam/``.
"""

from __future__ import annotations

__all__ = ["cli", "index", "labels", "mirror", "pds", "quality"]
