"""Python replacement for FfdecShapeExport.java.

Uses JPype1 to load ffdec_lib.jar (and its dependencies) into an embedded JVM,
then calls the JPEXS library API directly to export every DefinedShape tag from
a SWF as a PNG.

Exports all DefinedShape (tag codes 2, 22, 32, 83) from *swf_path* to *out_dir*
as ``<charId>.png`` files, and populates *target_dict* keyed by character ID.

Public entry point
------------------
    from FfdecShapeExport import export_shapes

    result = export_shapes(swf_path, target_dict)
    # result: dict[int, bytes]  same object as target_dict
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("mewgenics.ffdec_shape_export")

_CAT_ASSETS_DIR = Path(__file__).resolve().parent
_FFDEC_LIB_DIR  = _CAT_ASSETS_DIR / "ffdec_lib_26.0.0"

# ffdec tag codes for DefinedShape 1-4
_SHAPE_TAG_CODES = frozenset((2, 22, 32, 83))


def _build_classpath() -> list[str]:
    return [
        str(_FFDEC_LIB_DIR / name)
        for name in os.listdir(_FFDEC_LIB_DIR)
        if name.endswith(".jar")
    ]


def _start_jvm() -> bool:
    """Start the JVM with ffdec_lib on the classpath (idempotent)."""
    import jpype 
    if jpype.isJVMStarted():
        return True

    if not _FFDEC_LIB_DIR.is_dir():
        logger.error("ffdec_lib_26.0.0 folder not found at %s", _FFDEC_LIB_DIR)
        return False

    cp = _build_classpath()
    if not cp:
        logger.error("No .jar files found in %s", _FFDEC_LIB_DIR)
        return False

    # Try default system JVM first
    jvm_path = jpype.getDefaultJVMPath()
    if jvm_path:
        try:
            jpype.startJVM(
                jvm_path,
                "-ea",
                classpath=cp,
                convertStrings=False,
            )
            logger.debug("JVM started with system default JVM, %d jars from ffdec_lib_26.0.0", len(cp))
            return True
        except Exception as exc:
            logger.warning("Failed to start JVM with system default: %s", exc)
    
    # Fallback: try jlink-compiled JVM from src/CatAssets/jre
    jre_dir = _CAT_ASSETS_DIR / "jre"
    if jre_dir.is_dir():
        jvm_path = jre_dir / "bin" / "server" / "jvm.dll"
        if not jvm_path.exists():
            jvm_path = jre_dir / "lib" / "server" / "libjvm.so"
        if not jvm_path.exists():
            jvm_path = jre_dir / "lib" / "server" / "libjvm.dylib"  # macOS
        
        if jvm_path.exists():
            logger.debug("Trying jlink Java JVM library from %s", jvm_path)
            try:
                jpype.startJVM(
                    str(jvm_path),
                    "-ea",
                    classpath=cp,
                    convertStrings=False,
                )
                logger.debug("JVM started with jlink Java, %d jars from ffdec_lib_26.0.0", len(cp))
                return True
            except Exception as exc:
                logger.error("Failed to start JVM with jlink Java: %s", exc)
                return False
    
    logger.error("Could not find a working JVM (system default failed, jre folder not found or JVM library missing)")
    return False


def export_shapes(
    swf_path: str | Path,
    target_dict: dict[int, bytes],
) -> dict[int, bytes]:
    """Export all DefinedShape tags from *swf_path* as PNG bytes.

    Loads the SWF via ``com.jpexs.decompiler.flash.SWF``, collects all
    DefinedShape tags, and exports them in bulk via ShapeExporter.exportShapes.

    The PNG bytes (in memory, not written to disk) are stored in *target_dict*
    keyed by the SWF character ID and also returned.

    Args:
        swf_path:    Path to the ``.swf`` file to process.
        target_dict: Dict to populate with ``char_id -> png_bytes`` entries.

    Returns:
        *target_dict* (modified in place).
    """
    if not _start_jvm():
        return target_dict

    try:
        import jpype  # type: ignore
        import jpype.imports  # type: ignore
        from java.io import FileInputStream  # type: ignore
        from com.jpexs.decompiler.flash import SWF  # type: ignore
        from com.jpexs.decompiler.flash import ReadOnlyTagList  # type: ignore
        from com.jpexs.decompiler.flash.exporters import ShapeExporter  # type: ignore
        from com.jpexs.decompiler.flash.exporters.settings import ShapeExportSettings  # type: ignore
        from com.jpexs.decompiler.flash.exporters.modes import ShapeExportMode  # type: ignore
    except Exception as exc:
        logger.error("Could not import ffdec Java classes: %s", exc)
        return target_dict

    swf_path = str(swf_path)
    logger.info("Opening SWF for DefinedShape export: %s", swf_path)

    try:
        with FileInputStream(swf_path) as fis:
            swf = SWF(fis, True)
    except Exception as exc:
        logger.error("Failed to open SWF %s: %s", swf_path, exc)
        return target_dict

    # Collect all DefinedShape tags
    shape_tags = []
    try:
        all_tags = swf.getTags()
        for tag in all_tags:
            tag_id = int(tag.getId())
            if tag_id not in _SHAPE_TAG_CODES:
                continue
            if not isinstance(tag, jpype.JClass("com.jpexs.decompiler.flash.tags.base.CharacterIdTag")):
                continue
            shape_tags.append(tag)
    except Exception as exc:
        logger.error("Failed to collect shape tags: %s", exc)
        return target_dict

    logger.debug("Found %d DefinedShape tags to export", len(shape_tags))
    if not shape_tags:
        logger.info("FfdecShapeExport: No DefinedShape tags found")
        return target_dict

    # Create ReadOnlyTagList from collected tags
    try:
        ArrayList = jpype.JClass("java.util.ArrayList")
        java_list = ArrayList()
        for tag in shape_tags:
            java_list.add(tag)
        tag_list = jpype.JClass("com.jpexs.decompiler.flash.ReadOnlyTagList")(java_list)
    except Exception as exc:
        logger.error("Failed to create tag list: %s", exc)
        return target_dict

    # Export all shapes at once
    exporter = ShapeExporter()
    settings = ShapeExportSettings(ShapeExportMode.PNG, 1.0)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="ffdec_shapes_") as tmp_dir:
        try:
            # exportShapes(handler, outdir, swf, tags, settings, evl, unzoom, aaScale)
            # We pass None for handler and evl since we don't need them
            file_list = exporter.exportShapes(
                None,           # AbortRetryIgnoreHandler
                tmp_dir,        # outdir
                swf,            # SWF
                tag_list,       # ReadOnlyTagList
                settings,       # ShapeExportSettings
                None,           # EventListener
                1.0,            # unzoom
                1               # aaScale
            )
            
            logger.debug("exportShapes returned %d files", len(file_list) if file_list else 0)
            
            if file_list:
                for java_file in file_list:
                    try:
                        png_path = Path(str(java_file.getAbsolutePath()))
                        if not png_path.exists():
                            logger.warning("Exported file does not exist: %s", png_path)
                            continue
                        
                        # Extract character ID from filename (format: "<char_id>.png")
                        try:
                            char_id = int(png_path.stem)
                            target_dict[char_id] = png_path.read_bytes()
                        except ValueError:
                            logger.warning("Could not parse char_id from filename: %s", png_path.name)
                    except Exception as exc:
                        logger.debug("Error processing exported file: %s", exc)
        except Exception as exc:
            logger.error("Failed to export shapes: %s", exc)
            return target_dict

    logger.info("FfdecShapeExport: %d shapes loaded", len(target_dict))
    return target_dict
