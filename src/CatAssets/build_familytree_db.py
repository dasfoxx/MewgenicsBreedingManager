#!/usr/bin/env python3
"""
Build familytree.db from familytree.xml.

Parses the FFDec XML export and creates a SQLite database matching the structure
of catparts.db and catanis_database.db, one table per DefinedSprite.

Usage:
    python build_familytree_db.py
"""

import sqlite3
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Any

# Path to swf_database folder
SWF_DB_PATH = Path(__file__).parent / "swf_database"
XML_FILE = SWF_DB_PATH / "familytree.xml"
DB_FILE = SWF_DB_PATH / "familytree.db"
SYMBOL_CLASS_MAP_FILE = SWF_DB_PATH / "familytree_symbol_class_map.json"


def load_symbol_class_map() -> Dict[int, Dict[str, Any]]:
    """Load the familytree_symbol_class_map.json and convert to sprite_id -> info mapping."""
    with open(SYMBOL_CLASS_MAP_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Convert from {sprite_id_str: {...}} to {sprite_id_int: {...}}
    result = {}
    for sprite_id_str, info in data.items():
        sprite_id = int(sprite_id_str)
        result[sprite_id] = info
    
    return result


def parse_matrix(matrix_elem: Optional[ET.Element]) -> Dict[str, Any]:
    """Parse a MATRIX element to extract scale/translate/rotate data."""
    result = {
        'scale_x': 1.0,
        'scale_y': 1.0,
        'translate_x': 0,
        'translate_y': 0,
        'rotate_skew0': 0.0,
        'rotate_skew1': 0.0,
        'has_scale': False,
        'has_rotate': False,
    }
    
    if matrix_elem is None:
        return result
    
    result['has_scale'] = matrix_elem.get('hasScale', 'false').lower() == 'true'
    result['has_rotate'] = matrix_elem.get('hasRotate', 'false').lower() == 'true'
    
    scale_x = matrix_elem.get('scaleX', '1.0')
    scale_y = matrix_elem.get('scaleY', '1.0')
    translate_x = matrix_elem.get('translateX', '0')
    translate_y = matrix_elem.get('translateY', '0')
    rotate_skew0 = matrix_elem.get('rotateSkew0', '0.0')
    rotate_skew1 = matrix_elem.get('rotateSkew1', '0.0')
    
    try:
        result['scale_x'] = float(scale_x)
        result['scale_y'] = float(scale_y)
        result['translate_x'] = int(translate_x)
        result['translate_y'] = int(translate_y)
        result['rotate_skew0'] = float(rotate_skew0)
        result['rotate_skew1'] = float(rotate_skew1)
    except (ValueError, TypeError):
        pass
    
    return result


def parse_place_object(place_elem: ET.Element, frame_index: int) -> Optional[Dict[str, Any]]:
    """Parse a PlaceObject2Tag or PlaceObject3Tag element.
    
    Returns a dict with frame_index, character_id, depth, name, matrices, and flags.
    """
    tag_type = place_elem.get('type', '')
    
    # Extract basic attributes
    character_id = place_elem.get('characterId')
    if character_id:
        try:
            character_id = int(character_id)
        except (ValueError, TypeError):
            character_id = None
    else:
        character_id = -1  # -1 means no character placed, just modifying existing
    
    depth = place_elem.get('depth')
    if depth:
        try:
            depth = int(depth)
        except (ValueError, TypeError):
            depth = 1
    else:
        depth = 1
    
    name = place_elem.get('name', '') or ''
    
    # Parse matrix if present
    matrix_elem = place_elem.find('matrix')
    matrix_data = parse_matrix(matrix_elem)
    
    # Parse PlaceObject flags
    place_flags = {
        'has_clip_actions': place_elem.get('placeFlagHasClipActions', 'false').lower() == 'true',
        'has_clip_depth': place_elem.get('placeFlagHasClipDepth', 'false').lower() == 'true',
        'has_name': place_elem.get('placeFlagHasName', 'false').lower() == 'true',
        'has_ratio': place_elem.get('placeFlagHasRatio', 'false').lower() == 'true',
        'has_color_transform': place_elem.get('placeFlagHasColorTransform', 'false').lower() == 'true',
        'has_matrix': place_elem.get('placeFlagHasMatrix', 'false').lower() == 'true',
        'has_character': place_elem.get('placeFlagHasCharacter', 'false').lower() == 'true',
        'move': place_elem.get('placeFlagMove', 'false').lower() == 'true',
    }
    
    # Determine if character_id is a DefinedSprite
    character_id_is_definesprite = False
    if character_id > 0:
        # For now, rely on the symbol class map to be set correctly
        # In XML, we don't directly know, so we skip this for familytree.db
        # The accessor code will handle it
        pass
    
    return {
        'frame_index': frame_index,
        'character_id': character_id,
        'depth': depth,
        'name': name,
        'character_id_is_definesprite': character_id_is_definesprite,
        'matrix_scale_x': matrix_data['scale_x'],
        'matrix_scale_y': matrix_data['scale_y'],
        'matrix_translate_x': matrix_data['translate_x'],
        'matrix_translate_y': matrix_data['translate_y'],
        'matrix_rotate_skew0': matrix_data['rotate_skew0'],
        'matrix_rotate_skew1': matrix_data['rotate_skew1'],
        'matrix_has_scale': matrix_data['has_scale'],
        'matrix_has_rotate': matrix_data['has_rotate'],
        'place_flag_has_clip_actions': place_flags['has_clip_actions'],
        'place_flag_has_clip_depth': place_flags['has_clip_depth'],
        'place_flag_has_name': place_flags['has_name'],
        'place_flag_has_ratio': place_flags['has_ratio'],
        'place_flag_has_color_transform': place_flags['has_color_transform'],
        'place_flag_has_matrix': place_flags['has_matrix'],
        'place_flag_has_character': place_flags['has_character'],
        'place_flag_move': place_flags['move'],
    }


def parse_xml(xml_path: Path) -> Dict[int, List[Dict[str, Any]]]:
    """Parse familytree.xml and extract frame data for each DefinedSprite.
    
    Returns a mapping: sprite_id -> list of frame data dicts.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    sprite_frames: Dict[int, List[Dict[str, Any]]] = {}
    
    # Find all DefineSpriteTag elements
    for tag_elem in root.findall('.//item[@type="DefineSpriteTag"]'):
        sprite_id_str = tag_elem.get('spriteId')
        if not sprite_id_str:
            continue
        
        try:
            sprite_id = int(sprite_id_str)
        except (ValueError, TypeError):
            continue
        
        # Extract frames from subTags
        sub_tags = tag_elem.find('subTags')
        if sub_tags is None:
            sprite_frames[sprite_id] = []
            continue
        
        frame_index = 0
        frame_objects = []
        
        for sub_tag_elem in sub_tags.findall('item'):
            tag_type = sub_tag_elem.get('type', '')
            
            # Handle PlaceObject tags
            if tag_type in ('PlaceObject2Tag', 'PlaceObject3Tag'):
                place_data = parse_place_object(sub_tag_elem, frame_index)
                if place_data:
                    frame_objects.append(place_data)
            
            # ShowFrameTag marks end of frame
            elif tag_type == 'ShowFrameTag':
                # Current frame_objects will be saved; increment frame counter
                frame_index += 1
        
        sprite_frames[sprite_id] = frame_objects
    
    return sprite_frames


def create_database(db_path: Path, sprite_frames: Dict[int, List[Dict[str, Any]]], symbol_map: Dict[int, Dict[str, Any]]):
    """Create the SQLite database from parsed frame data."""
    
    # Remove existing database
    if db_path.exists():
        db_path.unlink()
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create sprite_metadata table
    cursor.execute('''
    CREATE TABLE sprite_metadata (
        sprite_id INTEGER PRIMARY KEY,
        table_name TEXT NOT NULL,
        class_name TEXT NOT NULL,
        is_named INTEGER NOT NULL
    )
    ''')
    
    # Insert metadata for each sprite
    for sprite_id, frame_objects in sprite_frames.items():
        sprite_info = symbol_map.get(sprite_id, {})
        class_name = sprite_info.get('class_name', '')
        table_name = sprite_info.get('table_name', '') or f'sprite_{sprite_id}'
        is_named = sprite_info.get('is_named', False)
        
        cursor.execute('''
        INSERT INTO sprite_metadata (sprite_id, table_name, class_name, is_named)
        VALUES (?, ?, ?, ?)
        ''', (sprite_id, table_name, class_name, int(is_named)))
        
        # Create table for this sprite
        create_sprite_table(cursor, table_name)
        
        # Insert frame objects
        if frame_objects:
            insert_frame_objects(cursor, table_name, frame_objects)
    
    conn.commit()
    conn.close()
    print(f"Created {db_path} with {len(sprite_frames)} sprites")


def create_sprite_table(cursor: sqlite3.Cursor, table_name: str):
    """Create a table for a single DefinedSprite."""
    cursor.execute(f'''
    CREATE TABLE [{table_name}] (
        frame_index INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        depth INTEGER NOT NULL,
        name TEXT,
        character_id_is_definesprite INTEGER NOT NULL,
        matrix_scale_x REAL NOT NULL,
        matrix_scale_y REAL NOT NULL,
        matrix_translate_x INTEGER NOT NULL,
        matrix_translate_y INTEGER NOT NULL,
        matrix_rotate_skew0 REAL NOT NULL,
        matrix_rotate_skew1 REAL NOT NULL,
        matrix_has_scale INTEGER NOT NULL,
        matrix_has_rotate INTEGER NOT NULL,
        place_flag_has_clip_actions INTEGER NOT NULL,
        place_flag_has_clip_depth INTEGER NOT NULL,
        place_flag_has_name INTEGER NOT NULL,
        place_flag_has_ratio INTEGER NOT NULL,
        place_flag_has_color_transform INTEGER NOT NULL,
        place_flag_has_matrix INTEGER NOT NULL,
        place_flag_has_character INTEGER NOT NULL,
        place_flag_move INTEGER NOT NULL
    )
    ''')
    # Create index for faster lookups
    # Use double brackets to safely quote table names with dots
    index_name = f"idx_{table_name.replace('.', '_')}_frame"
    cursor.execute(f'CREATE INDEX [{index_name}] ON [{table_name}](frame_index)')


def insert_frame_objects(cursor: sqlite3.Cursor, table_name: str, frame_objects: List[Dict[str, Any]]):
    """Insert frame objects into a sprite's table."""
    for obj in frame_objects:
        cursor.execute(f'''
        INSERT INTO [{table_name}] (
            frame_index, character_id, depth, name, character_id_is_definesprite,
            matrix_scale_x, matrix_scale_y, matrix_translate_x, matrix_translate_y,
            matrix_rotate_skew0, matrix_rotate_skew1, matrix_has_scale, matrix_has_rotate,
            place_flag_has_clip_actions, place_flag_has_clip_depth, place_flag_has_name,
            place_flag_has_ratio, place_flag_has_color_transform, place_flag_has_matrix,
            place_flag_has_character, place_flag_move
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            obj['frame_index'], obj['character_id'], obj['depth'], obj['name'],
            int(obj['character_id_is_definesprite']),
            obj['matrix_scale_x'], obj['matrix_scale_y'], obj['matrix_translate_x'],
            obj['matrix_translate_y'], obj['matrix_rotate_skew0'], obj['matrix_rotate_skew1'],
            int(obj['matrix_has_scale']), int(obj['matrix_has_rotate']),
            int(obj['place_flag_has_clip_actions']), int(obj['place_flag_has_clip_depth']),
            int(obj['place_flag_has_name']), int(obj['place_flag_has_ratio']),
            int(obj['place_flag_has_color_transform']), int(obj['place_flag_has_matrix']),
            int(obj['place_flag_has_character']), int(obj['place_flag_move']),
        ))


def main():
    """Main entry point."""
    print(f"Loading symbol class map from {SYMBOL_CLASS_MAP_FILE}...")
    symbol_map = load_symbol_class_map()
    print(f"Loaded {len(symbol_map)} sprite mappings")
    
    print(f"Parsing {XML_FILE}...")
    sprite_frames = parse_xml(XML_FILE)
    print(f"Found {len(sprite_frames)} sprites in XML")
    
    print(f"Creating database at {DB_FILE}...")
    create_database(DB_FILE, sprite_frames, symbol_map)
    
    print("Done!")


if __name__ == '__main__':
    main()
