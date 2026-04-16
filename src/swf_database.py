#!/usr/bin/env python3
"""
SWF Database Accessor for Unified catparts.db and catanis_database.db

Provides fast access to precomputed SWF frame data from the unified catparts.db database
and animation frame data from catanis_database.db with rotation support.

One table per DefinedSprite (named or unnamed), plus a sprite_metadata table for lookups.

Usage:
  db = SWFDatabaseAccessor()  # Uses catparts.db by default
  frame_objs = db.get_frame_objects("CatHead", frame_index=0)
  # Returns: [
  #   {characterId: 8055, depth: 1, characterIdIsDefinesprite: False, matrix: {...}},
  #   {characterId: 8056, depth: 2, characterIdIsDefinesprite: True, name: "tex", matrix: {...}},
  #   ...
  # ]
  
  # Access catanis animations with rotation data:
  db.set_active_database("catanis")
  frame_objs = db.get_frame_objects("FlatCat", frame_index=0)
  # frame_objs[i]['matrix'] now includes 'rotateSkew0' and 'rotateSkew1'
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional, Dict, List, Any, Literal
import logging

logger = logging.getLogger("swf_db")


class SWFDatabaseAccessor:
    """Fast accessor for precomputed SWF frame data from catparts.db, catanis_database.db, and familytree.db"""
    
    def __init__(self, db_dir: Optional[Path] = None, db_type: Literal["catparts", "catanis", "familytree"] = "catparts"):
        """Initialize the SWF database accessor.
        
        Args:
            db_dir: Path to the swf_database directory. Defaults to CatAssets/swf_database
            db_type: Which database to use: "catparts" (default), "catanis", or "familytree"
        """
        if db_dir is None:
            db_dir = Path(__file__).parent / "CatAssets" / "swf_database"
        
        self.db_dir = Path(db_dir)
        self.catparts_db_file = self.db_dir / "catparts.db"
        self.catanis_db_file = self.db_dir / "catanis_database.db"
        self.familytree_db_file = self.db_dir / "familytree.db"
        self.shapes_db_file = self.db_dir / "shapes.db"
        
        self._active_db_type: Literal["catparts", "catanis", "familytree"] = db_type
        self._symbol_class_map: Optional[Dict[str, Any]] = None
        self._catanis_symbol_class_map: Optional[Dict[str, Any]] = None
        self._familytree_symbol_class_map: Optional[Dict[str, Any]] = None
        self._sprite_metadata: Optional[Dict[int, tuple]] = None
        self._catanis_sprite_metadata: Optional[Dict[int, tuple]] = None
        self._familytree_sprite_metadata: Optional[Dict[int, tuple]] = None
        self._db_connection: Optional[sqlite3.Connection] = None
        self._catanis_db_connection: Optional[sqlite3.Connection] = None
        self._familytree_db_connection: Optional[sqlite3.Connection] = None
        self._shapes_db_connection: Optional[sqlite3.Connection] = None
        
        if not self.catparts_db_file.exists():
            logger.warning(f"Catparts database not found: {self.catparts_db_file}")
        
        if not self.catanis_db_file.exists():
            logger.debug(f"Catanis database not found: {self.catanis_db_file}")
        
        if not self.familytree_db_file.exists():
            logger.debug(f"Familytree database not found: {self.familytree_db_file}")
        
        if not self.shapes_db_file.exists():
            logger.debug(f"Shape bounds database not found: {self.shapes_db_file}")
        
        self._load_symbol_class_maps()
    
    def set_active_database(self, db_type: Literal["catparts", "catanis", "familytree"]) -> None:
        """Switch between catparts, catanis, and familytree databases."""
        if db_type not in ("catparts", "catanis", "familytree"):
            logger.warning(f"Unknown database type: {db_type}. Using catparts.")
            self._active_db_type = "catparts"
            return
        self._active_db_type = db_type
        logger.debug(f"Switched to {db_type} database")
    
    def _get_active_db_file(self) -> Path:
        """Get the currently active database file path"""
        if self._active_db_type == "catanis":
            return self.catanis_db_file
        elif self._active_db_type == "familytree":
            return self.familytree_db_file
        return self.catparts_db_file
    
    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """Get or create database connection for active database"""
        if self._active_db_type == "catanis":
            return self._get_catanis_connection()
        elif self._active_db_type == "familytree":
            return self._get_familytree_connection()
        return self._get_catparts_connection()
    
    def _get_catparts_connection(self) -> Optional[sqlite3.Connection]:
        """Get or create catparts database connection"""
        if self._db_connection is not None:
            return self._db_connection
        
        if not self.catparts_db_file.exists():
            return None
        
        try:
            self._db_connection = sqlite3.connect(str(self.catparts_db_file))
            self._db_connection.row_factory = sqlite3.Row
            return self._db_connection
        except Exception as e:
            logger.error(f"Failed to open catparts.db: {e}")
            return None
    
    def _get_catanis_connection(self) -> Optional[sqlite3.Connection]:
        """Get or create catanis database connection"""
        if self._catanis_db_connection is not None:
            return self._catanis_db_connection
        
        if not self.catanis_db_file.exists():
            return None
        
        try:
            self._catanis_db_connection = sqlite3.connect(str(self.catanis_db_file))
            self._catanis_db_connection.row_factory = sqlite3.Row
            return self._catanis_db_connection
        except Exception as e:
            logger.error(f"Failed to open catanis_database.db: {e}")
            return None
    
    def _get_familytree_connection(self) -> Optional[sqlite3.Connection]:
        """Get or create familytree database connection"""
        if self._familytree_db_connection is not None:
            return self._familytree_db_connection
        
        if not self.familytree_db_file.exists():
            return None
        
        try:
            self._familytree_db_connection = sqlite3.connect(str(self.familytree_db_file))
            self._familytree_db_connection.row_factory = sqlite3.Row
            return self._familytree_db_connection
        except Exception as e:
            logger.error(f"Failed to open familytree.db: {e}")
            return None
    
    def _get_shapes_connection(self) -> Optional[sqlite3.Connection]:
        """Get or create shapes database connection"""
        if self._shapes_db_connection is not None:
            return self._shapes_db_connection
        
        if not self.shapes_db_file.exists():
            return None
        
        try:
            self._shapes_db_connection = sqlite3.connect(str(self.shapes_db_file))
            self._shapes_db_connection.row_factory = sqlite3.Row
            return self._shapes_db_connection
        except Exception as e:
            logger.error(f"Failed to open shapes.db: {e}")
            return None
    
    def _load_symbol_class_maps(self) -> None:
        """Load the SymbolClass mappings from JSON for both databases"""
        # Load catparts symbol map
        map_file = self.db_dir / "symbol_class_map.json"
        if map_file.exists():
            try:
                with open(map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._symbol_class_map = data or {}
            except Exception as e:
                logger.error(f"Failed to load catparts symbol class map: {e}")
                self._symbol_class_map = {}
        else:
            logger.warning(f"Catparts symbol class map not found: {map_file}")
            self._symbol_class_map = {}
        
        # Load catanis symbol map
        catanis_map_file = self.db_dir / "catanis_symbol_class_map.json"
        if catanis_map_file.exists():
            try:
                with open(catanis_map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._catanis_symbol_class_map = data or {}
            except Exception as e:
                logger.debug(f"Failed to load catanis symbol class map: {e}")
                self._catanis_symbol_class_map = {}
        else:
            logger.debug(f"Catanis symbol class map not found: {catanis_map_file}")
            self._catanis_symbol_class_map = {}
        
        # Load familytree symbol map
        familytree_map_file = self.db_dir / "familytree_symbol_class_map.json"
        if familytree_map_file.exists():
            try:
                with open(familytree_map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._familytree_symbol_class_map = data or {}
            except Exception as e:
                logger.debug(f"Failed to load familytree symbol class map: {e}")
                self._familytree_symbol_class_map = {}
        else:
            logger.debug(f"Familytree symbol class map not found: {familytree_map_file}")
            self._familytree_symbol_class_map = {}
    
    def _load_sprite_metadata(self) -> None:
        """Load sprite metadata from active database's sprite_metadata table"""
        if self._active_db_type == "catanis":
            self._load_catanis_sprite_metadata()
        elif self._active_db_type == "familytree":
            self._load_familytree_sprite_metadata()
        else:
            self._load_catparts_sprite_metadata()
    
    def _load_catparts_sprite_metadata(self) -> None:
        """Load catparts sprite metadata from database sprite_metadata table"""
        if self._sprite_metadata is not None:
            return
        
        self._sprite_metadata = {}
        
        conn = self._get_catparts_connection()
        if conn is None:
            return
        
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT sprite_id, table_name, class_name, is_named
            FROM sprite_metadata
            ''')
            
            for row in cursor.fetchall():
                sprite_id = row['sprite_id']
                self._sprite_metadata[sprite_id] = (
                    row['table_name'],
                    row['class_name'],
                    bool(row['is_named'])
                )
            
            logger.debug(f"Loaded {len(self._sprite_metadata)} catparts sprite metadata entries")
        except Exception as e:
            logger.debug(f"Failed to load catparts sprite_metadata: {e}")
    
    def _load_catanis_sprite_metadata(self) -> None:
        """Load catanis sprite metadata from database sprite_metadata table"""
        if self._catanis_sprite_metadata is not None:
            return
        
        self._catanis_sprite_metadata = {}
        
        conn = self._get_catanis_connection()
        if conn is None:
            return
        
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT sprite_id, table_name, class_name, is_named
            FROM sprite_metadata
            ''')
            
            for row in cursor.fetchall():
                sprite_id = row['sprite_id']
                self._catanis_sprite_metadata[sprite_id] = (
                    row['table_name'],
                    row['class_name'],
                    bool(row['is_named'])
                )
            
            logger.debug(f"Loaded {len(self._catanis_sprite_metadata)} catanis sprite metadata entries")
        except Exception as e:
            logger.debug(f"Failed to load catanis sprite_metadata: {e}")
    
    def _load_familytree_sprite_metadata(self) -> None:
        """Load familytree sprite metadata from database sprite_metadata table"""
        if self._familytree_sprite_metadata is not None:
            return
        
        self._familytree_sprite_metadata = {}
        
        conn = self._get_familytree_connection()
        if conn is None:
            return
        
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT sprite_id, table_name, class_name, is_named
            FROM sprite_metadata
            ''')
            
            for row in cursor.fetchall():
                sprite_id = row['sprite_id']
                self._familytree_sprite_metadata[sprite_id] = (
                    row['table_name'],
                    row['class_name'],
                    bool(row['is_named'])
                )
            
            logger.debug(f"Loaded {len(self._familytree_sprite_metadata)} familytree sprite metadata entries")
        except Exception as e:
            logger.debug(f"Failed to load familytree sprite_metadata: {e}")
    
    def _get_table_name(self, class_name_or_sprite_id: Any) -> Optional[str]:
        """Get the table name for a sprite by class name or sprite ID."""
        # Determine which symbol map to use
        symbol_map = self._symbol_class_map
        if self._active_db_type == "catanis":
            symbol_map = self._catanis_symbol_class_map or self._symbol_class_map
        elif self._active_db_type == "familytree":
            symbol_map = self._familytree_symbol_class_map or self._symbol_class_map
        
        # If it's an int (sprite ID), look it up
        if isinstance(class_name_or_sprite_id, int):
            self._load_sprite_metadata()
            if self._active_db_type == "catanis":
                metadata_dict = self._catanis_sprite_metadata
            elif self._active_db_type == "familytree":
                metadata_dict = self._familytree_sprite_metadata
            else:
                metadata_dict = self._sprite_metadata
            if class_name_or_sprite_id in metadata_dict:
                return metadata_dict[class_name_or_sprite_id][0]
            return f"sprite_{class_name_or_sprite_id}"
        
        # String (class name) lookup
        class_name = str(class_name_or_sprite_id)
        
        for sprite_id_str, data in (symbol_map or {}).items():
            if isinstance(data, dict):
                if data.get('class_name') == class_name or data.get('table_name') == class_name:
                    return data.get('table_name', class_name)
            else:
                if data == class_name:
                    return class_name
        
        return class_name
    
    def get_symbol_class_map(self) -> Dict[str, Any]:
        """Get the full symbol class mapping for the active database"""
        if self._active_db_type == "catanis":
            return self._catanis_symbol_class_map or {}
        return self._symbol_class_map or {}
    
    def get_max_frame_index(self, class_name: str) -> int:
        """Get the highest frame index for a sprite class.
        
        Returns:
            Maximum frame index (0-based), or -1 if no frames found or error.
        """
        conn = self._get_connection()
        if conn is None:
            return -1
        
        table_name = self._get_table_name(class_name)
        if not table_name:
            logger.debug(f"No table name found for {class_name}")
            return -1
        
        try:
            cursor = conn.cursor()
            cursor.execute(f'''
            SELECT MAX(frame_index) as max_frame
            FROM [{table_name}]
            ''')
            
            row = cursor.fetchone()
            if row and row['max_frame'] is not None:
                return int(row['max_frame'])
            return -1
        except Exception as e:
            logger.debug(f"Error querying max frame index for {class_name}: {e}")
            return -1
    
    def get_frame_objects(
        self, class_name: str, frame_index: int
    ) -> Optional[List[Dict[str, Any]]]:
        """Get all PlaceObjects in a specific frame of a sprite.
        
        For catanis database, includes rotation data (rotateSkew0, rotateSkew1).
        For catparts database, returns standard transform data.
        
        Special handling for catheadplacements: if character_id is -1, resolves it
        to the previous frame's character_id for that depth (stateful continuity).
        """
        conn = self._get_connection()
        if conn is None:
            return None
        
        table_name = self._get_table_name(class_name)
        if not table_name:
            logger.debug(f"No table name found for {class_name}")
            return None
        
        try:
            cursor = conn.cursor()
            
            # Build SELECT clause based on active database
            if self._active_db_type == "catanis":
                # Catanis has rotation data
                cursor.execute(f'''
                SELECT character_id, depth, name, character_id_is_definesprite,
                       matrix_scale_x, matrix_scale_y,
                       matrix_translate_x, matrix_translate_y,
                       matrix_rotate_skew0, matrix_rotate_skew1,
                       matrix_has_scale, matrix_has_rotate,
                       place_flag_has_clip_actions, place_flag_has_clip_depth,
                       place_flag_has_name, place_flag_has_ratio,
                       place_flag_has_color_transform, place_flag_has_matrix,
                       place_flag_has_character, place_flag_move
                FROM [{table_name}]
                WHERE frame_index = ?
                ORDER BY ROWID ASC
                ''', (frame_index,))
            else:
                # Standard catparts format without rotation
                cursor.execute(f'''
                SELECT character_id, depth, name, character_id_is_definesprite,
                       matrix_scale_x, matrix_scale_y,
                       matrix_translate_x, matrix_translate_y,
                       matrix_has_scale, matrix_has_rotate,
                       place_flag_has_clip_actions, place_flag_has_clip_depth,
                       place_flag_has_name, place_flag_has_ratio,
                       place_flag_has_color_transform, place_flag_has_matrix,
                       place_flag_has_character, place_flag_move
                FROM [{table_name}]
                WHERE frame_index = ?
                ORDER BY ROWID ASC
                ''', (frame_index,))
            
            rows = cursor.fetchall()
            if not rows:
                return []
            
            result = []
            for row in rows:
                # Build matrix object based on available columns
                matrix = {
                    'scaleX': row['matrix_scale_x'],
                    'scaleY': row['matrix_scale_y'],
                    'translateX': row['matrix_translate_x'],
                    'translateY': row['matrix_translate_y'],
                    'hasScale': bool(row['matrix_has_scale']),
                    'hasRotate': bool(row['matrix_has_rotate']),
                }
                
                # Add rotation data if available (catanis database)
                if self._active_db_type == "catanis":
                    try:
                        matrix['rotateSkew0'] = row['matrix_rotate_skew0']
                        matrix['rotateSkew1'] = row['matrix_rotate_skew1']
                    except (KeyError, IndexError):
                        # Column doesn't exist, skip
                        pass
                
                obj = {
                    'characterId': row['character_id'],
                    'characterIdIsDefinesprite': bool(row['character_id_is_definesprite']),
                    'depth': row['depth'],
                    'matrix': matrix,
                    'placeFlags': {
                        'hasClipActions': bool(row['place_flag_has_clip_actions']),
                        'hasClipDepth': bool(row['place_flag_has_clip_depth']),
                        'hasName': bool(row['place_flag_has_name']),
                        'hasRatio': bool(row['place_flag_has_ratio']),
                        'hasColorTransform': bool(row['place_flag_has_color_transform']),
                        'hasMatrix': bool(row['place_flag_has_matrix']),
                        'hasCharacter': bool(row['place_flag_has_character']),
                        'move': bool(row['place_flag_move']),
                    }
                }
                if row['name']:
                    obj['name'] = row['name']
                result.append(obj)
            
            result.sort(key=lambda x: x['depth'])
            
            # Special stateful handling for catheadplacements: resolve -1 character_id
            # to the previous frame's character_id at that depth
            if table_name == 'catheadplacements':
                for obj in result:
                    if obj['characterId'] == -1:
                        resolved_id = self._resolve_previous_character_id(
                            table_name, obj['depth'], frame_index
                        )
                        if resolved_id is not None:
                            obj['characterId'] = resolved_id
                            logger.debug(
                                f"[SWF] Resolved catheadplacements depth {obj['depth']} "
                                f"frame {frame_index}: -1 -> {resolved_id}"
                            )
            
            return result
        except Exception as e:
            logger.error(f"Error querying frame {frame_index} from {table_name}: {e}")
            return None
    
    def _resolve_previous_character_id(
        self, table_name: str, depth: int, frame_index: int
    ) -> Optional[int]:
        """For catheadplacements, resolve -1 character_id by looking back to previous frames.
        
        Walks backwards from the given frame to find the first frame that has a PlaceObject
        at the given depth with a valid character_id.
        """
        conn = self._get_connection()
        if conn is None:
            return None
        
        try:
            cursor = conn.cursor()
            
            # Walk backwards from frame_index - 1 to frame 0
            for prev_frame in range(frame_index - 1, -1, -1):
                cursor.execute(f'''
                SELECT character_id FROM [{table_name}]
                WHERE frame_index = ? AND depth = ? AND character_id != -1
                LIMIT 1
                ''', (prev_frame, depth))
                
                row = cursor.fetchone()
                if row:
                    return row['character_id']
            
            return None
        except Exception as e:
            logger.debug(f"Error resolving character_id for depth {depth}: {e}")
            return None
    
    def get_first_layer_of_frame(
        self, class_name: str, frame_index: int = 0
    ) -> Optional[Dict[str, Any]]:
        """Get the first (topmost) layer of a specific frame."""
        objects = self.get_frame_objects(class_name, frame_index)
        if objects:
            return max(objects, key=lambda x: x['depth'])
        return None
    
    def get_texture_matrix(
        self, class_name: str, frame_index: int
    ) -> Optional[Dict[str, Any]]:
        """Get the MATRIX data for the "tex" (texture) layer if it exists."""
        objects = self.get_frame_objects(class_name, frame_index)
        if not objects:
            return None
        
        for obj in objects:
            if obj.get('name', '').strip().lower() in ('tex', 'text', 'texture'):
                return obj.get('matrix')
        
        return None
    
    def get_shape_bounds(self, character_id: int) -> Optional[Dict[str, int]]:
        """Get shape bounds from shapes.db for a DefinedShape character_id.
        
        Returns:
            Dict with keys: bounds_xMin, bounds_xMax, bounds_yMin, bounds_yMax (in twips)
            or None if not found or error
        """
        conn = self._get_shapes_connection()
        if conn is None:
            return None
        
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT bounds_x_min, bounds_x_max, bounds_y_min, bounds_y_max
            FROM defined_shapes
            WHERE character_id = ?
            ''', (int(character_id),))
            
            row = cursor.fetchone()
            if row:
                return {
                    'bounds_x_min': row['bounds_x_min'],
                    'bounds_x_max': row['bounds_x_max'],
                    'bounds_y_min': row['bounds_y_min'],
                    'bounds_y_max': row['bounds_y_max'],
                }
        except Exception as e:
            logger.debug(f"Failed to query bounds for character {character_id}: {e}")
        
        return None
    
    def get_shape_center_px(self, character_id: int) -> Optional[tuple[float, float]]:
        """Calculate the pivot point of a shape in pixels from its bounds.
        
        In SWF, the shape's coordinate space treats (0,0) as the registration point/pivot.
        The bounds define min/max extents around this pivot. To find where the pivot sits
        within the rendered image:
        
          pivot_x_px = 0 - (Xmin / 20.0) = -Xmin / 20.0 (offset from left edge)
          pivot_y_px = Ymax / 20.0 (offset from bottom edge, measured upward)
        
        Note: Y axis is measured from bottom upward (not top downward), so we use Ymax
        instead of -Ymin for the Y pivot calculation.
        
        Args:
            character_id: The DefinedShape character ID
        
        Returns:
            Tuple of (pivot_x_px, pivot_y_px) - position of pivot within rendered image bounds
        """
        bounds = self.get_shape_bounds(character_id)
        if bounds is None:
            return None
        
        # Pivot position: offset from image edges where (0,0) in shape space sits
        pivot_x = -bounds['bounds_x_min'] / 20.0
        pivot_y = bounds['bounds_y_max'] / 20.0
        
        return (pivot_x, pivot_y)
