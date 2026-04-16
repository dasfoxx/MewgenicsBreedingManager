#!/usr/bin/env python3
"""
Catanis Animation Database Builder

Parses 2d_catanis.xml and builds a unified SQLite database for all DefinedSprites,
mapping frame indices to lists of PlaceObjects with MATRIX data including rotation
(rotateSkew0 and rotateSkew1).

The unified catanis_database.db contains:
  - One table per DefinedSprite (named or unnamed)
  - sprite_metadata table for sprite lookups
  - Frame data with full MATRIX transformation (scale, rotation, translation)
"""

import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger("catanis_db_builder")
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


class CatanicsDatabaseBuilder:
    """Builds unified catanis_database.db for 2d_catanis.xml animations"""
    
    def __init__(self, xml_path: Path, db_dir: Path):
        self.xml_path = xml_path
        self.db_dir = db_dir
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.db_dir / "catanis_database.db"
        
        # Main outputs
        self.symbol_class_map: Dict[int, Dict[str, Any]] = {}  # {sprite_id: {class_name, table_name, is_named}}
        self.sprite_data: Dict[int, Dict[str, Any]] = {}  # Sprite metadata
        
    def load_xml(self) -> ET.Element:
        """Load and parse the 2d_catanis.xml file"""
        logger.info(f"Loading XML from {self.xml_path}")
        tree = ET.parse(self.xml_path)
        return tree.getroot()
    
    def build_symbol_class_map(self, root: ET.Element) -> None:
        """Extract SymbolClass mapping from the SWF XML"""
        logger.info("Building SymbolClass map...")
        
        tags = root.find('tags')
        if tags is None:
            logger.warning("No tags element found")
            return
        
        items = list(tags)
        
        # Find SymbolClassTag
        for item in items:
            if item.attrib.get('type') == 'SymbolClassTag':
                tags_elem = item.find('tags')
                names_elem = item.find('names')
                
                if tags_elem is not None and names_elem is not None:
                    tags_list = list(tags_elem)
                    names_list = list(names_elem)
                    
                    for tag_item, name_item in zip(tags_list, names_list):
                        tag_id = tag_item.text
                        class_name = name_item.text
                        
                        if tag_id and class_name:
                            try:
                                sprite_id = int(tag_id)
                                table_name = str(class_name)
                                self.symbol_class_map[sprite_id] = {
                                    'class_name': str(class_name),
                                    'table_name': table_name,
                                    'is_named': True
                                }
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Failed to parse symbol class entry: {e}")
        
        logger.info(f"Found {len(self.symbol_class_map)} symbol class mappings")
    
    def extract_matrix_data(self, matrix_elem: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
        """Extract MATRIX transformation data including rotation (rotateSkew values)"""
        if matrix_elem is None or matrix_elem.tag != 'matrix':
            return None
        
        attrs = matrix_elem.attrib
        return {
            'hasScale': attrs.get('hasScale') == 'true',
            'hasRotate': attrs.get('hasRotate') == 'true',
            'scaleX': float(attrs.get('scaleX', 1.0)),
            'scaleY': float(attrs.get('scaleY', 1.0)),
            'translateX': int(attrs.get('translateX', 0)),
            'translateY': int(attrs.get('translateY', 0)),
            'rotateSkew0': float(attrs.get('rotateSkew0', 0.0)),
            'rotateSkew1': float(attrs.get('rotateSkew1', 0.0)),
        }
    
    def extract_place_object_data(self, place_obj_elem: ET.Element) -> Dict[str, Any]:
        """Extract PlaceObject data including flags and matrix"""
        attrs = place_obj_elem.attrib
        
        data = {
            'characterId': int(attrs.get('characterId', 0)),
            'characterIdIsDefinesprite': attrs.get('characterIdIsDefinesprite') == 'true',
            'depth': int(attrs.get('depth', 0)),
            'name': attrs.get('name'),
            # PlaceObject flags
            'placeFlagHasClipActions': attrs.get('placeFlagHasClipActions') == 'true',
            'placeFlagHasClipDepth': attrs.get('placeFlagHasClipDepth') == 'true',
            'placeFlagHasName': attrs.get('placeFlagHasName') == 'true',
            'placeFlagHasRatio': attrs.get('placeFlagHasRatio') == 'true',
            'placeFlagHasColorTransform': attrs.get('placeFlagHasColorTransform') == 'true',
            'placeFlagHasMatrix': attrs.get('placeFlagHasMatrix') == 'true',
            'placeFlagHasCharacter': attrs.get('placeFlagHasCharacter') == 'true',
            'placeFlagMove': attrs.get('placeFlagMove') == 'true',
        }
        
        # Extract MATRIX data if present
        matrix_elem = place_obj_elem.find('matrix')
        if matrix_elem is not None:
            data['matrix'] = self.extract_matrix_data(matrix_elem)
        
        return data
    
    def parse_sprite_frames(self, sprite_elem: ET.Element) -> Dict[int, List[Dict[str, Any]]]:
        """
        Parse all frames from a DefinedSpriteTag.
        
        Uses stateful mode: accumulate PlaceObjects, respect RemoveObjects.
        This tracks what objects persist across frames.
        
        Returns:
            frame_index -> list of PlaceObjects at that frame
        """
        frames: Dict[int, List[Dict[str, Any]]] = {}
        current_frame = 0
        active_layers: Dict[int, Dict[str, Any]] = {}
        
        subTags = sprite_elem.find('subTags')
        if subTags is None:
            logger.debug(f"  No subTags found in sprite")
            return frames
        
        for item in list(subTags):
            item_type = item.attrib.get('type', '')
            
            if item_type == 'ShowFrameTag':
                # Frame boundary - save snapshot of all currently active layers
                if active_layers:
                    frame_list = sorted(active_layers.values(), key=lambda x: x.get('depth', 0))
                    frames[current_frame] = list(frame_list)
                else:
                    frames[current_frame] = []
                current_frame += 1
                    
            elif item_type == 'PlaceObject2Tag' or item_type == 'PlaceObjectTag':
                # Add or update layer in active state
                place_obj_data = self.extract_place_object_data(item)
                depth = place_obj_data.get('depth')
                if depth is not None:
                    active_layers[depth] = place_obj_data
                        
            elif item_type == 'RemoveObject2Tag' or item_type == 'RemoveObjectTag':
                # Remove layer from active state
                attrs = item.attrib
                depth = None
                if 'depth' in attrs:
                    depth = int(attrs.get('depth'))
                else:
                    depth_elem = item.find('depth')
                    if depth_elem is not None:
                        depth = int(depth_elem.text)
                
                if depth is not None:
                    active_layers.pop(depth, None)
        
        # Add final frame if there are active objects
        if active_layers:
            frame_list = sorted(active_layers.values(), key=lambda x: x.get('depth', 0))
            frames[current_frame] = list(frame_list)
        
        return frames
    
    def create_unified_database(self) -> None:
        """Create unified catanis_database.db with sprite_metadata table"""
        logger.info(f"Creating unified database: {self.db_file}")
        
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            
            # Create sprite_metadata table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS sprite_metadata (
                sprite_id INTEGER PRIMARY KEY,
                table_name TEXT NOT NULL UNIQUE,
                class_name TEXT,
                is_named BOOLEAN,
                frame_count INTEGER
            )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Created sprite_metadata table")
        except Exception as e:
            logger.error(f"Failed to create unified database: {e}")
            raise
    
    def insert_sprite_table(self, sprite_id: int, table_name: str, 
                           class_name: str, frames: Dict[int, List[Dict[str, Any]]]) -> None:
        """Insert a sprite's frame data into its own table in the unified database"""
        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        
        try:
            # Create table for this sprite with full MATRIX data including rotation
            cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS [{table_name}] (
                frame_index INTEGER NOT NULL,
                depth INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                character_id_is_definesprite BOOLEAN,
                name TEXT,
                matrix_scale_x REAL,
                matrix_scale_y REAL,
                matrix_translate_x INTEGER,
                matrix_translate_y INTEGER,
                matrix_rotate_skew0 REAL,
                matrix_rotate_skew1 REAL,
                matrix_has_scale BOOLEAN,
                matrix_has_rotate BOOLEAN,
                place_flag_has_clip_actions BOOLEAN,
                place_flag_has_clip_depth BOOLEAN,
                place_flag_has_name BOOLEAN,
                place_flag_has_ratio BOOLEAN,
                place_flag_has_color_transform BOOLEAN,
                place_flag_has_matrix BOOLEAN,
                place_flag_has_character BOOLEAN,
                place_flag_move BOOLEAN,
                PRIMARY KEY (frame_index, depth)
            )
            ''')
            
            # Insert frame data
            inserted_count = 0
            for frame_index, objects in frames.items():
                for obj in objects:
                    char_id = obj.get('characterId', 0)
                    char_id_is_sprite = obj.get('characterIdIsDefinesprite', False)
                    depth = obj.get('depth', 0)
                    name = obj.get('name')
                    
                    matrix = obj.get('matrix', {})
                    scale_x = matrix.get('scaleX', 1.0)
                    scale_y = matrix.get('scaleY', 1.0)
                    trans_x = matrix.get('translateX', 0)
                    trans_y = matrix.get('translateY', 0)
                    rotate_skew0 = matrix.get('rotateSkew0', 0.0)
                    rotate_skew1 = matrix.get('rotateSkew1', 0.0)
                    has_scale = matrix.get('hasScale', False)
                    has_rotate = matrix.get('hasRotate', False)
                    
                    # PlaceObject flags
                    pf_clip_actions = obj.get('placeFlagHasClipActions', False)
                    pf_clip_depth = obj.get('placeFlagHasClipDepth', False)
                    pf_name = obj.get('placeFlagHasName', False)
                    pf_ratio = obj.get('placeFlagHasRatio', False)
                    pf_color_transform = obj.get('placeFlagHasColorTransform', False)
                    pf_has_matrix = obj.get('placeFlagHasMatrix', False)
                    pf_has_character = obj.get('placeFlagHasCharacter', False)
                    pf_move = obj.get('placeFlagMove', False)
                    
                    cursor.execute(f'''
                    INSERT OR REPLACE INTO [{table_name}]
                    (frame_index, depth, character_id, character_id_is_definesprite, name, 
                     matrix_scale_x, matrix_scale_y,
                     matrix_translate_x, matrix_translate_y, 
                     matrix_rotate_skew0, matrix_rotate_skew1,
                     matrix_has_scale, matrix_has_rotate,
                     place_flag_has_clip_actions, place_flag_has_clip_depth,
                     place_flag_has_name, place_flag_has_ratio,
                     place_flag_has_color_transform, place_flag_has_matrix,
                     place_flag_has_character, place_flag_move)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (frame_index, depth, char_id, char_id_is_sprite, name, 
                          scale_x, scale_y, 
                          trans_x, trans_y, rotate_skew0, rotate_skew1,
                          has_scale, has_rotate,
                          pf_clip_actions, pf_clip_depth, pf_name, pf_ratio,
                          pf_color_transform, pf_has_matrix, pf_has_character, pf_move))
                    inserted_count += 1
            
            # Update sprite_metadata
            cursor.execute('''
            INSERT OR REPLACE INTO sprite_metadata 
            (sprite_id, table_name, class_name, is_named, frame_count)
            VALUES (?, ?, ?, ?, ?)
            ''', (sprite_id, table_name, class_name, True, len(frames)))
            
            conn.commit()
            logger.debug(f"  [{sprite_id}] {table_name}: {inserted_count} rows in {len(frames)} frames")
            
        except Exception as e:
            logger.error(f"Failed to insert sprite {sprite_id} ({table_name}): {e}")
            raise
        finally:
            conn.close()
    
    def build_all_sprites(self, root: ET.Element) -> None:
        """Build database for all DefinedSprites in the XML"""
        logger.info("Building sprite frame data...")
        
        tags = root.find('tags')
        if tags is None:
            return
        
        # Collect all DefineSpriteTag entries from the XML tree
        spriteid_to_element: Dict[int, ET.Element] = {}
        
        def collect_sprites(element: ET.Element) -> None:
            """Recursively find all DefineSpriteTag entries"""
            if element.attrib.get('type') == 'DefineSpriteTag':
                sprite_id_str = element.attrib.get('spriteId')
                if sprite_id_str:
                    try:
                        sprite_id = int(sprite_id_str)
                        spriteid_to_element[sprite_id] = element
                    except ValueError:
                        pass
            
            for child in element:
                collect_sprites(child)
        
        collect_sprites(root)
        logger.info(f"Found {len(spriteid_to_element)} DefineSpriteTag entries")
        
        # Process each sprite
        sprite_count = 0
        for sprite_id, sprite_elem in sorted(spriteid_to_element.items()):
            frame_count = int(sprite_elem.attrib.get('frameCount', 0))
            
            # Get class name from symbol map if available
            sprite_info = self.symbol_class_map.get(sprite_id, {})
            class_name = sprite_info.get('class_name')
            table_name = sprite_info.get('table_name', f"sprite_{sprite_id}")
            
            frames = self.parse_sprite_frames(sprite_elem)
            
            # Insert into database
            self.insert_sprite_table(sprite_id, table_name, class_name or '', frames)
            
            sprite_count += 1
            if sprite_count % 10 == 0 or sprite_count <= 5:
                logger.info(f"  [{sprite_count}] sprite {sprite_id} ({table_name}): {frame_count} frames, {len(frames)} indexed")
            
            self.sprite_data[sprite_id] = {
                'class_name': class_name,
                'table_name': table_name,
                'frame_count': frame_count,
                'frames_indexed': len(frames)
            }
        
        logger.info(f"Built frame data for {sprite_count} sprites")
    
    def save_symbol_class_map(self) -> None:
        """Save SymbolClass map for reference"""
        logger.info("Saving SymbolClass map...")
        
        symbol_map_file = self.db_dir / "catanis_symbol_class_map.json"
        with open(symbol_map_file, 'w', encoding='utf-8') as f:
            # Convert int keys to strings for JSON compatibility
            json_map = {str(k): v for k, v in self.symbol_class_map.items()}
            json.dump(json_map, f, indent=2)
        
        logger.info(f"Saved symbol map to {symbol_map_file}")
    
    def build_all(self) -> None:
        """Execute full build process"""
        logger.info("=" * 80)
        logger.info("Catanis Animation Database Builder")
        logger.info("=" * 80)
        
        root = self.load_xml()
        self.build_symbol_class_map(root)
        self.create_unified_database()
        self.build_all_sprites(root)
        self.save_symbol_class_map()
        
        logger.info("=" * 80)
        logger.info(f"Build complete! Database: {self.db_file}")
        logger.info("=" * 80)


if __name__ == '__main__':
    src_dir = Path(__file__).parent.parent / "src"
    xml_path = src_dir / "2d_catanis.xml"
    db_dir = src_dir / "CatAssets" / "swf_database"
    
    builder = CatanicsDatabaseBuilder(xml_path, db_dir)
    builder.build_all()
