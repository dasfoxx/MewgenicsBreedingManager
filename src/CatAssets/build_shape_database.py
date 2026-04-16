#!/usr/bin/env python3
"""
DefinedShape Database Builder

Parses catparts_timeline.xml and builds a single SQLite database containing
metadata about all DefinedShape, DefineShape2Tag, DefineShape3Tag, DefineShape4Tag entries.

This supplements the DefinedSprite databases and provides shape metadata that may be
useful for rendering context, bounds checking, or advanced rendering scenarios.
"""

import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger("shape_db_builder")
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


class ShapeDatabaseBuilder:
    """Builds a single .db file containing all DefinedShape metadata"""
    
    def __init__(self, xml_path: Path, db_dir: Path):
        self.xml_path = xml_path
        self.db_dir = db_dir
        self.db_dir.mkdir(parents=True, exist_ok=True)
        
    def load_xml(self) -> ET.Element:
        """Load and parse the catparts_timeline.xml file"""
        logger.info(f"Loading XML from {self.xml_path}")
        tree = ET.parse(self.xml_path)
        return tree.getroot()
    
    def extract_rect_data(self, rect_elem: Optional[ET.Element]) -> Optional[Dict[str, int]]:
        """Extract RECT bounds data from a shapeBounds element.
        
        The bounds are stored as attributes: Xmin, Xmax, Ymin, Ymax
        """
        if rect_elem is None or rect_elem.tag != 'shapeBounds':
            return None
        
        attrs = rect_elem.attrib
        return {
            'xMin': int(attrs.get('Xmin', 0)),
            'xMax': int(attrs.get('Xmax', 0)),
            'yMin': int(attrs.get('Ymin', 0)),
            'yMax': int(attrs.get('Ymax', 0)),
        }
    
    def extract_matrix_from_fill(self, fill_style_elem: ET.Element) -> Optional[Dict[str, Any]]:
        """Extract matrix data from a FillStyle's bitmapMatrix if present"""
        matrix_elem = fill_style_elem.find('bitmapMatrix')
        if matrix_elem is None or matrix_elem.tag != 'matrix':
            return None
        
        attrs = matrix_elem.attrib
        return {
            'hasScale': attrs.get('hasScale') == 'true',
            'hasRotate': attrs.get('hasRotate') == 'false',
            'scaleX': float(attrs.get('scaleX', 1.0)),
            'scaleY': float(attrs.get('scaleY', 1.0)),
            'translateX': int(attrs.get('translateX', 0)),
            'translateY': int(attrs.get('translateY', 0)),
        }
    
    def extract_shape_data(self, shape_elem: ET.Element) -> Optional[Dict[str, Any]]:
        """Extract metadata from a DefineShape/DefineShape2Tag/etc element"""
        attrs = shape_elem.attrib
        
        data = {
            'characterId': int(attrs.get('characterId', 0)),
            'shapeType': shape_elem.attrib.get('type', 'Unknown'),
        }
        
        # Extract bounds
        bounds_elem = shape_elem.find('shapeBounds')
        if bounds_elem is not None:
            bounds = self.extract_rect_data(bounds_elem)
            if bounds:
                data.update({
                    'bounds_xMin': bounds['xMin'],
                    'bounds_xMax': bounds['xMax'],
                    'bounds_yMin': bounds['yMin'],
                    'bounds_yMax': bounds['yMax'],
                })
        
        # Count fill styles (useful for understanding shape complexity)
        shapes_elem = shape_elem.find('shapes')
        fill_count = 0
        line_count = 0
        
        if shapes_elem is not None:
            fill_styles = shapes_elem.find('fillStyles')
            if fill_styles is not None:
                fill_count = len([child for child in fill_styles if child.tag in ['fillStyle', 'FILLSTYLE']])
            
            line_styles = shapes_elem.find('lineStyles')
            if line_styles is not None:
                line_count = len([child for child in line_styles if child.tag in ['lineStyle', 'LINESTYLE']])
        
        data['fill_style_count'] = fill_count
        data['line_style_count'] = line_count
        
        return data
    
    def create_shape_database(self, shapes: List[Dict[str, Any]]) -> Path:
        """Create a shapes.db file containing all DefinedShape metadata"""
        db_file = self.db_dir / "shapes.db"
        
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            
            # Create shapes table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS defined_shapes (
                character_id INTEGER PRIMARY KEY,
                shape_type TEXT,
                bounds_x_min INTEGER,
                bounds_x_max INTEGER,
                bounds_y_min INTEGER,
                bounds_y_max INTEGER,
                fill_style_count INTEGER,
                line_style_count INTEGER
            )
            ''')
            
            # Insert shape data
            inserted_count = 0
            for shape in shapes:
                try:
                    cursor.execute('''
                    INSERT OR REPLACE INTO defined_shapes
                    (character_id, shape_type,
                     bounds_x_min, bounds_x_max, bounds_y_min, bounds_y_max,
                     fill_style_count, line_style_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shape.get('characterId'),
                        shape.get('shapeType'),
                        shape.get('bounds_xMin'),
                        shape.get('bounds_xMax'),
                        shape.get('bounds_yMin'),
                        shape.get('bounds_yMax'),
                        shape.get('fill_style_count', 0),
                        shape.get('line_style_count', 0),
                    ))
                    inserted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to insert shape {shape.get('characterId')}: {e}")
            
            conn.commit()
            conn.close()
            
            logger.info(f"Created shapes.db with {inserted_count} entries")
            return db_file
            
        except Exception as e:
            logger.error(f"Failed to create shapes database: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def extract_all_shapes(self, root: ET.Element) -> List[Dict[str, Any]]:
        """Extract all top-level DefineShape entries from tags list"""
        shapes = []
        
        tags = root.find('tags')
        if tags is None:
            return shapes
        
        items = list(tags)
        
        # Process top-level items - read the shapeId or characterId attribute
        for index, item in enumerate(items):
            element_type = item.attrib.get('type', '')
            
            if element_type in ['DefineShapeTag', 'DefineShape2Tag', 'DefineShape3Tag', 'DefineShape4Tag']:
                # Read the actual character ID from the XML attributes, not the position
                char_id = item.attrib.get('shapeId') or item.attrib.get('characterId')
                if char_id is None:
                    logger.warning(f"Shape at index {index} has no shapeId or characterId attribute")
                    continue
                
                shape_data = {
                    'characterId': int(char_id),
                    'shapeType': element_type,
                }
                
                # Extract bounds
                bounds_elem = item.find('shapeBounds')
                if bounds_elem is not None:
                    bounds = self.extract_rect_data(bounds_elem)
                    if bounds:
                        shape_data.update({
                            'bounds_xMin': bounds['xMin'],
                            'bounds_xMax': bounds['xMax'],
                            'bounds_yMin': bounds['yMin'],
                            'bounds_yMax': bounds['yMax'],
                        })
                
                # Count fill/line styles
                shapes_elem = item.find('shapes')
                fill_count = 0
                line_count = 0
                
                if shapes_elem is not None:
                    fill_styles = shapes_elem.find('fillStyles')
                    if fill_styles is not None:
                        fill_count = len([child for child in fill_styles if child.tag in ['fillStyle', 'FILLSTYLE']])
                    
                    line_styles = shapes_elem.find('lineStyles')
                    if line_styles is not None:
                        line_count = len([child for child in line_styles if child.tag in ['lineStyle', 'LINESTYLE']])
                
                shape_data['fill_style_count'] = fill_count
                shape_data['line_style_count'] = line_count
                
                shapes.append(shape_data)
        
        return shapes
    
    def build_all(self) -> None:
        """Execute full build process"""
        logger.info("=" * 80)
        logger.info("DefinedShape Database Builder")
        logger.info("=" * 80)
        
        root = self.load_xml()
        shapes = self.extract_all_shapes(root)
        
        logger.info(f"Found {len(shapes)} DefinedShape entries")
        
        if shapes:
            self.create_shape_database(shapes)
        
        logger.info("=" * 80)
        logger.info("Build complete!")
        logger.info("=" * 80)


if __name__ == '__main__':
    src_dir = Path(__file__).parent / "src"
    xml_path = src_dir / "catparts_timeline.xml"
    db_dir = src_dir / "CatAssets" / "swf_database"
    
    builder = ShapeDatabaseBuilder(xml_path, db_dir)
    builder.build_all()
