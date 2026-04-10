"""DefineShape PNG loader (CatAssetsLoader / DefineShapesLoader).

_DEFINESHAPE_PNGDATA is the in-memory store of all DefinedShape PNGs
extracted from the game's catparts.swf.  Keys are SWF character IDs (int).

On startup:
  1. Try to load _DEFINESHAPE_PNGDATA from CatAssets/shape_cache.pkl.
  2. If cache exists and is valid, use it (instant).
  3. Otherwise, on first call to ensure_defineshape_pngdata():
     - Extract swfs/catparts.swf from the GPAK archive.
     - Use FfdecShapeExport (JPype + ffdec_lib) to render every DefinedShape.
     - Save the dict to shape_cache.pkl for next startup.

Cache is automatically invalidated when the GPAK file modification time changes.
"""
from __future__ import annotations

import logging
import os
import pickle
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("mewgenics.cat_assets")

_CAT_ASSETS_DIR = Path(__file__).resolve().parent
_SHAPE_CACHE_FILE = _CAT_ASSETS_DIR / "shape_cache.pkl"
_SHAPE_CACHE_MTIME_FILE = _CAT_ASSETS_DIR / "shape_cache.mtime"
_PALETTE_CACHE_FILE = _CAT_ASSETS_DIR / "palette_cache.pkl"
_PALETTE_CACHE_MTIME_FILE = _CAT_ASSETS_DIR / "palette_cache.mtime"

# ── Module-level state ────────────────────────────────────────────────────────
# Keys: SWF character IDs (int).  Values: raw PNG bytes.
_DEFINESHAPE_PNGDATA: dict[int, bytes] = {}
# Palette PNG bytes (single image)
_PALETTE_PNGDATA: bytes | None = None
_GPAK_PATH_AT_LOAD: str | None = None


def _save_shape_cache() -> None:
    """Save _DEFINESHAPE_PNGDATA to disk and record GPAK's mtime."""
    if not _DEFINESHAPE_PNGDATA:
        return
    try:
        with open(_SHAPE_CACHE_FILE, "wb") as f:
            pickle.dump(_DEFINESHAPE_PNGDATA, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.debug("Saved shape cache: %d shapes", len(_DEFINESHAPE_PNGDATA))

        if _GPAK_PATH_AT_LOAD and os.path.exists(_GPAK_PATH_AT_LOAD):
            mtime = os.path.getmtime(_GPAK_PATH_AT_LOAD)
            _SHAPE_CACHE_MTIME_FILE.write_text(str(mtime))
    except Exception as exc:
        logger.warning("Could not save shape cache: %s", exc)


def _load_shape_cache() -> bool:
    """Load *_DEFINESHAPE_PNGDATA* from disk if valid.

    Cache is considered invalid if:
      - The pickled file doesn't exist.
      - The ``.mtime`` file shows the GPAK has been modified since cache creation.

    Returns True if cache was loaded, False otherwise.
    """
    global _GPAK_PATH_AT_LOAD

    if not _SHAPE_CACHE_FILE.exists():
        return False

    # Check if GPAK has been modified since cache was written
    try:
        from mewgenics.utils.game_data import get_gpak_path

        gpak_path = get_gpak_path()
        if gpak_path and os.path.exists(gpak_path):
            current_mtime = os.path.getmtime(gpak_path)
            if _SHAPE_CACHE_MTIME_FILE.exists():
                cached_mtime = float(_SHAPE_CACHE_MTIME_FILE.read_text())
                if current_mtime > cached_mtime:
                    logger.debug("Shape cache is stale (GPAK was modified)")
                    return False
            _GPAK_PATH_AT_LOAD = gpak_path
    except Exception as exc:
        logger.debug("Could not check GPAK mtime: %s", exc)

    try:
        with open(_SHAPE_CACHE_FILE, "rb") as f:
            loaded = pickle.load(f)
        _DEFINESHAPE_PNGDATA.update(loaded)
        logger.debug("Loaded shape cache: %d shapes", len(_DEFINESHAPE_PNGDATA))
        return True
    except Exception as exc:
        logger.warning("Could not load shape cache: %s", exc)
        return False


def _save_palette_cache() -> None:
    """Save _PALETTE_PNGDATA to disk and record GPAK's mtime."""
    if _PALETTE_PNGDATA is None:
        return
    try:
        with open(_PALETTE_CACHE_FILE, "wb") as f:
            pickle.dump(_PALETTE_PNGDATA, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.debug("Saved palette cache: %d bytes", len(_PALETTE_PNGDATA))

        if _GPAK_PATH_AT_LOAD and os.path.exists(_GPAK_PATH_AT_LOAD):
            mtime = os.path.getmtime(_GPAK_PATH_AT_LOAD)
            _PALETTE_CACHE_MTIME_FILE.write_text(str(mtime))
    except Exception as exc:
        logger.warning("Could not save palette cache: %s", exc)


def _load_palette_cache() -> bool:
    """Load *_PALETTE_PNGDATA* from disk if valid.

    Cache is considered invalid if:
      - The pickled file doesn't exist.
      - The ``.mtime`` file shows the GPAK has been modified since cache creation.

    Returns True if cache was loaded, False otherwise.
    """
    global _PALETTE_PNGDATA, _GPAK_PATH_AT_LOAD

    if not _PALETTE_CACHE_FILE.exists():
        return False

    # Check if GPAK has been modified since cache was written
    try:
        from mewgenics.utils.game_data import get_gpak_path

        gpak_path = get_gpak_path()
        if gpak_path and os.path.exists(gpak_path):
            current_mtime = os.path.getmtime(gpak_path)
            if _PALETTE_CACHE_MTIME_FILE.exists():
                cached_mtime = float(_PALETTE_CACHE_MTIME_FILE.read_text())
                if current_mtime > cached_mtime:
                    logger.debug("Palette cache is stale (GPAK was modified)")
                    return False
            if not _GPAK_PATH_AT_LOAD:
                _GPAK_PATH_AT_LOAD = gpak_path
    except Exception as exc:
        logger.debug("Could not check GPAK mtime: %s", exc)

    try:
        with open(_PALETTE_CACHE_FILE, "rb") as f:
            loaded = pickle.load(f)
        _PALETTE_PNGDATA = loaded
        logger.debug("Loaded palette cache: %d bytes", len(_PALETTE_PNGDATA) if _PALETTE_PNGDATA else 0)
        return True
    except Exception as exc:
        logger.warning("Could not load palette cache: %s", exc)
        return False


def ensure_defineshape_pngdata() -> bool:
    """Populate *_DEFINESHAPE_PNGDATA* from catparts.swf via ffdec_lib.

    Does nothing (returns True immediately) if already populated.

    Steps:
      1. Retrieve the GPAK path via ``get_gpak_path()``.
      2. Use :mod:`CatAssetsGPAKExtract` to extract ``swfs/catparts.swf``.
      3. Use :mod:`FfdecShapeExport` (JPype + ffdec_lib) to export all
         DefinedShape tags as PNGs into *_DEFINESHAPE_PNGDATA*.
      4. Save the dict to shape_cache.pkl for next startup.

    Returns:
        True  – dict is populated (either pre-existing or freshly loaded).
        False – GPAK unavailable, extraction failed, or JPype/Java not found.
    """
    if _DEFINESHAPE_PNGDATA:
        return True

    # Lazy import to avoid circular dependency at module load time
    from mewgenics.utils.game_data import get_gpak_path

    gpak_path = get_gpak_path()
    if not gpak_path:
        logger.warning("GPAK path not set — cannot load DefinedShape PNGs")
        return False

    # Ensure sibling modules in CatAssets/ are importable
    _here = str(_CAT_ASSETS_DIR)
    if _here not in sys.path:
        sys.path.insert(0, _here)

    # Step 1: extract catparts.swf bytes from the GPAK archive
    logger.info("Extracting swfs/catparts.swf from GPAK at %s", gpak_path)
    from CatAssetsGPAKExtract import extract_entry  # noqa: PLC0415

    swf_bytes = extract_entry(gpak_path, "swfs/catparts.swf")
    if not swf_bytes:
        logger.error("Could not extract swfs/catparts.swf from GPAK")
        return False

    # Step 2: write SWF to a temp file so ffdec can open it via FileInputStream
    with tempfile.TemporaryDirectory(prefix="mewgenics_shapes_") as tmp:
        swf_path = Path(tmp) / "catparts.swf"
        swf_path.write_bytes(swf_bytes)

        from FfdecShapeExport import export_shapes  # noqa: PLC0415
        export_shapes(str(swf_path), _DEFINESHAPE_PNGDATA)

    if not _DEFINESHAPE_PNGDATA:
        logger.error("ffdec shape export produced no results")
        return False

    logger.info(
        "Loaded %d DefinedShape PNGs into _DEFINESHAPE_PNGDATA",
        len(_DEFINESHAPE_PNGDATA),
    )

    # Step 3: persist the cache to disk
    _save_shape_cache()
    return True


def get_defineshape_png(char_id: int) -> bytes | None:
    """Return PNG bytes for a DefinedShape by SWF character ID, or None."""
    return _DEFINESHAPE_PNGDATA.get(char_id)


def ensure_palette_pngdata() -> bool:
    """Populate *_PALETTE_PNGDATA* from textures/palette.png in the GPAK.

    Does nothing (returns True immediately) if already populated.

    Steps:
      1. Retrieve the GPAK path via ``get_gpak_path()``.
      2. Use :mod:`CatAssetsGPAKExtract` to extract ``textures/palette.png``.
      3. Store the PNG bytes in *_PALETTE_PNGDATA*.
      4. Save to palette_cache.pkl for next startup.

    Returns:
        True  – palette is loaded (either pre-existing or freshly loaded).
        False – GPAK unavailable or extraction failed.
    """
    global _PALETTE_PNGDATA

    if _PALETTE_PNGDATA is not None:
        return True

    # Lazy import to avoid circular dependency at module load time
    from mewgenics.utils.game_data import get_gpak_path

    gpak_path = get_gpak_path()
    if not gpak_path:
        logger.warning("GPAK path not set — cannot load palette PNG")
        return False

    # Ensure sibling modules in CatAssets/ are importable
    _here = str(_CAT_ASSETS_DIR)
    if _here not in sys.path:
        sys.path.insert(0, _here)

    # Extract palette.png from the GPAK archive
    logger.info("Extracting textures/palette.png from GPAK at %s", gpak_path)
    from CatAssetsGPAKExtract import extract_entry  # noqa: PLC0415

    palette_bytes = extract_entry(gpak_path, "textures/palette.png")
    if not palette_bytes:
        logger.error("Could not extract textures/palette.png from GPAK")
        return False

    _PALETTE_PNGDATA = palette_bytes
    logger.info("Loaded palette PNG: %d bytes", len(_PALETTE_PNGDATA))

    # Persist the cache to disk
    _save_palette_cache()
    return True


def get_palette_png() -> bytes | None:
    """Return palette PNG bytes, or None if not loaded."""
    return _PALETTE_PNGDATA


# ── Initialization (called on module import) ──────────────────────────────────
# Attempt to load the caches from disk on startup
_load_shape_cache()
_load_palette_cache()
