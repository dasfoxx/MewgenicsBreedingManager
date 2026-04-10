#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
import io
from PIL import Image
from swf_database import SWFDatabaseAccessor
import numpy as np
from CatAssets.CatAssetsLoader import get_defineshape_png, get_palette_png, ensure_palette_pngdata

logger = logging.getLogger("mewgenics.swf")


# SWF database directory with precomputed sprite frame data
_SWF_DATABASE_DIR = Path(__file__).parent / "CatAssets" / "swf_database"
# Persistent on-disk caches. These are intentionally not deleted on shutdown.
_PART_CACHE_DIR = Path(__file__).parent / "CatAssets" / "part_cache"
_THUMBNAIL_CACHE_DIR = Path(__file__).parent / "CatAssets" / "thumbnail_cache"

_SYMBOL_CLASS_MAP: dict[str, int] = {}
_SYMBOL_CLASS_LOADED = False
_THUMBNAIL_BYTES_CACHE: dict[tuple[str, int], Optional[bytes]] = {}
_PALETTE_IMAGE = None
_LAYER_IMAGE_CACHE: dict[tuple[int, str], Optional[bytes]] = {}
_TEXTURED_LAYER_BYTES_CACHE: dict[tuple[int, int, str], Optional[bytes]] = {}
_TINTED_TEXTURE_CACHE: dict[tuple[int, str], Optional[bytes]] = {}

DEFAULT_TREE_THUMBNAIL_SIZE = 192
THUMBNAIL_CACHE_VERSION = 13
PART_RENDER_CANVAS_W = 570
PART_RENDER_CANVAS_H = 580

# Palette columns inferred from game behavior.
# texture tones: x=6, untextured/base tones: x=3
PALETTE_COL_TEXTURE = 6
PALETTE_COL_BASE = 3
PALETTE_COL_CLASS = 1

_SLOT_PART_NAME: dict[str, str] = {
    # body_parts keys
    "texture":    "CatTexture",
    "bodyShape":  "CatBody",
    "headShape":  "CatHead",
    # visual_mutation_slots keys (same T[] values as body_parts above)
    "fur":        "CatTexture",
    "body":       "CatBody",
    "head":       "CatHead",
    "tail":       "CatTail",
    "leg_L":      "CatLeg",
    "leg_R":      "CatLeg",
    "arm_L":      "CatLeg",   # arms share leg sprites (same fallback_part in save parser)
    "arm_R":      "CatLeg",
    "eye_L":      "CatEye",
    "eye_R":      "CatEye_Right",
    "eyebrow_L":  "CatEyebrow",
    "eyebrow_R":  "CatEyebrow",
    "ear_L":      "CatEar",
    "ear_R":      "CatEar",
    "mouth":      "CatMouth",
}

_LAYERED_PARTS = {"CatBody", "CatHead", "CatLeg", "CatTail"}


def _normalize_matrix_scale(raw_scale, default: int = 65536) -> float:
    """Convert matrix scale from fixed-point 16.16 to float, or just return float as-is.
    
    Handles both:
    - Integers from binary (fixed-point 16.16): 0=missing, 65536=1.0x, etc.
    - Floats from database (already converted): 0.0=missing, 1.0=1.0x, etc.
    
    If raw_scale is 0/0.0 (missing or invalid), treat as 1.0.
    """
    # Convert to float first if int
    if isinstance(raw_scale, int):
        # Integer: fixed-point 16.16 format
        # 0 means no scale data (treat as 1.0)
        if raw_scale == 0:
            return 1.0
        # Very small values also treated as 1.0
        if raw_scale < 100:
            return 1.0
        return raw_scale / float(default)
    else:
        # Already a float from database
        # 0.0 or very small (<0.01) = missing/invalid = use 1.0
        if raw_scale == 0.0 or raw_scale < 0.01:
            return 1.0
        return float(raw_scale)


def calculate_layer_position_within_outline(
    outline_bounds: tuple[float, float, float, float],
    translate_x_twips: float,
    translate_y_twips: float,
    in_twips: bool = True,
) -> tuple[float, float]:
    """
    Calculate the center position of a layer within an outline's PNG coordinate space.
    
    This function positions a smaller object (layer) within the coordinate space defined
    by an outline's bounds. The outline's minimum bounds represent the top-left corner
    (0,0) of the PNG canvas.
    """
    outline_min_x = outline_bounds[0]
    outline_min_y = outline_bounds[1]
    mult = 20.0 if in_twips else 1.0
    translate_x_px = translate_x_twips / mult
    translate_y_px = translate_y_twips / mult
    relative_x = translate_x_px - outline_min_x
    relative_y = translate_y_px - outline_min_y
    
    return (relative_x, relative_y)


def _is_textured_layered_slot(slot: str) -> bool:
    class_name = _SLOT_PART_NAME.get(slot)
    return class_name in _LAYERED_PARTS


def _load_symbol_class_map() -> dict[str, int]:
    global _SYMBOL_CLASS_MAP, _SYMBOL_CLASS_LOADED
    if _SYMBOL_CLASS_LOADED:
        return _SYMBOL_CLASS_MAP
    _SYMBOL_CLASS_LOADED = True

    json_path = _SWF_DATABASE_DIR / "symbol_class_map.json"
    if json_path.exists():
        try:
            import json
            with open(json_path, encoding="utf-8") as f:
                json_data = json.load(f)
            
            # Handle current unified DB format: {sprite_id_str: {class_name, ...}} or {class_name: {sprite_id, ...}}
            for key, value in json_data.items():
                if isinstance(value, dict):
                    # Dict value - extract both sprite_id and class_name
                    class_name = value.get('class_name')
                    sprite_id = value.get('sprite_id')
                    
                    # Try to figure out which one key is based on content
                    try:
                        key_as_int = int(key)
                        # Key is numeric (sprite_id), use value's class_name
                        if class_name:
                            _SYMBOL_CLASS_MAP[class_name] = key_as_int
                    except ValueError:
                        # Key is string (class_name), use value's sprite_id
                        if sprite_id is not None:
                            _SYMBOL_CLASS_MAP[key] = int(sprite_id)
                else:
                    # Old format: assume value is char_id (int or str)
                    try:
                        _SYMBOL_CLASS_MAP[key] = int(value)
                    except (ValueError, TypeError):
                        pass
            
            logger.info("[SWF] Loaded %d SymbolClass entries from %s", len(_SYMBOL_CLASS_MAP), json_path)
            return _SYMBOL_CLASS_MAP
        except Exception as e:
            logger.warning("[SWF] Failed to load symbol_class_map.json: %s", e)
    
    # Always return the symbol class map (empty if loading failed)
    return _SYMBOL_CLASS_MAP


def get_sprite_id(class_name: str) -> Optional[int]:
    """Look up a DefinedSprite character_id by its symbolClass name."""
    return _load_symbol_class_map().get(class_name)

def _apply_matrix_scale(img, m):
    sx = m.get("scaleX",1)
    sy = m.get("scaleY",1)
    sx = _normalize_matrix_scale(sx)
    sy = _normalize_matrix_scale(sy)
    if sx != 1 or sy != 1:
        img = img.resize((int(img.width*sx), int(img.height*sy)), Image.Resampling.LANCZOS)
    return img

def get_shape_bounds(layer, db: "SWFDatabaseAccessor") -> Optional[tuple[float, float, float, float]]:
    if not layer:
        return None
    
    cid = layer.get("characterId")
    if not cid:
        return None
    
    bounds = db.get_shape_bounds(cid)
    if not bounds:
        return None
    bounds_min_x = bounds.get('bounds_x_min', 0) / 20.0
    bounds_min_y = bounds.get('bounds_y_min', 0) / 20.0
    bounds_max_x = bounds.get('bounds_x_max', 0) / 20.0
    bounds_max_y = bounds.get('bounds_y_max', 0) / 20.0
    
    width = bounds_max_x - bounds_min_x
    height = bounds_max_y - bounds_min_y
    
    return (bounds_min_x, bounds_min_y, width, height)

def _render_texture_frame(texture_id: int, palette_row: int = 0) -> Optional[bytes]:
    try:
        db = SWFDatabaseAccessor()
        layers_all = db.get_frame_objects("CatTexture", texture_id - 1)
        
        if not layers_all:
            return None
        
        texture_width = 150
        texture_height = 150
        bounds = [texture_width / 2, texture_height / 2, texture_width, texture_height]
        layers = sorted(layers_all, key=lambda x: x.get("depth",0))
        canvas = Image.new("RGBA", (texture_width, texture_height), (0,0,0,0))
        for layer in layers:
            if layer is not None:
                og_layer = layer
                depth = layer.get("depth", 0)
                char_id = layer.get("characterId")
                if char_id:
                    if layer.get("characterIdIsDefinesprite"):
                        layer = _get_nested_shape(layer, db)
                        char_id = layer.get("characterId")
                    data = _render_shape(char_id)
                    im = Image.open(io.BytesIO(data)).convert("RGBA")
                    relative_x, relative_y = 0,0
                    matrix = og_layer.get("matrix", {})
                    if matrix:
                        if matrix.get("hasScale", False):
                            im = _apply_matrix_scale(im, matrix)
                        tx_twips = matrix.get("translateX", 0) 
                        ty_twips = matrix.get("translateY", 0)
                        if abs(tx_twips) > 1 or abs(ty_twips) > 1:
                            relative_x, relative_y = calculate_layer_position_within_outline(bounds, tx_twips, ty_twips)
                        if int(tx_twips) == 0 and int(ty_twips) == 0:
                            detail_bounds = get_shape_bounds(og_layer, db)
                            relative_x, relative_y = detail_bounds[0] + bounds[0], detail_bounds[1] + bounds[1]
                        
                    palette_cols = [1, 3, 5]
                    palette_col = palette_cols[min(depth - 1, len(palette_cols) - 1)]
                    palette_rgb = _palette_color(palette_row, palette_col)
                    if palette_rgb:
                        im = _apply_monochrome_tint(im, palette_rgb, preserve_dark=-1)
                    canvas.alpha_composite(im, (int(relative_x), int(relative_y)))
        # Convert to PNG bytes
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
        
    except Exception as e:
        logger.debug("[SWF] Error rendering texture frame %d: %s", texture_id, e)
        return None


def _render_shape(character_id: int) -> Optional[bytes]:
    """
    Load a DefinedShape PNG from _DEFINESHAPE_PNGDATA (in-memory cache).
    Falls back to disk if not in memory.
    """
    cache_key = (character_id, "shape")
    if cache_key in _LAYER_IMAGE_CACHE:
        return _LAYER_IMAGE_CACHE[cache_key]

    # Try to load from in-memory _DEFINESHAPE_PNGDATA first
    data = get_defineshape_png(character_id)
    if data is not None:
        _LAYER_IMAGE_CACHE[cache_key] = data
        return data

    # Fallback: try to load from disk DefinedShapes folder
    defined_shapes_dir = Path(__file__).parent / "CatAssets" / "DefinedShapes"
    shape_path = defined_shapes_dir / f"{character_id}.png"
    
    if shape_path.exists():
        try:
            data = shape_path.read_bytes()
            _LAYER_IMAGE_CACHE[cache_key] = data
            return data
        except Exception as e:
            logger.error("[SWF] Failed to load DefinedShape %d: %s", character_id, e)
            _LAYER_IMAGE_CACHE[cache_key] = None
            return None
    
    logger.debug("[SWF] DefinedShape %d not found in memory or on disk", character_id)
    _LAYER_IMAGE_CACHE[cache_key] = None
    return None

def _replace_canvas_pixels_with_texture(
    canvas,
    texture_image,
    preserve_dark: int = 20,
):
    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")
    if texture_image.mode != "RGBA":
        texture_image = texture_image.convert("RGBA")

    canvas_pixels = list(canvas.getdata())
    texture_pixels = list(texture_image.getdata())
    out_pixels = list(canvas_pixels)

    for canvas_y in range(canvas.height):
        if canvas_y < 0 or canvas_y >= texture_image.height:
            continue

        row_offset = canvas_y * canvas.width
        tex_row_offset = canvas_y * texture_image.width
        for canvas_x in range(canvas.width):
            idx = row_offset + canvas_x
            base_r, base_g, base_b, base_a = canvas_pixels[idx]
            if base_a == 0:
                continue

            lum = (base_r + base_g + base_b) / 3.0
            if lum <= preserve_dark:
                continue

            if canvas_x < 0 or canvas_x >= texture_image.width:
                continue

            tex_r, tex_g, tex_b, tex_a = texture_pixels[tex_row_offset + canvas_x]
            if tex_a == 0:
                continue

            out_pixels[idx] = (tex_r, tex_g, tex_b, base_a)

    result = canvas.copy()
    result.putdata(out_pixels)
    return result


def _texture_cache_digest(texture_data: bytes) -> str:
    return hashlib.sha1(texture_data).hexdigest()[:16]


def _tinted_texture_cache_key(texture_id: int, key_tag: str) -> tuple[int, str]:
    """Stable cache key based on texture id + semantic source tag, not resolved RGB."""
    return int(texture_id), key_tag


def _get_tinted_texture_png(
    texture_id: int,
    palette_row: Optional[int] = None,
    key_tag: str = "raw",
) -> Optional[bytes]:
    """
    Return a cached texture PNG with depth-layered colors from palette.
    
    CatTexture frames have multiple layers (depths) that are each tinted with
    different palette colors from the specified palette_row.
    
    key_tag must uniquely describe the palette source:
      - "class{row}" for class palette row
      - "pal{palette_idx}" for base palette index
      - "raw" for untinted
    """
    cache_key = _tinted_texture_cache_key(texture_id, key_tag)
    if cache_key in _TINTED_TEXTURE_CACHE:
        return _TINTED_TEXTURE_CACHE[cache_key]

    _PART_CACHE_DIR.mkdir(exist_ok=True)
    disk_path = _PART_CACHE_DIR / f"texture_{cache_key[0]}_{cache_key[1]}.png"
    if disk_path.exists():
        data = disk_path.read_bytes()
        _TINTED_TEXTURE_CACHE[cache_key] = data
        return data

    # Render texture frame with depth-layered tinting from palette row
    texture_png = _render_texture_frame(texture_id, palette_row=palette_row or 0)
    if not texture_png:
        _TINTED_TEXTURE_CACHE[cache_key] = None
        return None

    _TINTED_TEXTURE_CACHE[cache_key] = texture_png
    disk_path.write_bytes(texture_png)
    return texture_png

def _get_nested_shape(frame_layer: int, db: SWFDatabaseAccessor) -> Optional[bytes]:
    frame_data = frame_layer
    max_nesting_depth = 5
    nesting_count = 0
    while frame_data.get("characterIdIsDefinesprite") and nesting_count < max_nesting_depth:
        data_char_id = frame_data.get("characterId")
        sprite_class_name = None
        for class_name, char_id in _load_symbol_class_map().items():
            if char_id == data_char_id:
                sprite_class_name = class_name
                break
        frame_objects = db.get_frame_objects(sprite_class_name, 0)
        frame_data = frame_objects[0]
        if not sprite_class_name:
            logger.debug("[SWF] No SymbolClass mapping for character_id %d", data_char_id)
            return None
        if frame_data is None:
            logger.debug("[SWF] No frame data mapping for frame_data %d", frame_data)
            return None
        nesting_count += 1
    if nesting_count >= max_nesting_depth:
        logger.warning("[SWF] Reached max nesting depth (%d) for character %d", max_nesting_depth, data_char_id)
        return None
    return frame_data

def _create_part(part_name: str, frame_id: int, texture_data: Optional[bytes] = None) -> tuple[Optional[bytes], bool]:
    """
    Render a cat part and return (png_bytes, has_texture_layer).
    has_texture_layer indicates if the part definition has a texture layer (not whether texture_data was applied).
    """
    try:
        db = SWFDatabaseAccessor()
        layers_all = db.get_frame_objects(part_name, frame_id - 1)
        if not layers_all:
            logger.debug("[SWF] No layers found for %s frame %d", part_name, frame_id)
            return None, False
        
        if (layers_all[0].get("depth") > 1 and len(layers_all) > 1):
            layers_all.remove(layers_all[0])
        layers = sorted(layers_all, key=lambda x: x.get("depth",0))

        base = next((l for l in layers if not l.get("name")), None)
        texture = next((l for l in layers if (l.get("name") or "").lower() in {"tex"}), None)
        has_texture_layer = texture is not None
        scars = next((l for l in layers if (l.get("name") or "").lower() == "scars"), None)
        aux = next((l for l in layers if (l.get("name") or "").lower() == "aux"), None)
        outline = next((l for l in layers if not l.get("name") and l != base), None)
        details = None
        if outline is not None:
            outline_idx = layers.index(outline)
            details = [l for l in layers[outline_idx + 1:] if not l.get("name")]

        basebounds = None
        basecanvas = Image.new("RGBA", (1, 1), (0,0,0,0))
        canvas = Image.new("RGBA", (1, 1), (0,0,0,0))
        if base is not None:
            char_id = base.get("characterId")
            if char_id:
                if base.get("characterIdIsDefinesprite"):
                    base = _get_nested_shape(base, db)
                    char_id = base.get("characterId")
                basebounds = get_shape_bounds(base, db)
                basecanvas = Image.new("RGBA", (int(basebounds[2]), int(basebounds[3])), (0,0,0,0))
                data = _render_shape(char_id)
                im = Image.open(io.BytesIO(data)).convert("RGBA")
                basecanvas.alpha_composite(im, (0, 0))
                if texture is not None and texture_data is not None:
                    texture_image = Image.open(io.BytesIO(texture_data)).convert("RGBA")
                    basecanvas = _replace_canvas_pixels_with_texture(
                    basecanvas, texture_image,
                    preserve_dark=20)      
        if outline is not None:
            char_id = outline.get("characterId")
            if char_id:
                if outline.get("characterIdIsDefinesprite"):
                    outline = _get_nested_shape(outline, db)
                    char_id = outline.get("characterId")
                bounds = get_shape_bounds(outline, db)
                canvas = Image.new("RGBA", (int(bounds[2]), int(bounds[3])), (0,0,0,0))
                data = _render_shape(char_id)
                im = Image.open(io.BytesIO(data)).convert("RGBA")
                relative_x, relative_y = calculate_layer_position_within_outline(bounds, basebounds[0], basebounds[1], False)
                canvas.alpha_composite(basecanvas, (int(relative_x), int(relative_y)))
                canvas.alpha_composite(im, (0, 0))
        if details is not None and len(details) > 0:
            for detail in details:
                detail_base = detail
                char_id = detail.get("characterId")
                if char_id:
                    if detail.get("characterIdIsDefinesprite"):
                        detail = _get_nested_shape(detail, db)
                        char_id = detail.get("characterId")
                    data = _render_shape(char_id)
                    im = Image.open(io.BytesIO(data)).convert("RGBA")
                    relative_x, relative_y = 0, 0
                    matrix = detail_base.get("matrix", {})
                    if matrix:
                        im = _apply_matrix_scale(im, matrix)
                        tx_twips = matrix.get("translateX", 0)
                        ty_twips = matrix.get("translateY", 0)
                        relative_x, relative_y = calculate_layer_position_within_outline(bounds, tx_twips, ty_twips)
                        relative_x = relative_x - im.width / 2
                        relative_y = relative_y - im.height / 2
                    else:
                        detail_bounds = get_shape_bounds(detail, db)
                        relative_x, relative_y = calculate_layer_position_within_outline(bounds, detail_bounds[0], detail_bounds[1])
                    canvas.alpha_composite(im, (int(relative_x), int(relative_y)))
        if outline is None:
            canvas = basecanvas
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue(), has_texture_layer
    
    except Exception as e:
        logger.exception("[SWF] Failed to render layered part %s[%d]: %s", part_name, frame_id, e)
        return None, False

def render_cat_part(slot: str, part_id: int, size: int = 128, texture_data: Optional[bytes] = None, palette_rgb: Optional[tuple[int, int, int]] = None) -> Optional[bytes]:
    """
    Render one cat body part slot to PNG bytes.

    Args:
        slot:    Save-blob slot key (e.g. 'bodyShape', 'eye_L', 'mouth')
        part_id: Integer from cat.body_parts or cat.visual_mutation_slots - used as frame index
        texture_data: Optional PNG bytes to use for 'tex' layer in layered parts
        palette_rgb: Optional RGB tuple to tint base layers

    Returns:
        PNG bytes, or None if slot/frame is unavailable.
    """
    if part_id is None or part_id == 0 or part_id >= 0xFFFFFFFE:
        return None

    part_name = _SLOT_PART_NAME.get(slot)
    if not part_name:
        logger.debug("[SWF] No class mapping for slot '%s'", slot)
        return None

    sprite_chid = get_sprite_id(part_name)
    if sprite_chid is None:
        logger.debug("[SWF] '%s' not in SymbolClass - run dump_symbol_classes() to verify names",
                     part_name)
        return None
    
    # Check cache for both textured and non-textured versions
    use_texture = texture_data is not None
    
    if not use_texture:
        # Non-textured - check memory cache first
        cache_key = (sprite_chid, part_id, "untextured-v1")
        if cache_key in _LAYER_IMAGE_CACHE:
            return _normalize_part_to_center_origin(_LAYER_IMAGE_CACHE[cache_key])
    else:
        # Textured - check memory and disk cache
        texture_digest = _texture_cache_digest(texture_data)
        cache_key = (sprite_chid, part_id, texture_digest)
        if cache_key in _TEXTURED_LAYER_BYTES_CACHE:
            return _TEXTURED_LAYER_BYTES_CACHE[cache_key]
        _PART_CACHE_DIR.mkdir(exist_ok=True)
        disk_path = _PART_CACHE_DIR / f"part_{sprite_chid}_{part_id}_{texture_digest}.png"
        if disk_path.exists():
            cached = disk_path.read_bytes()
            _TEXTURED_LAYER_BYTES_CACHE[cache_key] = cached
            return cached
    
    # Render the part (and get info about whether it has a texture layer)
    rendered, has_texture_layer = _create_part(part_name, part_id, texture_data=texture_data)
    
    if rendered is None:
        logger.warning("[SWF] Failed to render part %s[%d]", part_name, part_id)
        return None
    
    # Cache based on what was actually rendered
    if not has_texture_layer:
        cache_key = (sprite_chid, part_id, "untextured-v1")
        _LAYER_IMAGE_CACHE[cache_key] = rendered
        logger.debug("[SWF] Cached non-textured part %s[%d]", part_name, part_id)
    else:
        texture_digest = _texture_cache_digest(texture_data)
        cache_key = (sprite_chid, part_id, texture_digest)
        _TEXTURED_LAYER_BYTES_CACHE[cache_key] = rendered
        disk_path.write_bytes(rendered)
        logger.debug("[SWF] Cached textured part %s[%d]", part_name, part_id)
    
    return rendered

_THUMBNAIL_SLOT_ORDER = [
    "texture", 
    "tail",
    "leg_L",
    "arm_L",
    "bodyShape",
    "leg_R",
    "arm_R",
    "headShape",
]
_HEAD_DETAIL_SPRITE_CHID: dict[str, int] = {
    "lear": 10988,
    "rear": 10990,
    "leye": 10994,
    "mouth": 10998,
    "reye": 11000,
    "ahead": 11002,
    "aneck": 11004,
    "aface": 11006,
}
_CHAR_ID_TO_HEAD_DETAIL: dict[int, str] = {v: k for k, v in _HEAD_DETAIL_SPRITE_CHID.items()}

# Map abbreviated _HEAD_DETAIL_DEPTHS names to save_parser slot names
_HEAD_DETAIL_DEPTH_TO_SLOT: dict[str, str] = {
    "lear": "ear_L",
    "rear": "ear_R",
    "leye": "eye_L",
    "reye": "eye_R",
    "mouth": "mouth",
    "ahead": None,  # accessories handled separately if needed
    "aneck": None,
    "aface": None,
}

def _load_palette_image():
    global _PALETTE_IMAGE
    if _PALETTE_IMAGE is not None:
        return _PALETTE_IMAGE
    
    # Try to load from cached GPAK extraction first
    ensure_palette_pngdata()
    palette_bytes = get_palette_png()
    if palette_bytes:
        try:
            _PALETTE_IMAGE = Image.open(io.BytesIO(palette_bytes)).convert("RGBA")
            return _PALETTE_IMAGE
        except Exception:
            logger.exception("[SWF] Failed loading palette image from cache")
            _PALETTE_IMAGE = False
            return None

def _palette_color(row: int, col: int) -> tuple[int, int, int] | None:
    im = _load_palette_image()
    if im is None:
        return None
    if row is None:
        return None

    y = max(0, min(im.height - 1, int(row)))
    x = max(0, min(im.width - 1, int(col)))
    r, g, b, _a = im.getpixel((x, y))
    return int(r), int(g), int(b)


def _apply_monochrome_tint(layer, rgb: tuple[int, int, int], preserve_dark: int = 20):
    """
    Recolor grayscale SWF exports while keeping line-art dark pixels intact.
    Uses vectorized numpy operations for ~50-100x speedup vs pixel-by-pixel.
    """

    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")

    # Convert to numpy array with float32 for computation
    data = np.array(layer, dtype=np.float32)
    
    # Extract channels
    r, g, b, a = data[:,:,0], data[:,:,1], data[:,:,2], data[:,:,3]
    
    # Calculate luminance (vectorized)
    lum = (r + g + b) / 3.0
    
    # Apply tint factor: 0.35 + 0.65 * min(1.0, lum / 96.0)
    factor = 0.35 + 0.65 * np.minimum(1.0, lum / 96.0)
    
    # Mask for pixels to preserve (alpha=0 or luminance <= preserve_dark)
    preserve_mask = (a == 0) | (lum <= preserve_dark)
    
    # Apply tint to non-preserved pixels
    out_r = np.where(preserve_mask, r, rgb[0] * factor)
    out_g = np.where(preserve_mask, g, rgb[1] * factor)
    out_b = np.where(preserve_mask, b, rgb[2] * factor)
    
    # Clamp to 0-255 and convert back to uint8
    out_rgba = np.stack([
        np.clip(out_r, 0, 255).astype(np.uint8),
        np.clip(out_g, 0, 255).astype(np.uint8),
        np.clip(out_b, 0, 255).astype(np.uint8),
        a.astype(np.uint8),
    ], axis=2)
    
    return Image.fromarray(out_rgba, "RGBA")


def _normalize_part_to_center_origin(png_bytes: Optional[bytes]) -> Optional[bytes]:
    if not png_bytes:
        return png_bytes
    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return png_bytes

    if image.width == PART_RENDER_CANVAS_W and image.height == PART_RENDER_CANVAS_H:
        return png_bytes

    canvas = Image.new("RGBA", (PART_RENDER_CANVAS_W, PART_RENDER_CANVAS_H), (0, 0, 0, 0))
    dest_x = (PART_RENDER_CANVAS_W - image.width) // 2
    dest_y = (PART_RENDER_CANVAS_H - image.height) // 2
    canvas.alpha_composite(image, (dest_x, dest_y))

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _get_effective_parts(cat) -> dict[str, int]:
    """Build a parts dict with variant fallback: if primary texture is 0, use variant.
    
    Variant textures represent the base/default limb. When no mutation is present (primary=0),
    the variant texture should be used for rendering the base form.
    
    Includes all body parts AND head detail parts.
    """
    parts: dict[str, int] = {}
    
    # Merge body_parts and visual_mutation_slots
    if hasattr(cat, "body_parts"):
        parts.update(cat.body_parts)
    if hasattr(cat, "visual_mutation_slots"):
        parts.update(cat.visual_mutation_slots)
    
    # Apply variant fallback for each slot in _SLOT_PART_NAME
    # (includes body parts, head details, and special slots like texture)
    if hasattr(cat, "visual_mutation_variant_slots"):
        variant_slots = cat.visual_mutation_variant_slots
        for slot_name in _SLOT_PART_NAME.keys():
            if slot_name in ("texture", "class_palette_index", "base_palette_index"):
                continue  # Skip non-body-part slots
            
            primary = parts.get(slot_name)
            # If primary is 0 or a sentinel "no part" value, use variant as base
            if not primary or primary == 0 or primary >= 0xFFFFFFFE:
                variant_slot = f"{slot_name}_variant"
                variant = variant_slots.get(variant_slot)
                if variant and variant != 0 and variant < 0xFFFFFFFE:
                    parts[slot_name] = variant
                else:
                    # No valid variant — remove sentinel so renderer skips this slot
                    parts.pop(slot_name, None)
    
    return parts


def _thumbnail_signature(cat) -> str:
    parts = _get_effective_parts(cat)

    signature_parts: list[str] = []
    for slot in _THUMBNAIL_SLOT_ORDER:
        value = parts.get(slot)
        if value is not None:
            signature_parts.append(f"{slot}:{value}")

    palette_idx = getattr(cat, "base_palette_index", None) 
    if palette_idx is not None:
        signature_parts.append(f"palette:{palette_idx}")

    class_row = getattr(cat, "class_palette_index", None)
    if class_row is not None:
        signature_parts.append(f"class_row:{class_row}")

    signature_parts.append(f"version:{THUMBNAIL_CACHE_VERSION}")
    return "|".join(signature_parts)


def render_cat_thumbnail(cat, size: int = DEFAULT_TREE_THUMBNAIL_SIZE) -> Optional[bytes]:
    """
    Compose a cat thumbnail by layering body-sized SWF sprite frames with PIL.

    Uses cat.body_parts and cat.visual_mutation_slots for part IDs.
    Returns PNG bytes scaled to `size` px, or None if nothing rendered.
    """
    signature = _thumbnail_signature(cat)
    if not signature:
        return None

    cache_key = (signature, size)
    if cache_key in _THUMBNAIL_BYTES_CACHE:
        return _THUMBNAIL_BYTES_CACHE[cache_key]

    _THUMBNAIL_CACHE_DIR.mkdir(exist_ok=True)
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
    disk_path = _THUMBNAIL_CACHE_DIR / f"{digest}_{size}.png"
    if disk_path.exists():
        data = disk_path.read_bytes()
        _THUMBNAIL_BYTES_CACHE[cache_key] = data
        return data

    parts = _get_effective_parts(cat)

    palette_idx = getattr(cat, "base_palette_index", None) 
    class_row = getattr(cat, "class_palette_index", None) 

    if class_row is not None:
        # Use class row for texture tinting
        tex_palette_row = class_row
        tex_key_tag = f"class{class_row}"
    elif palette_idx is not None:
        # Use base palette index as row for texture tinting
        tex_palette_row = palette_idx
        tex_key_tag = f"pal{palette_idx}"
    else:
        tex_palette_row = None
        tex_key_tag = "raw"

    #Temp. Hardcoded to sprite_19 catanis size
    CANVAS_W, CANVAS_H = 173, 149
    #CANVAS_W, CANVAS_H = 570, 570
    thumbnail_bounds = [-74.35, -146.00, CANVAS_W, CANVAS_H]
    
    # Create larger canvas with buffers to prevent parts from being cut off
    buffer_left, buffer_top = 100, 100
    buffer_right, buffer_bottom = 50, 50
    canvas_w_buffered = CANVAS_W + buffer_left + buffer_right
    canvas_h_buffered = CANVAS_H + buffer_top + buffer_bottom
    canvas = Image.new("RGBA", (canvas_w_buffered, canvas_h_buffered), (0, 0, 0, 0))

    texture_id = parts.get("texture")
    cached_texture_png = None
    if texture_id:
        cached_texture_png = _get_tinted_texture_png(texture_id, palette_row=tex_palette_row, key_tag=tex_key_tag)

    rendered_any = False
    head_part_id = None
    headcanvas = Image.new("RGBA", (1, 1), (0,0,0,0))
    head_position = (0, 0)  # Track where to composite the head with details
    ear_canvas = Image.new("RGBA", (1, 1), (0,0,0,0))
    ear_position = (0, 0)  # Track where to composite the ears
    # Render main body parts (including headShape)
    for slot in _THUMBNAIL_SLOT_ORDER:
        if slot == "texture":
            continue

        part_id = parts.get(slot)
        if not part_id or part_id >= 0xFFFFFFFE:
            continue

        # Save head part_id for later head detail placement lookup
        if slot == "headShape":
            head_part_id = part_id
        
        # For layered parts with texture, pass texture data to render
        png_bytes = None
        if _is_textured_layered_slot(slot) and cached_texture_png:
            # Don't apply base tint if texture is being applied
            png_bytes = render_cat_part(slot, part_id, texture_data=cached_texture_png)
        
        # Fallback to normal render if no texture or not a layered part
        if png_bytes is None:
            png_bytes = render_cat_part(slot, part_id)
        
        if not png_bytes:
            continue
        layer = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

        # Render using catanis animation database with matrix-based positioning
        db_anis = SWFDatabaseAccessor()
        db_anis.set_active_database("catanis")
        db = SWFDatabaseAccessor()
        # Get animation frame 0 (FlatCat sprite) - contains all parts with matrix positioning
        try:
            animation_layers = db_anis.get_frame_objects("sprite_10", 0)
            
            if animation_layers:
                # Sort by depth to maintain correct layering (back to front)
                sorted_layers = sorted(animation_layers, key=lambda x: x.get("depth", 0))
                
                #TODO using sprite_10 frame 0 catanis which has named layers, others don't
                for layer_data in sorted_layers:
                    layer_name = layer_data.get("name", "").lower()
                    if not layer_name:
                        continue
                    
                    # Map animation layer names to our slot names
                    slot_mapping = {
                        "tail": "tail",
                        "leg2": "leg_R",
                        "leg1": "leg_L",
                        "arm2": "arm_R",
                        "arm1": "arm_L",
                        "body": "bodyShape",
                        "head": "headShape",
                    }
                    
                    slot_name = slot_mapping.get(layer_name)
                    if not slot_name:
                        continue
                    
                    # Get part_id for this slot
                    part_id = parts.get(slot_name)
                    if not part_id or part_id >= 0xFFFFFFFE:
                        continue

                    # Render the part
                    png_bytes = None
                    if _is_textured_layered_slot(slot_name) and cached_texture_png:
                        png_bytes = render_cat_part(slot_name, part_id, texture_data=cached_texture_png)
                    
                    if png_bytes is None:
                        png_bytes = render_cat_part(slot_name, part_id)
                    
                    if not png_bytes:
                        continue
                    
                    layer = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                    
                    matrix = layer_data.get("matrix", {})
                    if matrix.get("hasScale"):
                        layer = _apply_matrix_scale(layer, matrix)
                    translate_x = matrix.get("translateX", 0)
                    translate_y = matrix.get("translateY", 0)
                    relative_x, relative_y = calculate_layer_position_within_outline(thumbnail_bounds, translate_x, translate_y)
                    layers_all = db.get_frame_objects(_SLOT_PART_NAME.get(slot_name), part_id - 1)
                    if not layers_all:
                        logger.debug("[SWF] No layers found for %s frame %d", slot_name, part_id - 1)
                        return None
                    layers = sorted(layers_all, key=lambda x: x.get("depth",0))
                    base = next((l for l in layers if not l.get("name")), None)
                    outline = next((l for l in layers if not l.get("name") and l != base), None)
                    bounds = None
                    if outline:
                        if outline.get("characterIdIsDefinesprite"):
                            outline = _get_nested_shape(outline, db)
                        bounds = get_shape_bounds(outline, db)
                    else:
                        if base.get("characterIdIsDefinesprite"):
                            base = _get_nested_shape(base, db)
                        bounds = get_shape_bounds(base, db)
                    relative_x = relative_x + bounds[0]
                    relative_y = relative_y + bounds[1]
                    # Get rotation data if available
                    # rotateSkew0 = matrix.get("rotateSkew0", 0)
                    # rotateSkew1 = matrix.get("rotateSkew1", 0)
                    # TODO: Apply rotation transform if needed
                    
                    # Apply buffer offset to position
                    canvas_x = int(relative_x) + buffer_left
                    canvas_y = int(relative_y) + buffer_top
                    
                    if slot_name == "headShape" and head_part_id is not None:
                        # Render head to head_canvas with buffer-aware sizing
                        if headcanvas.size == (1, 1):
                            # Initialize headcanvas with buffered size
                            headcanvas = Image.new("RGBA", (canvas_w_buffered, canvas_h_buffered), (0, 0, 0, 0))
                        headcanvas.alpha_composite(layer, (canvas_x, canvas_y))
                        head_position = (canvas_x, canvas_y)
                    else:
                        canvas.alpha_composite(layer, (canvas_x, canvas_y))
                    rendered_any = True
        except Exception as e:
            logger.debug("[SWF] Failed to render from catanis sprite_10 animation: %s", e)
    
    # Render head detail pieces using CatHeadPlacement positioning
    if head_part_id:
        try:
            db = SWFDatabaseAccessor()
            placement_layers = db.get_frame_objects("CatHeadPlacements", head_part_id - 1)
            head_layers = db.get_frame_objects("CatHead", head_part_id - 1)
            
            if placement_layers and head_layers:
                # Find outline and base layers to get head bounds
                base = next((l for l in placement_layers if not l.get("name")), None)
                outline = next((l for l in placement_layers if not l.get("name") and l != base), None)
                if outline:
                    if outline.get("characterIdIsDefinesprite"):
                        outline = _get_nested_shape(outline, db)
                    head_bounds = get_shape_bounds(outline, db)
                else:
                    if base.get("characterIdIsDefinesprite"):
                        base = _get_nested_shape(base, db)
                    head_bounds = get_shape_bounds(base, db)
                # Ensure headcanvas is initialized as a proper canvas if not already
                if headcanvas.size == (1, 1):
                    headcanvas = Image.new("RGBA", (canvas_w_buffered, canvas_h_buffered), (0, 0, 0, 0))
                
                # Get characterIds from head_layers to identify which layers are part of the head
                head_char_ids = {l.get("characterId") for l in head_layers if l.get("characterId")}
                
                # Find the first layer in placement_layers that's NOT in head_layers
                detail_layers_start_idx = None
                for idx, layer in enumerate(placement_layers):
                    if layer.get("characterId") not in head_char_ids:
                        detail_layers_start_idx = idx
                        break
                
                # If we found where details start, iterate through them
                if detail_layers_start_idx is not None:
                    detail_layers = placement_layers[detail_layers_start_idx:]
                    
                    # Iterate through detail layers, mapping each to _HEAD_DETAIL_SPRITE_CHID
                    for layer_data in detail_layers:
                        abbrev_name = _CHAR_ID_TO_HEAD_DETAIL.get(layer_data.get("characterId"))
                        if not abbrev_name:
                            continue
                        # Map abbreviated name to save_parser slot name
                        slot_name = _HEAD_DETAIL_DEPTH_TO_SLOT.get(abbrev_name)
                        if not slot_name:
                            continue
                        
                        # Get the part_id for this detail slot from parts dict
                        detail_part_id = parts.get(slot_name)

                        # Use fallback frame ID 1 if detail is missing (for eyes, ears, etc. that should always exist)
                        if not detail_part_id or detail_part_id >= 0xFFFFFFFE:
                            detail_part_id = 1
                        
                        # Render the detail part
                        detail_png = render_cat_part(slot_name, detail_part_id, texture_data=cached_texture_png)
                        if not detail_png:
                            continue
                        detail_layer = Image.open(io.BytesIO(detail_png)).convert("RGBA")
                        
                        # Get position from matrix (convert twips to pixels: twips / 20 = pixels)
                        matrix = layer_data.get('matrix', {})
                        if matrix.get("hasScale"):
                            detail_layer = _apply_matrix_scale(detail_layer, matrix)
                        
                        translate_x = matrix.get("translateX", 0)
                        translate_y = matrix.get("translateY", 0)
                        relative_x, relative_y = calculate_layer_position_within_outline(head_bounds, translate_x, translate_y)
                        relative_x = relative_x - detail_layer.width / 2
                        relative_y = relative_y - detail_layer.height / 2
                        
                        # Apply buffer offset for head details positioned relative to head_bounds (which is relative to thumbnail_bounds)
                        detail_canvas_x = int(relative_x) + head_position[0]
                        detail_canvas_y = int(relative_y) + head_position[1]
                        
                        # Render ears to ear_canvas, other details to headcanvas
                        if slot_name == "ear_L" or slot_name == "ear_R":
                            if ear_canvas.size == (1, 1):
                                # Initialize ear_canvas with buffered size
                                ear_canvas = Image.new("RGBA", (canvas_w_buffered, canvas_h_buffered), (0, 0, 0, 0))
                            # Flip ear_R horizontally
                            if slot_name == "ear_R":
                                detail_layer = detail_layer.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                            ear_canvas.alpha_composite(detail_layer, (detail_canvas_x, detail_canvas_y))
                        else:
                            headcanvas.alpha_composite(detail_layer, (detail_canvas_x, detail_canvas_y))
        except Exception as e:
            logger.debug("[SWF] Failed to render head details: %s", e)
    
    # Composite the fully-rendered head (shape + details) back onto the main canvas at the correct position
    # Layering order: body parts → ears → head shape + head details
    if ear_canvas.size != (1, 1):
        canvas.alpha_composite(ear_canvas, (0, 0))
    if head_part_id and headcanvas.size != (1, 1):
        canvas.alpha_composite(headcanvas, (0, 0))

    if not rendered_any:
        _THUMBNAIL_BYTES_CACHE[cache_key] = None
        return None

    # Scale only at the very end to final thumbnail size
    # Use the full buffered canvas (don't crop) to maintain consistent size across all thumbnails for UI matching
    canvas.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    data = buf.getvalue()
    disk_path.write_bytes(data)
    _THUMBNAIL_BYTES_CACHE[cache_key] = data
    return data
