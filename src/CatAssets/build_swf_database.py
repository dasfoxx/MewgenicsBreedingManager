#!/usr/bin/env python3
"""
Build SWF Database from catparts_timeline.xml

Creates a unified catparts.db SQLite database with one table per DefinedSprite.

Structure:
  CatAssets/swf_database/
    catparts.db                     (unified database with all sprite data)
      sprite_metadata               (CHID -> table_name, class_name mapping)
      CatBody                       (frame_index → PlaceObjects for CatBody)
      CatHead                       (frame_index → PlaceObjects for CatHead)
      sprite_8054                   (frame_index → PlaceObjects for unnamed sprite)
      sprite_8084                   (frame_index → PlaceObjects for unnamed sprite)
      ... (one table per sprite, named or unnamed)
    symbol_class_map.json           (complete mapping including unnamed sprites)
"""

import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Namespace for SWF XML
NS = {'': 'http://swf.open-tools.org'}


class SWFDatabaseBuilder:
    """Builds unified catparts.db with one table per sprite from catparts_timeline.xml"""
    
    def __init__(self, swf_xml_path: Path, db_dir: Path):
        """
        Initialize builder.
        
        Args:
            swf_xml_path: Path to catparts_timeline.xml
            db_dir: Output directory for database files
        """
        self.swf_xml_path = Path(swf_xml_path)
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_file = self.db_dir / "catparts.db"
        self.tree = None
        self.root = None
        self.all_tags = {}  # tag_id -> tag dict
        
        # Maps for building metadata
        self.sprite_id_to_class_name: Dict[int, str] = {}  # chid -> class_name
        self.sprite_id_to_table_name: Dict[int, str] = {}  # chid -> table_name
        self.is_named_sprite: Dict[int, bool] = {}  # chid -> is_named
        
    def load_xml(self) -> bool:
        """Load and parse catparts_timeline.xml"""
        try:
            self.tree = ET.parse(self.swf_xml_path)
            self.root = self.tree.getroot()
            logger.info(f"Loaded {self.swf_xml_path}")
            
            # Index all tags by ID
            self._index_tags()
            return True
        except Exception as e:
            logger.error(f"Failed to load XML: {e}")
            return False
    
    def _index_tags(self) -> None:
        """Create a dictionary of all tags by tag_id"""
        for tag_elem in self.root.findall('.//{http://swf.open-tools.org}tag'):
            tag_id = tag_elem.get('id')
            if tag_id:
                self.all_tags[tag_id] = tag_elem
    
    def build_symbol_class_map(self) -> Dict[str, Any]:
        """
        Extract SymbolClass mappings and build complete sprite map including unnamed sprites.
        
        Returns:
            Dict with 'named' and 'unnamed' keys, each containing sprite_id -> class_name
        """
        # Named DefinedSprites with class names
        named_map = {
            "11007": "CatHeadPlacements",
            "10983": "CatTail",
            "10758": "catparts_fla.Firem_58",
            "9926": "CatLeg",
            "8846": "CatBody",
            "8052": "CatEar",
            "7133": "CatHead",
            "7115": "catparts_fla.Symbol167copy_53",
            "7096": "catparts_fla.eye3_46",
            "7092": "catparts_fla.eye2_42",
            "6989": "catparts_fla.fire2_29",
            "5950": "CatEye",
            "5948": "CatMouth",
            "5520": "CatEyebrow",
            "5166": "CatEyeClosed",
            "5164": "CatMouthOpen",
            "4731": "HeadItemF",
            "4569": "HeadItemB",
            "4547": "catparts_fla.Symbol341_158",
            "4540": "catparts_fla.Symbol37_157",
            "4277": "catparts_fla.housecrown_101",
            "4271": "catparts_fla.fire2_103",
            "4196": "NeckItemF",
            "4071": "NeckItemB",
            "3807": "FaceItemF",
            "3742": "catparts_fla.eye2_38",
            "3698": "FaceItemB",
            "3585": "catparts_fla.Symbol341_141",
            "3558": "catparts_fla.eye3_40",
            "3435": "CatMouthSmile",
            "3002": "Weapon",
            "2980": "catparts_fla.GAMdiceart_329",
            "2965": "catparts_fla.Symbol80copy2_328",
            "2676": "Trinket",
            "2675": "CatTexture",
            "2670": "catparts_fla.Symbol114_10",
            "2664": "catparts_fla.Symbol115_9",
            "2338": "CatEye_Right",
            "2315": "catparts_fla.Symbol167_68",
            "2306": "catparts_fla.eye4_92",
            "2303": "catparts_fla.eye5_50",
            "1858": "CatEyeClosed_Right",
            "1714": "catparts_fla.Wispfire_73",
            "1628": "catparts_fla.diceeye_76",
            "1356": "HeadItemIcon",
            "1355": "FaceItemIcon",
            "1354": "NeckItemIcon",
            "1353": "WeaponIcon",
            "1352": "TrinketIcon",
            "1351": "HeadItemIcon_Worn",
            "1350": "FaceItemIcon_Worn",
            "1349": "NeckItemIcon_Worn",
            "1348": "WeaponIcon_Worn",
            "1347": "TrinketIcon_Worn",
            "1346": "catparts_fla.Symbol206_420",
            "1343": "catparts_fla.Symbol210_418",
            "1340": "HeadItemIcon_Broken",
            "1128": "FaceItemIcon_Broken",
            "910": "NeckItemIcon_Broken",
            "718": "WeaponIcon_Broken",
            "412": "TrinketIcon_Broken",
            "22": "catparts_fla.Symbol207_427",
        }
        
        # Get named IDs first
        named_ids = {int(sid) for sid in named_map.keys()}
        
        # Build unnamed sprite map without calling build_unnamed_sprite_groups
        unnamed_groups = self._build_unnamed_sprite_groups_internal(named_ids)
        unnamed_map = {}
        for unnamed_ids in unnamed_groups.values():
            for sprite_id in unnamed_ids:
                unnamed_map[str(sprite_id)] = f"sprite_{sprite_id}"
        
        logger.info(f"Loaded {len(named_map)} named sprite mappings")
        logger.info(f"Loaded {len(unnamed_map)} unnamed sprite mappings")
        
        return {
            'named': named_map,
            'unnamed': unnamed_map,
            'all': {**named_map, **unnamed_map}
        }
    
    def _build_unnamed_sprite_groups_internal(self, named_ids: set) -> Dict[str, List[int]]:
        """
        Internal version of build_unnamed_sprite_groups that takes named_ids as parameter.
        
        Returns:
            Dict mapping named sprite ID (str) -> list of unnamed child sprite IDs (int)
        """
        # All DefineSprite character IDs from catparts.swf in order (from JPEXS)
        all_sprites = [
            3, 6, 9, 12, 13, 16, 17,
            22,
            24, 31, 33, 35, 37, 39, 41, 42, 44, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76, 85, 87,
            90, 93, 95, 97, 113, 121, 123, 159, 161, 180, 188, 191, 221, 247, 250, 252, 257, 260, 262, 264,
            283, 290, 293, 324, 336, 356,
            412,
            572, 576, 579, 586, 599, 601, 622, 630, 637, 647, 666, 667, 687, 689, 698, 700,
            718,
            914, 1093, 1095, 1097, 1099, 1102, 1104, 1106, 1107, 1109, 1111,
            1128,
            1308,
            1340,
            1342,
            1343,
            1346,
            1347,
            1348,
            1349,
            1350,
            1351,
            1352,
            1353,
            1354,
            1355,
            1356,
            1628,
            1714,
            1858,
            2131, 2133, 2141, 2142, 2152, 2161, 2162, 2169, 2171, 2172, 2219, 2256, 2257, 2277, 2279, 2301,
            2303,
            2306,
            2315,
            2338,
            2636, 2652, 2655, 2657, 2659,
            2664,
            2670,
            2672, 2673,
            2675,
            2676,
            2688, 2695, 2697, 2702, 2704, 2706, 2708, 2710, 2712, 2714, 2716, 2718, 2721, 2723, 2725, 2729,
            2731, 2733, 2735, 2737, 2739, 2741, 2743, 2744, 2745, 2747, 2749, 2751, 2753, 2790, 2796, 2803,
            2805, 2807, 2809, 2833, 2885, 2889, 2892, 2909, 2912, 2916, 2937, 2956, 2960, 2962,
            2965,
            2966,
            2980,
            2982,
            3002,
            3356,
            3435,
            3440, 3452, 3463, 3465, 3467, 3469, 3472, 3474, 3476, 3478, 3480, 3482, 3483, 3485, 3487, 3489,
            3491, 3528, 3547, 3549, 3550, 3552, 3554, 3556,
            3558,
            3560, 3562, 3564, 3565, 3567, 3569, 3580, 3582, 3584,
            3585,
            3664, 3666, 3668, 3670, 3672, 3675, 3676, 3678, 3681, 3686, 3688,
            3698,
            3699, 3740,
            3742,
            3744, 3798, 3799,
            3807,
            3809, 3819, 3821, 3823, 3825, 3827, 3829, 3831, 3833, 3835, 3837, 3839, 3840, 3842, 3844, 3846,
            3849, 3851, 3853, 3855, 3857, 3858, 3860, 3891, 3909, 3912, 3914, 3916, 3917, 3919, 3921, 3922,
            3974, 3975, 3997, 3998, 3999, 4000, 4030, 4032, 4035, 4041, 4044, 4047, 4048, 4050, 4052, 4054,
            4055,
            4071,
            4072, 4078,
            4196,
            4271,
            4272,
            4277,
            4294, 4296, 4298, 4300, 4302, 4304, 4306, 4312, 4314, 4344, 4349, 4354, 4359, 4365, 4367, 4368,
            4370, 4386, 4387, 4392, 4393, 4395, 4397, 4399, 4404, 4405, 4406, 4410, 4412, 4414, 4415, 4416,
            4464, 4465, 4490, 4494, 4512, 4529, 4533, 4535, 4537,
            4540,
            4541, 4542, 4544, 4545, 4546,
            4547,
            4550, 4552, 4554, 4555, 4559, 4561, 4568,
            4569,
            4583, 4587, 4589, 4591, 4596, 4655, 4697, 4725, 4728,
            4731,
            5085,
            5164,
            5166,
            5520,
            5870, 5935,
            5948,
            5950,
            5962, 5973, 6292, 6338, 6898, 6900,
            6989,
            6991, 6992, 6993, 6995, 6996, 6998, 6999, 7017, 7019, 7020, 7091,
            7092,
            7093, 7094, 7095,
            7096,
            7101, 7104, 7105,
            7115,
            7133,
            7269, 7802,
            8052,
            8054, 8084, 8823,
            8846,
            9054, 9084, 9087,
            9926,
            10121, 10758,
            10936,
            10983,
            10985, 10988, 10990, 10992, 10994, 10996, 10998, 11000, 11002, 11004, 11006,
            11007,
        ]
        
        # Build groups: for each named sprite, collect all unnamed sprites between it and the previous named sprite
        groups: Dict[str, List[int]] = {}
        last_named_idx = -1
        
        for i, sprite_id in enumerate(all_sprites):
            if sprite_id in named_ids:
                # This is a named sprite; collect all unnamed sprites since the last named sprite
                if i > last_named_idx + 1:
                    unnamed_between = [all_sprites[j] for j in range(last_named_idx + 1, i)]
                    groups[str(sprite_id)] = unnamed_between
                last_named_idx = i
        
        logger.info(f"Built {len(groups)} unnamed sprite groups")
        return groups
    
    def parse_sprite_frames(self, sprite_elem: ET.Element, stateful: bool = True) -> Dict[int, List[Dict[str, Any]]]:
        """
        Parse a DefinedSprite XML element into frame_index -> PlaceObjects.
        
        Args:
            sprite_elem: XML element with type="DefineSpriteTag"
            stateful: If True, track PlaceObject/RemoveObject state across frames (for parts like CatBody).
                     If False, capture only PlaceObjects in each frame block (for CatTexture).
        
        Returns:
            {0: [placObj1, placObj2, ...], 1: [...], ...}
        """
        frames = {}
        current_frame = 0
        
        # Look for subTags - the timeline of the sprite
        sub_tags = sprite_elem.find('subTags')
        if sub_tags is None:
            logger.warning(f"No subTags found in sprite element")
            return frames
        
        logger.info(f"Found subTags with {len(list(sub_tags))} items")
        
        if stateful:
            # State-tracking mode: accumulate PlaceObjects, respect RemoveObjects
            display_by_depth = {}
            
            # Iterate through all items in subTags
            for item in list(sub_tags):
                tag_type = item.attrib.get('type')
                
                # Process PlaceObject tags
                if tag_type == 'PlaceObject2Tag' or tag_type == 'PlaceObjectTag':
                    depth_str = item.attrib.get('depth')
                    char_id = item.attrib.get('characterId')
                    # Check the placeFlagHasCharacter flag - if false, character should be reused
                    place_flag_has_character = item.attrib.get('placeFlagHasCharacter') == 'true'
                    
                    # Debug: log frames 758-759
                    if current_frame >= 758 and current_frame <= 759:
                        logger.info(f"Frame {current_frame}: PlaceObject depth_attr={depth_str} char_id={char_id} flagHasChar={place_flag_has_character}")
                    
                    if depth_str:
                        depth = int(depth_str)
                        # If placeFlagHasCharacter is False, reuse character from previous frame at this depth
                        if not place_flag_has_character and depth in display_by_depth:
                            # PlaceObject without specifying a character - keep the previous character
                            # but update other properties (matrix, etc.)
                            previous = display_by_depth[depth]
                            updated = self._extract_place_object(item, allow_missing_char_id=True)
                            if updated:
                                # Merge: keep previous characterId, use new matrix/other properties
                                updated['character_id'] = previous.get('character_id')
                                display_by_depth[depth] = updated
                        else:
                            # Normal PlaceObject with character specified, or new depth
                            place_obj = self._extract_place_object(item)
                            if place_obj:
                                display_by_depth[depth] = place_obj
                    else:
                        logger.warning(f"Frame {current_frame}: PlaceObject has no depth attribute!")
                
                # Process RemoveObject tags  
                elif tag_type == 'RemoveObject2Tag' or tag_type == 'RemoveObjectTag':
                    depth_str = item.attrib.get('depth')
                    if current_frame >= 758 and current_frame <= 759:
                        logger.info(f"Frame {current_frame}: RemoveObject depth_attr={depth_str}")
                    if depth_str:
                        depth = int(depth_str)
                        display_by_depth.pop(depth, None)
                
                # Frame boundary
                elif tag_type == 'ShowFrameTag':
                    if current_frame >= 758 and current_frame <= 759:
                        logger.info(f"Frame {current_frame}: ShowFrameTag - saving {len(display_by_depth)} layers: {sorted(display_by_depth.keys())}")
                    frame_list = [dict(v) for _d, v in sorted(display_by_depth.items())]
                    frames[current_frame] = frame_list
                    current_frame += 1
        else:
            # Non-stateful mode: capture only PlaceObjects in each frame block
            frame_objects = []
            place_count = 0
            show_count = 0
            
            # Iterate through all items in subTags
            for item in list(sub_tags):
                tag_type = item.attrib.get('type')
                
                # Process PlaceObject tags only
                if tag_type == 'PlaceObject2Tag' or tag_type == 'PlaceObjectTag':
                    place_obj = self._extract_place_object(item)
                    if place_obj:
                        frame_objects.append(place_obj)
                        place_count += 1
                    else:
                        logger.debug(f"_extract_place_object returned None for item")
                
                # Frame boundary
                elif tag_type == 'ShowFrameTag':
                    frame_list = [dict(v) for v in sorted(frame_objects, key=lambda x: x.get('depth', 0))]
                    frames[current_frame] = frame_list
                    show_count += 1
                    frame_objects = []
                    current_frame += 1
            
            logger.info(f"Non-stateful parse: {show_count} ShowFrameTags, {place_count} PlaceObjects extracted, {len(frames)} frames created")
        
        return frames
    
    def _extract_place_object(self, place_obj_item: ET.Element, allow_missing_char_id: bool = False) -> Optional[Dict[str, Any]]:
        """Extract place object data with MATRIX info and PlaceObject flags
        
        Args:
            place_obj_item: XML element for PlaceObject2/PlaceObject
            allow_missing_char_id: If True, allow extraction even without characterId (for state tracking)
        """
        try:
            result = {}
            
            # Character ID (required unless allow_missing_char_id is True)
            char_id = place_obj_item.attrib.get('characterId')
            if char_id:
                result['character_id'] = int(char_id)
            elif not allow_missing_char_id:
                return None
            
            # Depth (required)
            depth = place_obj_item.attrib.get('depth')
            result['depth'] = int(depth) if depth else 0
            
            # Name (optional)
            name = place_obj_item.attrib.get('name')
            result['name'] = name
            
            # Clip depth (optional)
            clip_depth = place_obj_item.attrib.get('clipDepth')
            result['clipDepth'] = int(clip_depth) if clip_depth else None
            
            # Force write as long (optional)
            force_write = place_obj_item.attrib.get('forceWriteAsLong')
            result['forceWriteAsLong'] = force_write == 'true' if force_write else False
            
            # Extract PlaceObject flags from attributes
            result['placeFlagHasClipActions'] = place_obj_item.attrib.get('placeFlagHasClipActions') == 'true'
            result['placeFlagHasClipDepth'] = place_obj_item.attrib.get('placeFlagHasClipDepth') == 'true'
            result['placeFlagHasName'] = place_obj_item.attrib.get('placeFlagHasName') == 'true'
            result['placeFlagHasRatio'] = place_obj_item.attrib.get('placeFlagHasRatio') == 'true'
            result['placeFlagHasColorTransform'] = place_obj_item.attrib.get('placeFlagHasColorTransform') == 'true'
            result['placeFlagHasMatrix'] = place_obj_item.attrib.get('placeFlagHasMatrix') == 'true'
            result['placeFlagHasCharacter'] = place_obj_item.attrib.get('placeFlagHasCharacter') == 'true'
            result['placeFlagMove'] = place_obj_item.attrib.get('placeFlagMove') == 'true'
            
            # Extract MATRIX data
            matrix_data = self._extract_matrix(place_obj_item)
            result.update(matrix_data)
            
            return result
        except Exception as e:
            logger.debug(f"Error extracting PlaceObject: {e}")
            return None
    
    def _extract_matrix(self, place_obj_item: ET.Element) -> Dict[str, Any]:
        """Extract MATRIX transformation data"""
        result = {
            'matrix_scale_x': 1.0,
            'matrix_scale_y': 1.0,
            'matrix_translate_x': 0,
            'matrix_translate_y': 0,
            'matrix_has_scale': False,
            'matrix_has_rotate': False,
        }
        
        # Find MATRIX element within this item
        matrix_elem = place_obj_item.find('matrix')
        if matrix_elem is None:
            return result
        
        # Translation (from MATRIX attributes)
        trans_x = matrix_elem.attrib.get('translateX')
        trans_y = matrix_elem.attrib.get('translateY')
        if trans_x:
            result['matrix_translate_x'] = int(trans_x)
        if trans_y:
            result['matrix_translate_y'] = int(trans_y)
        
        # Scale (from scale child element)
        scale = matrix_elem.find('scale')
        if scale is not None:
            result['matrix_has_scale'] = True
            scale_x = scale.attrib.get('scaleX')
            scale_y = scale.attrib.get('scaleY')
            if scale_x:
                result['matrix_scale_x'] = float(scale_x)
            if scale_y:
                result['matrix_scale_y'] = float(scale_y)
        
        # Rotate (from rotate child element)
        rotate = matrix_elem.find('rotate')
        if rotate is not None:
            result['matrix_has_rotate'] = True
        
        return result
    
    def create_sprite_table(self, conn: sqlite3.Connection, table_name: str, sprite_id: int) -> bool:
        """
        Create a table for a single sprite in the unified database.
        
        Args:
            conn: SQLite connection to catparts.db
            table_name: Name of the table (e.g., "CatBody" or "sprite_8054")
            sprite_id: Character ID of the sprite
        
        Returns:
            True if successful
        """
        try:
            cursor = conn.cursor()
            
            # Create table with all data columns plus the new character_id_is_definesprite column
            cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS [{table_name}] (
                frame_index INTEGER NOT NULL,
                depth INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                character_id_is_definesprite BOOLEAN NOT NULL DEFAULT 0,
                name TEXT,
                matrix_scale_x REAL,
                matrix_scale_y REAL,
                matrix_translate_x INTEGER,
                matrix_translate_y INTEGER,
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
                clip_depth INTEGER,
                force_write_as_long BOOLEAN,
                PRIMARY KEY (frame_index, depth)
            )
            ''')
            
            logger.debug(f"Created table [{table_name}]")
            return True
        except Exception as e:
            logger.error(f"Failed to create table {table_name}: {e}")
            return False
    
    def insert_sprite_frames(
        self, conn: sqlite3.Connection, table_name: str, frames: Dict[int, List[Dict[str, Any]]],
        all_sprite_ids: set
    ) -> bool:
        """
        Insert frame data into a sprite table.
        
        Args:
            conn: SQLite connection to catparts.db
            table_name: Name of the table
            frames: Dict from parse_sprite_frames()
            all_sprite_ids: Set of all known DefineSprite IDs for checking character_id_is_definesprite
        
        Returns:
            True if successful
        """
        try:
            cursor = conn.cursor()
            
            for frame_idx, objects in frames.items():
                for obj in objects:
                    # Determine if this character_id references another DefineSprite
                    char_id = obj.get('character_id')
                    is_definesprite = 1 if (char_id in all_sprite_ids) else 0
                    
                    cursor.execute(f'''
                    INSERT OR REPLACE INTO [{table_name}] (
                        frame_index, depth, character_id, character_id_is_definesprite, name,
                        matrix_scale_x, matrix_scale_y,
                        matrix_translate_x, matrix_translate_y,
                        matrix_has_scale, matrix_has_rotate,
                        place_flag_has_clip_actions, place_flag_has_clip_depth,
                        place_flag_has_name, place_flag_has_ratio,
                        place_flag_has_color_transform, place_flag_has_matrix,
                        place_flag_has_character, place_flag_move,
                        clip_depth, force_write_as_long
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        frame_idx,
                        obj.get('depth', 0),
                        char_id,
                        is_definesprite,
                        obj.get('name'),
                        obj.get('matrix_scale_x', 1.0),
                        obj.get('matrix_scale_y', 1.0),
                        obj.get('matrix_translate_x', 0),
                        obj.get('matrix_translate_y', 0),
                        obj.get('matrix_has_scale', False),
                        obj.get('matrix_has_rotate', False),
                        obj.get('placeFlagHasClipActions', False),
                        obj.get('placeFlagHasClipDepth', False),
                        obj.get('placeFlagHasName', False),
                        obj.get('placeFlagHasRatio', False),
                        obj.get('placeFlagHasColorTransform', False),
                        obj.get('placeFlagHasMatrix', False),
                        obj.get('placeFlagHasCharacter', False),
                        obj.get('placeFlagMove', False),
                        obj.get('clipDepth'),
                        obj.get('forceWriteAsLong', False),
                    ))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to insert frames into {table_name}: {e}")
            return False
    
    def create_sprite_metadata_table(self, conn: sqlite3.Connection, all_mappings: Dict[int, Tuple[str, str, bool]]) -> bool:
        """
        Create sprite_metadata table mapping sprite IDs to table names and class names.
        
        Args:
            conn: SQLite connection
            all_mappings: {sprite_id: (table_name, class_name, is_named)}
        
        Returns:
            True if successful
        """
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS sprite_metadata (
                sprite_id INTEGER PRIMARY KEY,
                table_name TEXT NOT NULL UNIQUE,
                class_name TEXT,
                is_named BOOLEAN NOT NULL
            )
            ''')
            
            for sprite_id, (table_name, class_name, is_named) in all_mappings.items():
                cursor.execute('''
                INSERT OR REPLACE INTO sprite_metadata (sprite_id, table_name, class_name, is_named)
                VALUES (?, ?, ?, ?)
                ''', (sprite_id, table_name, class_name, is_named))
            
            conn.commit()
            logger.info(f"Created sprite_metadata table with {len(all_mappings)} entries")
            return True
        except Exception as e:
            logger.error(f"Failed to create sprite_metadata table: {e}")
            return False
    
    def build(self) -> bool:
        """Build unified catparts.db with one table per sprite"""
        if not self.load_xml():
            return False
        
        # Build complete symbol mappings
        symbol_maps = self.build_symbol_class_map()
        all_sprite_ids = {int(sid) for sid in symbol_maps['all'].keys()}
        named_sprite_ids = {int(sid) for sid in symbol_maps['named'].keys()}
        
        logger.info(f"Building unified catparts.db for {len(all_sprite_ids)} sprites ({len(named_sprite_ids)} named)")
        
        # Create/connect to unified database
        try:
            conn = sqlite3.connect(str(self.db_file))
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception as e:
            logger.error(f"Failed to create catparts.db: {e}")
            return False
        
        # Storage for all sprite frames
        all_frames: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}  # sprite_id -> frames
        all_metadata: Dict[int, Tuple[str, str, bool]] = {}  # sprite_id -> (table_name, class_name, is_named)
        
        # Parse all sprites and collect frames
        processed = set()
        
        try:
            for _event, elem in ET.iterparse(self.swf_xml_path, events=("end",)):
                if elem.tag != 'item' or elem.attrib.get('type') != 'DefineSpriteTag':
                    continue
                
                sprite_id_raw = elem.attrib.get('spriteId')
                if not sprite_id_raw:
                    elem.clear()
                    continue
                
                try:
                    sprite_id = int(sprite_id_raw)
                except ValueError:
                    elem.clear()
                    continue
                
                if sprite_id not in all_sprite_ids or sprite_id in processed:
                    elem.clear()
                    continue
                
                processed.add(sprite_id)
                
                # Parse frames
                frames = self.parse_sprite_frames(elem)
                if not frames:
                    logger.warning(f"No frames found in sprite ID {sprite_id}")
                    elem.clear()
                    continue
                
                all_frames[sprite_id] = frames
                
                # Build metadata entry
                is_named = sprite_id in named_sprite_ids
                class_name = symbol_maps['all'][str(sprite_id)]
                
                if is_named:
                    table_name = class_name  # Use class name as table name for named sprites
                else:
                    table_name = f"sprite_{sprite_id}"  # Use sprite_CHID for unnamed
                
                all_metadata[sprite_id] = (table_name, class_name, is_named)
                self.sprite_id_to_table_name[sprite_id] = table_name
                self.sprite_id_to_class_name[sprite_id] = class_name
                self.is_named_sprite[sprite_id] = is_named
                
                elem.clear()
                
                if len(processed) >= len(all_sprite_ids):
                    break
        
        except Exception as e:
            logger.error(f"Error during XML parsing: {e}")
            conn.close()
            return False
        
        logger.info(f"Parsed {len(all_frames)} sprites")
        
        # Create sprite_metadata table
        if not self.create_sprite_metadata_table(conn, all_metadata):
            conn.close()
            return False
        
        # Create tables and insert data
        created_count = 0
        failed_count = 0
        
        for sprite_id, frames in all_frames.items():
            table_name, class_name, is_named = all_metadata[sprite_id]
            
            if not self.create_sprite_table(conn, table_name, sprite_id):
                failed_count += 1
                continue
            
            if not self.insert_sprite_frames(conn, table_name, frames, all_sprite_ids):
                failed_count += 1
                continue
            
            created_count += 1
            if created_count % 20 == 0:
                logger.info(f"Created {created_count} tables...")
        
        conn.close()
        
        logger.info(f"Built {created_count} sprite tables in catparts.db, {failed_count} failed, {len(processed)} processed")
        logger.info("Symbol class map JSON files are externally maintained and not auto-generated")
        return created_count > 0


def main():
    """Main entry point"""
    script_dir = Path(__file__).parent
    
    # Paths - catparts_timeline.xml is in the tools directory
    xml_file = script_dir.parent / "tools" / "catparts_timeline.xml"
    db_dir = script_dir / "CatAssets" / "swf_database"
    
    if not xml_file.exists():
        logger.error(f"catparts_timeline.xml not found at {xml_file}")
        sys.exit(1)
    
    # Build
    builder = SWFDatabaseBuilder(xml_file, db_dir)
    if builder.build():
        logger.info("✅ Database build complete!")
        sys.exit(0)
    else:
        logger.error("❌ Database build failed")
        sys.exit(1)


if __name__ == '__main__':
    main()
