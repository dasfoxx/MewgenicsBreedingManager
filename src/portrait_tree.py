import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Hash utilities  (unchanged from before)
# ---------------------------------------------------------------------------

def _murmur_finalizer(val: int, addend: int) -> int:
    MASK = 0xffffffffffffffff
    MUL1 = (-0x40a7b892e31b1a47) & MASK
    MUL2 = (-0x6b2fb644ecceee15) & MASK
    h = (val + addend) & MASK
    h = ((h >> 0x1e) ^ h) & MASK
    h = (h * MUL1) & MASK
    h = ((h >> 0x1b) ^ h) & MASK
    h = (h * MUL2) & MASK
    h = ((h >> 0x1f) ^ h) & MASK
    return h

_SEED_ADDENDS = [
    0x9e3779b97f4a7c15,
    0x3c6ef372fe94f82a,
    0xdaa66d2c7ddf743f,
    0x78dde6e5fd29f054,
]

def compute_cat_color_seed(node_id: int) -> tuple[int,int,int,int]:
    return tuple(_murmur_finalizer(node_id, a) for a in _SEED_ADDENDS)  # type: ignore

GENDER_STRINGS = {0: "male", 1: "female"}
def gender_string(g: int) -> str:
    return GENDER_STRINGS.get(g, "neutral")

# ---------------------------------------------------------------------------
# Collision shape  (FUN_1409f7c90)
# ---------------------------------------------------------------------------

# 37 points, 10 degrees apart, radius 0.2 — a circular collision hull
# around each portrait card center.
NODE_COLLISION_RADIUS = 0.2
NODE_COLLISION_STEPS  = 0x25   # 37

def collision_circle(cx: float, cy: float) -> list[tuple[float, float]]:
    """
    Reproduce the collision hull the game places around each node.
    FUN_1409f7c90 loops iVar6 from 0..36, stepping 10° per iteration:
      angle = iVar6 * 0.17453292519943295   (== degrees_to_radians(10))
      px = cos(angle) * 0.2 + cx
      py = sin(angle) * 0.2 + cy
    These points are fed into the graph's physics engine for repulsion.
    """
    step = 0.17453292519943295  # 10 degrees in radians
    return [
        (math.cos(i * step) * NODE_COLLISION_RADIUS + cx,
         math.sin(i * step) * NODE_COLLISION_RADIUS + cy)
        for i in range(NODE_COLLISION_STEPS)
    ]

# ---------------------------------------------------------------------------
# Edge geometry  (FUN_14017b840 + FUN_14017bbf0)
# ---------------------------------------------------------------------------

# How far from each card center the connector line starts/ends.
# Matches the 0.2 literal used throughout FUN_14017b840.
EDGE_INSET = 0.2

@dataclass
class StraightEdge:
    """One-parent connection — a straight line between two cards."""
    x1: float; y1: float   # start (inset from parent card center)
    x2: float; y2: float   # end   (inset from child  card center)


@dataclass
class BezierEdge:
    """
    Two-parent connection — the classic family-tree V-shape.
    FUN_14017bbf0 is called twice (once per parent), each time
    receiving four (x,y) points that form a cubic bezier segment.
    We store both halves so a renderer can draw the full V.
    """
    # Left parent → child midpoint
    lp_p0: tuple[float,float]  # left parent inset point
    lp_p1: tuple[float,float]  # left control point
    lp_p2: tuple[float,float]  # right control point
    lp_p3: tuple[float,float]  # child inset top (left side)

    # Right parent → child midpoint
    rp_p0: tuple[float,float]
    rp_p1: tuple[float,float]
    rp_p2: tuple[float,float]
    rp_p3: tuple[float,float]


def _normalize(dx: float, dy: float) -> tuple[float, float]:
    d = math.sqrt(dx*dx + dy*dy)
    if d == 0.0:
        return 0.0, 0.0
    return dx/d, dy/d


def _straight_edge(child_x: float, child_y: float,
                   parent_x: float, parent_y: float) -> StraightEdge:
    """
    Straight-line connector used when only one parent slot is filled.
    FUN_14017b840 if-branch:
      dx, dy = normalize(child_pos - parent_pos)
      start  = parent_pos + (dx, dy) * 0.2
      end    = child_pos  - (dx, dy) * 0.2
    """
    dx, dy = _normalize(child_x - parent_x, child_y - parent_y)
    return StraightEdge(
        x1 = parent_x + dx * EDGE_INSET,
        y1 = parent_y + dy * EDGE_INSET,
        x2 = child_x  - dx * EDGE_INSET,
        y2 = child_y  - dy * EDGE_INSET,
    )


def _bezier_edge(child_x: float, child_y: float,
                 parent_a_x: float, parent_a_y: float,
                 parent_b_x: float, parent_b_y: float,
                 mid_y_offset: float) -> BezierEdge:
    """
    Two-parent V-shape connector with inverted Y-axis.
    
    Layout: selected cat at y=0, ancestors go to NEGATIVE y (downward on screen).
    
    Line flow:
    1. Start from parent bottom (parent_y - 0.2)
    2. Go down toward junction between parents
    3. Meet at a y-level slightly below the child (child_y - mid_y_offset)
    4. Go up to child top (child_y + 0.2)
    """
    # Junction point: between the two parents, slightly below the child
    # Since y is negative going down, the junction is at a lower (more negative) y value
    junction_y = child_y - mid_y_offset

    left_parent_a = BezierEdge(
        # Left parent: from parent bottom down to junction, then up to child top
        lp_p0 = (parent_a_x, parent_a_y - EDGE_INSET),  # parent_a bottom
        lp_p1 = (parent_a_x, junction_y),                # control point: go down from parent
        lp_p2 = (child_x,    junction_y),                # control point: move toward child horizontally
        lp_p3 = (child_x,    child_y + EDGE_INSET),     # child top
        
        # Right parent: same structure
        rp_p0 = (parent_b_x, parent_b_y - EDGE_INSET),
        rp_p1 = (parent_b_x, junction_y),
        rp_p2 = (child_x,    junction_y),
        rp_p3 = (child_x,    child_y + EDGE_INSET),
    )
    return left_parent_a

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CatNode:
    id:        int
    parent_ids: list[int]   # 0, 1, or 2 entries  (node+0x20, node+0x28)
    gender:    int           # 0=male 1=female 2=neutral
    name:      str
    mid_y_offset: float = 0.5   # node+0x68 — V-curve midpoint height

    # Filled by layout / setup:
    position:     tuple[float,float]      = (0.0, 0.0)
    color_seed:   tuple[int,int,int,int]  = (0,0,0,0)
    gender_label: str                     = ""
    collision_hull: list[tuple[float,float]] = field(default_factory=list)


@dataclass
class EdgeResult:
    """Unified output — either a straight or bezier edge."""
    child: CatNode
    parents: list[CatNode]
    geometry: StraightEdge | BezierEdge


@dataclass
class FamilyTreeHUD:
    display_depth: int = 5
    nodes:  list[CatNode]   = field(default_factory=list)
    edges:  list[EdgeResult] = field(default_factory=list)

    min_x: float = 0.0; max_x: float = 0.0
    min_y: float = 0.0; max_y: float = 0.0
    center_x: float = 0.0; center_y: float = 0.0
    scale:    float = 1.0
    panel_width:  float = 640.0
    panel_height: float = 360.0


# ---------------------------------------------------------------------------
# Phase 1 — portrait setup  (FUN_14017ad10)
# ---------------------------------------------------------------------------

def setup_node_portrait(node: CatNode) -> None:
    node.color_seed   = compute_cat_color_seed(node.id)
    node.gender_label = gender_string(node.gender)


# ---------------------------------------------------------------------------
# Phase 2a — layout  (FUN_14017b690 first loop + FUN_1409f7c90)
# ---------------------------------------------------------------------------

def layout_tree(nodes: list[CatNode], show_thumbnails: bool = False) -> None:
    """
    Simple ancestor tree layout: place the root (first node, the selected cat) at origin,
    then spread ancestors in a cone pattern above it.
    
    Uses fixed spacing in world units. The panel will be sized dynamically to fit
    the resulting layout, accounting for card dimensions.
    
    Args:
        nodes: List of nodes to layout
        show_thumbnails: If True, use spacing for 110x130 px cards
    """
    if not nodes:
        return
    
    id_map: dict[int, CatNode] = {n.id: n for n in nodes}
    
    # Build parent->children map
    parent_to_child: dict[int, list[int]] = {n.id: [] for n in nodes}
    for node in nodes:
        for pid in node.parent_ids:
            if pid in id_map:
                parent_to_child[pid].append(node.id)

    # Fixed spacing for world units
    if show_thumbnails:
        VERT  = 2.5   # Space between generations
        HORIZ = 1.5   # Horizontal spacing between siblings
    else:
        VERT  = 1.0
        HORIZ = 0.5
    
    # Root is the first node (selected cat)
    root = nodes[0]
    root.position = (0.0, 0.0)
    
    # Process generation by generation, spreading ancestors horizontally
    # Ancestors go DOWNWARD (negative y) from the selected cat
    current_gen = [root]
    gen_y = -VERT  # Start below the root
    
    while current_gen:
        next_gen = []
        next_gen_set = set()
        
        # Collect all unique parents
        for node in current_gen:
            for pid in node.parent_ids:
                if pid in id_map and pid not in next_gen_set:
                    next_gen.append(id_map[pid])
                    next_gen_set.add(pid)
        
        if not next_gen:
            break
        
        # Position this generation in a horizontal line
        num_parents = len(next_gen)
        
        if num_parents == 1:
            next_gen[0].position = (0.0, gen_y)
        else:
            spread_width = HORIZ * num_parents * 1.5
            start_x = -spread_width / 2.0
            
            for i, parent in enumerate(next_gen):
                x = start_x + (spread_width / (num_parents - 1)) * i if num_parents > 1 else 0.0
                parent.position = (x, gen_y)
        
        gen_y -= VERT  # Go further down
        current_gen = next_gen

    # FUN_1409f7c90: build the circular collision hull for each node
    for node in nodes:
        cx, cy = node.position
        node.collision_hull = collision_circle(cx, cy)


# ---------------------------------------------------------------------------
# Phase 2b — edge geometry  (FUN_14017b690 second loop → FUN_14017b840)
# ---------------------------------------------------------------------------

def build_edges(nodes: list[CatNode]) -> list[EdgeResult]:
    """
    Reproduce FUN_14017b840's two branches for every node that has parents.
    """
    id_map = {n.id: n for n in nodes}
    results: list[EdgeResult] = []

    for child in nodes:
        live_parents = [id_map[pid] for pid in child.parent_ids if pid in id_map]

        if len(live_parents) == 0:
            continue

        cx, cy = child.position

        if len(live_parents) == 2:
            # else-branch: both parent slots filled → bezier V-shape
            pa, pb = live_parents
            geom = _bezier_edge(
                child_x=cx, child_y=cy,
                parent_a_x=pa.position[0], parent_a_y=pa.position[1],
                parent_b_x=pb.position[0], parent_b_y=pb.position[1],
                mid_y_offset=child.mid_y_offset,
            )
        else:
            # if-branch: only one parent → straight line
            pa = live_parents[0]
            geom = _straight_edge(cx, cy, pa.position[0], pa.position[1])

        results.append(EdgeResult(child=child, parents=live_parents, geometry=geom))

    return results


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def compute_camera(hud: FamilyTreeHUD) -> None:
    if not hud.nodes:
        return
    xs = [n.position[0] for n in hud.nodes]
    ys = [n.position[1] for n in hud.nodes]
    hud.min_x = min(xs) - 1.0;  hud.max_x = max(xs) + 1.0
    hud.min_y = min(ys) - 1.0;  hud.max_y = max(ys) + 1.0
    hud.center_x = (hud.min_x + hud.max_x) * 0.5
    hud.center_y = (hud.min_y + hud.max_y) * 0.5
    world_w = hud.max_x - hud.min_x
    world_h = hud.max_y - hud.min_y
    # Use max() to maximize spacing in at least one dimension
    # This ensures the tree has visible vertical separation even when it's wider than tall
    sx = hud.panel_width  / world_w if world_w > 0 else 1.0
    sy = hud.panel_height / world_h if world_h > 0 else 1.0
    hud.scale = min(sx, sy)

def world_to_screen(wx: float, wy: float, hud: FamilyTreeHUD) -> tuple[int,int]:
    return (
        int((wx - hud.center_x) * hud.scale + hud.panel_width  * 0.5),
        int((wy - hud.center_y) * hud.scale + hud.panel_height * 0.5),
    )

def bezier_point(p0, p1, p2, p3, t):
    """Cubic bezier at parameter t."""
    u = 1 - t
    return (
        u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0],
        u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1],
    )

def render_edge(edge: EdgeResult, hud: FamilyTreeHUD, draw_line, draw_curve):
    """
    draw_line(x1,y1,x2,y2) and draw_curve(points) are your renderer callbacks.
    """
    g = edge.geometry

    if isinstance(g, StraightEdge):
        ax, ay = world_to_screen(g.x1, g.y1, hud)
        bx, by = world_to_screen(g.x2, g.y2, hud)
        draw_line(ax, ay, bx, by)

    elif isinstance(g, BezierEdge):
        # Draw left-parent arm
        pts = [bezier_point(g.lp_p0, g.lp_p1, g.lp_p2, g.lp_p3, t/20)
               for t in range(21)]
        draw_curve([world_to_screen(*p, hud) for p in pts])

        # Draw right-parent arm
        pts = [bezier_point(g.rp_p0, g.rp_p1, g.rp_p2, g.rp_p3, t/20)
               for t in range(21)]
        draw_curve([world_to_screen(*p, hud) for p in pts])
# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def create_family_tree_hud(
    cat_nodes: list[CatNode],
    display_depth: int = 5,
    panel_width:  float = 640.0,
    panel_height: float = 360.0,
    show_thumbnails: bool = False,
) -> FamilyTreeHUD:

    hud = FamilyTreeHUD(
        display_depth=display_depth,
        nodes=list(cat_nodes),
        panel_width=panel_width,
        panel_height=panel_height,
    )

    for node in hud.nodes:          # FUN_14017ad10
        setup_node_portrait(node)

    layout_tree(hud.nodes, show_thumbnails=show_thumbnails)  # FUN_14017b690 first loop
    
    # If showing thumbnails, dynamically size the panel to fit all cards
    if show_thumbnails:
        card_width_px = 110
        card_height_px = 130
        padding_px = 20  # space between cards
        
        # Get bounding box of positioned nodes
        if hud.nodes:
            xs = [n.position[0] for n in hud.nodes]
            ys = [n.position[1] for n in hud.nodes]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            
            # Conversion factor: pixels per world unit
            # Use the card width relative to HORIZ spacing (1.5 world units)
            pixels_per_world_unit = card_width_px / 1.5
            
            # Calculate required panel size
            world_width = max_x - min_x
            world_height = max_y - min_y
            
            # Screen space: world distance * pixels_per_unit + card dimensions + padding
            required_width = world_width * pixels_per_world_unit + card_width_px + padding_px * 2
            required_height = world_height * pixels_per_world_unit + card_height_px + padding_px * 2
            
            # Update HUD panel size
            hud.panel_width = required_width
            hud.panel_height = required_height
    
    hud.edges = build_edges(hud.nodes)  # FUN_14017b690 second loop
    compute_camera(hud)
    return hud