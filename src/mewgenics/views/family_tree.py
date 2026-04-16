from __future__ import annotations

import math
from typing import Optional

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QThread, QPointF
from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QPainter, QPen, QColor, QPainterPath

from save_parser import Cat
from mewgenics.models.cat_table_model import _SortKeyItem
from mewgenics.utils.localization import _tr
from mewgenics.utils.styling import _enforce_min_font_in_widget_tree, _sidebar_btn
from mewgenics.utils.tags import _make_tag_icon, _cat_tags
from mewgenics.utils.config import _load_app_config, _save_app_config
from CatAssets.CatAssetsLoader import ensure_defineshape_pngdata
from portrait_tree import (
    CatNode as HudCatNode,
    create_family_tree_hud,
    world_to_screen,
    BezierEdge,
    StraightEdge,
    EDGE_INSET,
)

try:
    import swf_cat_renderer
    _SWF_RENDERER_AVAILABLE = True
except Exception:
    _SWF_RENDERER_AVAILABLE = False

_FAMILY_TREE_THUMBNAILS_KEY = "family_tree_show_thumbnails"
_FAMILY_TREE_PORTRAIT_STYLE_KEY = "family_tree_portrait_style"
_THUMB_SIZE = 96  # px
_CARD_W = 110    # world-space node card width in pixels (for overlap guard)
_CARD_H = 130    # includes thumbnail + label


def _load_family_tree_show_thumbnails() -> bool:
    return bool(_load_app_config().get(_FAMILY_TREE_THUMBNAILS_KEY, False))

def _save_family_tree_show_thumbnails(enabled: bool) -> None:
    data = _load_app_config()
    data[_FAMILY_TREE_THUMBNAILS_KEY] = bool(enabled)
    _save_app_config(data)

def _load_family_tree_portrait_style() -> bool:
    return bool(_load_app_config().get(_FAMILY_TREE_PORTRAIT_STYLE_KEY, False))

def _save_family_tree_portrait_style(enabled: bool) -> None:
    data = _load_app_config()
    data[_FAMILY_TREE_PORTRAIT_STYLE_KEY] = bool(enabled)
    _save_app_config(data)


# ---------------------------------------------------------------------------
# Thumbnail preload worker (unchanged)
# ---------------------------------------------------------------------------

class FamilyTreeThumbnailPreloadWorker(QThread):
    def __init__(self, cats: list[Cat], thumbnail_size: int, parent=None):
        super().__init__(parent)
        self._cats = list(cats)
        self._thumbnail_size = int(thumbnail_size)

    def run(self):
        if not _SWF_RENDERER_AVAILABLE:
            return
        try:
            for cat in self._cats:
                if self.isInterruptionRequested():
                    return
                swf_cat_renderer.render_cat_thumbnail(cat, size=self._thumbnail_size)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HUD canvas: draws bezier/straight edges and places portrait cards
# ---------------------------------------------------------------------------

class _HudEdgeOverlay(QWidget):
    """
    Transparent overlay painted on top of the canvas widget.
    Receives pre-computed screen-space bezier/straight segments and repaints them.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self._segments: list[tuple] = []   # list of ('bezier'|'straight', *coords)

    def set_segments(self, segments: list[tuple]):
        self._segments = segments
        self.update()

    def paintEvent(self, _event):
        if not self._segments:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#3d2817"))
        pen.setWidth(3)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)

        for seg in self._segments:
            kind = seg[0]
            if kind == 'straight':
                _, x1, y1, x2, y2 = seg
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
            elif kind == 'bezier':
                # seg = ('bezier', p0, p1, p2, p3)  each is (x, y) floats
                _, p0, p1, p2, p3 = seg
                path = QPainterPath()
                path.moveTo(QPointF(*p0))
                path.cubicTo(QPointF(*p1), QPointF(*p2), QPointF(*p3))
                p.drawPath(path)

        p.end()


class FamilyTreeHudCanvas(QWidget):
    """
    Absolute-position canvas for the HUD-style portrait tree.
    Nodes are placed at world→screen positions; edges are drawn by the overlay.
    """

    # Padding around the whole canvas so cards at the edges aren't clipped.
    _PAD = 20

    def __init__(
        self,
        cat: Cat,
        by_key: dict[int, Cat],
        open_cat_cb,
        show_thumbnails: bool,
        panel_w: float = 900.0,
        panel_h: float = 700.0,
        parent=None,
    ):
        super().__init__(parent)
        self._open_cat_cb = open_cat_cb
        self._show_thumbnails = show_thumbnails
        self._by_key = by_key

        # Build HUD nodes from the Cat object and all its ancestors (limited to max_ancestors depth)
        # max_ancestors=5 means go back 5 generations; all cats at those levels are included
        root_cat, ancestor_levels, _ = _get_lineage_cats(cat, max_ancestors=5)
        # Flatten the ancestor levels: [root_cat, parents, grandparents, ...]
        lineage_cats = [root_cat] + [c for level in ancestor_levels for c in level]
        hud_nodes, cat_for_node = _make_hud_nodes(lineage_cats)
        hud = create_family_tree_hud(
            hud_nodes,
            display_depth=5,
            panel_width=panel_w,
            panel_height=panel_h,
            show_thumbnails=show_thumbnails,
        )
        self._hud = hud
        self._cat_for_node = cat_for_node   # HudCatNode.id → Cat

        # Convert world positions to canvas pixels
        # world_to_screen uses hud.scale; we add _PAD so nothing clips the edge.
        def w2s(wx, wy):
            sx, sy = world_to_screen(wx, wy, hud)
            return sx + self._PAD, sy + self._PAD

        # Determine required canvas size
        all_sx = [w2s(n.position[0], n.position[1])[0] for n in hud.nodes]
        all_sy = [w2s(n.position[0], n.position[1])[1] for n in hud.nodes]
        canvas_w = int(max(all_sx) + _CARD_W + self._PAD * 2) if all_sx else 900
        canvas_h = int(max(all_sy) + _CARD_H + self._PAD * 2) if all_sy else 700
        self.setFixedSize(canvas_w, canvas_h)

        # Edge overlay (drawn behind cards)
        self._overlay = _HudEdgeOverlay(self)
        self._overlay.setGeometry(0, 0, canvas_w, canvas_h)
        self._overlay.lower()

        # Place one card widget per node
        for node in hud.nodes:
            sx, sy = w2s(node.position[0], node.position[1])
            orig_cat = cat_for_node.get(node.id)
            card = self._make_card(orig_cat, highlight=(orig_cat is cat))
            cw, ch = card.sizeHint().width(), card.sizeHint().height()
            # Centre the card on the screen position
            card.setParent(self)
            card.move(int(sx - cw / 2), int(sy - ch / 2))
            card.show()

        # Build edge segments in screen space
        segments: list[tuple] = []
        for edge in hud.edges:
            g = edge.geometry

            if isinstance(g, StraightEdge):
                # Transform pre-computed world-space inset points to screen space
                x1, y1 = world_to_screen(g.x1, g.y1, hud)
                x2, y2 = world_to_screen(g.x2, g.y2, hud)
                segments.append(('straight', x1, y1, x2, y2))
                
            elif isinstance(g, BezierEdge):
                # Transform pre-computed bezier control points to screen space
                # Left parent arm
                lp_p0 = world_to_screen(*g.lp_p0, hud)
                lp_p1 = world_to_screen(*g.lp_p1, hud)
                lp_p2 = world_to_screen(*g.lp_p2, hud)
                lp_p3 = world_to_screen(*g.lp_p3, hud)
                segments.append(('bezier', lp_p0, lp_p1, lp_p2, lp_p3))
                
                # Right parent arm
                rp_p0 = world_to_screen(*g.rp_p0, hud)
                rp_p1 = world_to_screen(*g.rp_p1, hud)
                rp_p2 = world_to_screen(*g.rp_p2, hud)
                rp_p3 = world_to_screen(*g.rp_p3, hud)
                segments.append(('bezier', rp_p0, rp_p1, rp_p2, rp_p3))

        self._overlay.set_segments(segments)
        self._overlay.raise_()   # paint over everything, then mouse events pass through

    def _make_card(self, cat: Optional[Cat], highlight: bool) -> QWidget:
        """Build a portrait card widget using the shared cat button function."""
        return _make_cat_button(
            cat=cat,
            on_click_cb=self._open_cat_cb,
            is_selected=highlight,
            show_thumbnail=self._show_thumbnails,
        )


# ---------------------------------------------------------------------------
# Shared lineage and card creation logic
# ---------------------------------------------------------------------------

def _get_lineage_cats(cat: Cat, max_ancestors: int = 5) -> tuple[Cat, list[list[Cat]], list[Cat]]:
    """
    Get all cats needed for lineage display: the root cat, its ancestors organized by generation,
    and its children/grandchildren.
    
    Returns:
        (root_cat, ancestor_levels, children_all)
        - ancestor_levels: list of lists [parents, grandparents, ...]
        - children_all: flattened list of all children and grandchildren
    """
    # Build ancestor levels
    ancestor_levels: list[list[Cat]] = []
    frontier: list[Cat] = [cat]
    
    for _ in range(max_ancestors):
        nxt: list[Cat] = []
        for node in frontier:
            if node.parent_a is not None:
                nxt.append(node.parent_a)
            if node.parent_b is not None:
                nxt.append(node.parent_b)
        if not nxt:
            break
        ancestor_levels.append(nxt)
        frontier = nxt
    
    # Get children and grandchildren
    children = list(cat.children)
    grandchildren: list[Cat] = []
    for child in children:
        grandchildren.extend(child.children)
    grandchildren = list({id(c): c for c in grandchildren}.values())
    children_all = children + grandchildren
    
    return cat, ancestor_levels, children_all


def _make_cat_button(
    cat: Optional[Cat],
    on_click_cb,
    is_selected: bool = False,
    show_thumbnail: bool = False,
) -> QWidget:
    """
    Create a shared cat button/card widget used by both legacy and portrait views.
    
    Args:
        cat: Cat to display, or None for unknown cat
        on_click_cb: Callback when button is clicked
        is_selected: If True, highlight the button and disable it
        show_thumbnail: If True and available, include the cat's thumbnail image
    """
    if cat is None:
        btn = QPushButton(_tr("family_tree.unknown"))
        btn.setEnabled(False)
        btn.setStyleSheet(
            "QPushButton { color:#303040; font-size:10px; padding:7px 10px;"
            " background:#0e0e1c; border:1px solid #18182a; border-radius:6px; }")
        return btn

    line2 = cat.gender_display or ""
    if cat.room_display:
        if line2:
            line2 += f"  {cat.room_display}"
        else:
            line2 = cat.room_display
    if cat.status == "Gone":
        if line2:
            line2 += f"  ({_tr('status.gone')})"
        else:
            line2 = _tr("status.gone")
    
    bg     = "#1d2f4a" if is_selected else "#131326"
    border = "#3b5f95" if is_selected else "#252545"
    
    btn = QPushButton(f"{cat.name}\n{line2}" if line2 else cat.name)
    icon = _make_tag_icon(_cat_tags(cat), dot_size=14, spacing=4)
    if not icon.isNull():
        btn.setIcon(icon)
    btn.setStyleSheet(
        f"QPushButton {{ color:#ddd; font-size:10px; padding:7px 10px;"
        f" background:{bg}; border:1px solid {border}; border-radius:6px; }}"
        "QPushButton:hover { background:#1a2a46; }")
    btn.setMinimumWidth(110)
    
    if is_selected:
        btn.setEnabled(False)
    else:
        btn.clicked.connect(lambda checked=False: on_click_cb(cat))

    # Add thumbnail if requested and available
    if show_thumbnail and _SWF_RENDERER_AVAILABLE:
        try:
            png = swf_cat_renderer.render_cat_thumbnail(cat, size=_THUMB_SIZE)
            if png:
                pix = QPixmap()
                pix.loadFromData(bytes(png))
                card = QWidget()
                card.setStyleSheet(
                    f"QWidget {{ background:{bg}; border:1px solid {border};"
                    " border-radius:6px; }")
                cv = QVBoxLayout(card)
                cv.setContentsMargins(4, 4, 4, 0)
                cv.setSpacing(2)
                img_lbl = QLabel()
                img_lbl.setPixmap(pix.scaled(
                    _THUMB_SIZE, _THUMB_SIZE,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                img_lbl.setAlignment(Qt.AlignCenter)
                img_lbl.setStyleSheet("border:none; background:transparent;")
                btn.setStyleSheet(
                    f"QPushButton {{ color:#ddd; font-size:10px; padding:4px 6px;"
                    f" background:transparent; border:none; border-radius:0; }}"
                    "QPushButton:hover { color:#fff; }")
                cv.addWidget(img_lbl)
                cv.addWidget(btn)
                return card
        except Exception:
            pass
    
    return btn


# ---------------------------------------------------------------------------
# Thumbnail preview card (used in both legacy and portrait HUD modes)
# ---------------------------------------------------------------------------

def _make_hud_nodes(cats: list[Cat]) -> tuple[list[HudCatNode], dict[int, Cat]]:
    """
    Convert a list of Cat objects to HudCatNode objects with proper parent references.
    
    Args:
        cats: List of Cat objects to convert
        
    Returns:
        (hud_nodes, cat_for_node) where cat_for_node maps HudCatNode.id to Cat
    """
    visited: dict[int, HudCatNode] = {}
    cat_for_node: dict[int, Cat] = {}
    
    for c in cats:
        if c.db_key in visited:
            continue
        parent_ids = []
        if c.parent_a is not None:
            parent_ids.append(c.parent_a.db_key)
        if c.parent_b is not None:
            parent_ids.append(c.parent_b.db_key)
        node = HudCatNode(
            id=c.db_key,
            parent_ids=parent_ids,
            gender={"male": 0, "female": 1}.get((c.gender_display or "").lower(), 2),
            name=c.name or "?",
        )
        visited[c.db_key] = node
        cat_for_node[node.id] = c
    
    return list(visited.values()), cat_for_node


# ---------------------------------------------------------------------------
# Main view (merged from both versions)
# ---------------------------------------------------------------------------

class FamilyTreeBrowserView(QWidget):
    COL_NAME = 0
    COL_LOC  = 1
    COL_GEN  = 2
    COL_AGE  = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QListWidget { background:#0d0d1c; color:#ddd; border:1px solid #1e1e38; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QScrollArea { border:none; background:#0a0a18; }"
        )
        self._cats: list[Cat] = []
        self._by_key: dict[int, Cat] = {}
        self._alive_only: bool = True
        self._show_thumbnails: bool = _load_family_tree_show_thumbnails()
        self._portrait_style: bool = _load_family_tree_portrait_style()
        self._thumb_worker: Optional[FamilyTreeThumbnailPreloadWorker] = None

        if self._show_thumbnails and _SWF_RENDERER_AVAILABLE:
            ensure_defineshape_pngdata()

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ── Left pane ────────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(390)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(QLabel(_tr("family_tree.cats"),
                            styleSheet="color:#666; font-size:10px; font-weight:bold;"))
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._all_btn   = _sidebar_btn(_tr("family_tree.filter_all"))
        self._alive_btn = _sidebar_btn(_tr("family_tree.filter_alive"))
        self._all_btn.setToolTip(_tr("family_tree.filter_all.tooltip",
                                     default="Show all cats including gone"))
        self._alive_btn.setToolTip(_tr("family_tree.filter_alive.tooltip",
                                       default="Show only alive cats"))
        self._all_btn.setCheckable(True)
        self._alive_btn.setCheckable(True)
        self._alive_btn.setChecked(True)
        self._all_btn.clicked.connect(lambda: self._set_alive_only(False))
        self._alive_btn.clicked.connect(lambda: self._set_alive_only(True))
        mode_row.addWidget(self._all_btn)
        mode_row.addWidget(self._alive_btn)
        lv.addLayout(mode_row)
        self._search = QLineEdit()
        self._search.setPlaceholderText(_tr("family_tree.search_placeholder"))
        self._search.setToolTip(_tr("family_tree.search.tooltip",
                                    default="Filter cats by name"))
        lv.addWidget(self._search)
        self._list = QTableWidget(0, 4)
        self._list.setHorizontalHeaderLabels(["Name", "Location", "Generation", "Age"])
        self._list.verticalHeader().setVisible(False)
        self._list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setWordWrap(False)
        self._list.setSortingEnabled(True)
        self._list.sortByColumn(self.COL_NAME, Qt.SortOrder.AscendingOrder)
        hh = self._list.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(self.COL_NAME, QHeaderView.Interactive)
        self._list.setColumnWidth(self.COL_NAME, 150)
        hh.setSectionResizeMode(self.COL_LOC, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_GEN, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_AGE, QHeaderView.ResizeToContents)
        lv.addWidget(self._list, 1)
        root.addWidget(left)

        # ── Right pane ───────────────────────────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        _btn_style = (
            "QPushButton { background:#131326; color:#aaa; border:1px solid #252545;"
            " border-radius:4px; padding:4px 10px; font-size:10px; }"
            "QPushButton:checked { background:#1d2f4a; color:#7fb3f5; border-color:#3b5f95; }"
            "QPushButton:hover { background:#1a2040; }"
            "QPushButton:disabled { color:#444; }"
        )
        self._thumb_toggle = QPushButton("Show Cat Images")
        self._thumb_toggle.setCheckable(True)
        self._thumb_toggle.setChecked(self._show_thumbnails)
        self._thumb_toggle.setEnabled(_SWF_RENDERER_AVAILABLE)
        self._thumb_toggle.setToolTip(_tr("family_tree.thumb_toggle.tooltip",
                                          default="Toggle cat image thumbnails in the tree"))
        if not _SWF_RENDERER_AVAILABLE:
            self._thumb_toggle.setToolTip(_tr(
                "family_tree.thumb_toggle_disabled.tooltip",
                default="Cat image rendering requires Pillow and numpy"))
        self._thumb_toggle.setStyleSheet(_btn_style)
        self._thumb_toggle.clicked.connect(self._toggle_thumbnails)

        self._portrait_toggle = QPushButton("Portrait Style")
        self._portrait_toggle.setCheckable(True)
        self._portrait_toggle.setChecked(self._portrait_style)
        self._portrait_toggle.setEnabled(_SWF_RENDERER_AVAILABLE)
        self._portrait_toggle.setVisible(self._show_thumbnails)
        self._portrait_toggle.setToolTip(_tr("family_tree.portrait_toggle.tooltip",
                                             default="Use game-style HUD layout with bezier connectors"))
        self._portrait_toggle.setStyleSheet(_btn_style)
        self._portrait_toggle.clicked.connect(self._toggle_portrait_style)

        btn_row.addWidget(self._thumb_toggle)
        btn_row.addWidget(self._portrait_toggle)
        btn_row.addStretch()
        rv.addLayout(btn_row)

        self._tree_scroll = QScrollArea()
        self._tree_scroll.setWidgetResizable(True)
        self._tree_content = QWidget()
        self._tree_scroll.setWidget(self._tree_content)
        rv.addWidget(self._tree_scroll, 1)
        root.addWidget(right, 1)

        self._search.textChanged.connect(self._refresh_list)
        self._list.currentCellChanged.connect(self._on_current_item_changed)
        _enforce_min_font_in_widget_tree(self)
        self._refresh_filter_button_labels()

    # ── Filter / list helpers ────────────────────────────────────────────────

    def _refresh_filter_button_labels(self):
        total = len(self._cats)
        alive = sum(1 for c in self._cats if c.status != "Gone")
        self._all_btn.setText(f"{_tr('family_tree.filter_all')} ({total})")
        self._alive_btn.setText(f"{_tr('family_tree.filter_alive')} ({alive})")

    def _set_alive_only(self, enabled: bool):
        self._alive_only = enabled
        self._alive_btn.setChecked(enabled)
        self._all_btn.setChecked(not enabled)
        self._refresh_list()

    def _refresh_list(self):
        query = self._search.text().strip().lower()
        current_key = None
        cur = self._list.currentItem()
        if cur is not None:
            current_key = int(cur.data(Qt.UserRole))

        self._list.setSortingEnabled(False)
        self._list.clearContents()
        self._list.setRowCount(0)

        for cat in self._cats:
            if self._alive_only and cat.status == "Gone":
                continue
            if query and query not in cat.name.lower():
                continue
            row = self._list.rowCount()
            self._list.insertRow(row)

            name_item = QTableWidgetItem(cat.name)
            name_item.setData(Qt.UserRole, cat.db_key)
            icon = _make_tag_icon(_cat_tags(cat), dot_size=10, spacing=3)
            if not icon.isNull():
                name_item.setIcon(icon)
            name_item.setToolTip(cat.name)
            self._list.setItem(row, self.COL_NAME, name_item)

            if cat.status == "In House":
                location_text = cat.room_display or _tr("status.in_house")
            else:
                location_text = (_tr("status.gone") if cat.status == "Gone"
                                 else _tr("status.adventure"))
            loc_item = QTableWidgetItem(location_text)
            loc_item.setTextAlignment(Qt.AlignCenter)
            self._list.setItem(row, self.COL_LOC, loc_item)

            gen_item = _SortKeyItem(str(cat.generation))
            gen_item.setData(Qt.UserRole, cat.generation)
            gen_item.setTextAlignment(Qt.AlignCenter)
            self._list.setItem(row, self.COL_GEN, gen_item)

            age_value = getattr(cat, "age", None)
            if cat.status == "Gone":
                age_item = _SortKeyItem("—")
                age_item.setData(Qt.UserRole, 10**9)
            else:
                age_item = _SortKeyItem(str(age_value) if age_value is not None else "—")
                age_item.setData(Qt.UserRole, age_value if age_value is not None else 10**9)
            age_item.setTextAlignment(Qt.AlignCenter)
            self._list.setItem(row, self.COL_AGE, age_item)

        self._list.setSortingEnabled(True)
        self._list.sortByColumn(self.COL_NAME, Qt.SortOrder.AscendingOrder)

        if self._list.rowCount() == 0:
            self._render_tree(None)
            return
        if current_key is not None:
            for row in range(self._list.rowCount()):
                it = self._list.item(row, self.COL_NAME)
                if it is not None and int(it.data(Qt.UserRole)) == current_key:
                    self._list.setCurrentCell(row, self.COL_NAME)
                    return
        self._list.setCurrentCell(0, self.COL_NAME)

    def _on_current_item_changed(self, cr, cc, pr, pc):
        if cr < 0:
            self._render_tree(None)
            return
        current = self._list.item(cr, self.COL_NAME)
        if current is None:
            self._render_tree(None)
            return
        self._render_tree(self._by_key.get(int(current.data(Qt.UserRole))))

    # ── Toggle helpers ───────────────────────────────────────────────────────

    def _toggle_thumbnails(self):
        self._show_thumbnails = self._thumb_toggle.isChecked()
        self._portrait_toggle.setVisible(self._show_thumbnails)
        _save_family_tree_show_thumbnails(self._show_thumbnails)
        self._re_render_current()

    def _toggle_portrait_style(self):
        self._portrait_style = self._portrait_toggle.isChecked()
        _save_family_tree_portrait_style(self._portrait_style)
        self._re_render_current()

    def _re_render_current(self):
        cur = self._list.currentItem()
        cat = self._by_key.get(int(cur.data(Qt.UserRole))) if cur is not None else None
        self._render_tree(cat)

    def _stop_thumbnail_preload(self):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.requestInterruption()
            self._thumb_worker.wait(500)
        self._thumb_worker = None

    def _queue_thumbnail_preload(self, cats: list[Cat]):
        self._stop_thumbnail_preload()
        if not _SWF_RENDERER_AVAILABLE or not self._show_thumbnails or not cats:
            return
        self._thumb_worker = FamilyTreeThumbnailPreloadWorker(cats, _THUMB_SIZE, self)
        self._thumb_worker.start()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_cats(self, cats: list[Cat]):
        self._stop_thumbnail_preload()
        selected_key = None
        cur = self._list.currentItem()
        if cur is not None:
            selected_key = int(cur.data(Qt.UserRole))
        self._cats = sorted(cats, key=lambda c: (c.name or "").lower())
        self._by_key = {c.db_key: c for c in self._cats}
        self._refresh_filter_button_labels()
        self._refresh_list()
        if selected_key is not None and selected_key in self._by_key:
            self.select_cat(self._by_key[selected_key])
        elif self._list.rowCount():
            self._list.setCurrentCell(0, self.COL_NAME)
        else:
            self._render_tree(None)

    def select_cat(self, cat: Optional[Cat]):
        if cat is None:
            return
        for row in range(self._list.rowCount()):
            item = self._list.item(row, self.COL_NAME)
            if item is not None and int(item.data(Qt.UserRole)) == cat.db_key:
                self._list.setCurrentCell(row, self.COL_NAME)
                self._list.scrollToItem(item)
                return

    def _open_cat_from_tree(self, cat: Optional[Cat]):
        if cat is None:
            return
        if self._alive_only and cat.status == "Gone":
            self._set_alive_only(False)
        if self._search.text():
            self._search.clear()
        self.select_cat(cat)

    # ── Tree rendering ───────────────────────────────────────────────────────

    def _render_tree(self, cat: Optional[Cat]):
        self._tree_content = QWidget()
        self._tree_scroll.setWidget(self._tree_content)
        root = QVBoxLayout(self._tree_content)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(10)

        if cat is None:
            root.addWidget(QLabel(_tr("family_tree.no_match"),
                                  styleSheet="color:#666; font-size:12px;"))
            root.addStretch()
            return

        # ── HUD canvas mode (thumbnails + portrait style both on) ────────────
        if self._show_thumbnails and self._portrait_style and _SWF_RENDERER_AVAILABLE:
            title = QLabel(_tr("family_tree.title", name=cat.name))
            title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
            root.addWidget(title)
            root.addWidget(QLabel(_tr("family_tree.click_hint"),
                                  styleSheet="color:#666; font-size:11px;"))

            scroll_w = self._tree_scroll.viewport().width() or 900
            scroll_h = self._tree_scroll.viewport().height() or 700

            canvas = FamilyTreeHudCanvas(
                cat=cat,
                by_key=self._by_key,
                open_cat_cb=self._open_cat_from_tree,
                show_thumbnails=True,
                panel_w=float(scroll_w),
                panel_h=float(scroll_h),
                parent=None,
            )
            root.addWidget(canvas)
            root.addStretch()
            
            # Scroll to show the selected cat at the bottom center of view
            # The selected cat is at world position (0, 0)
            # Calculate its screen position and scroll to it
            from portrait_tree import world_to_screen
            screen_x, screen_y = world_to_screen(0.0, 0.0, canvas._hud)
            screen_x += canvas._PAD  # Account for padding
            screen_y += canvas._PAD
            
            # Scroll to center the selected cat at the bottom of the viewport
            # Position it so the cat is at the bottom with some margin
            target_scroll_y = max(0, screen_y + _CARD_H - scroll_h + 50)  # 50px from bottom
            target_scroll_x = max(0, screen_x - scroll_w // 2)  # Center horizontally
            
            # Use a single-shot timer to scroll after layout is complete
            from PySide6.QtCore import QTimer
            def do_scroll():
                self._tree_scroll.horizontalScrollBar().setValue(int(target_scroll_x))
                self._tree_scroll.verticalScrollBar().setValue(int(target_scroll_y))
            QTimer.singleShot(0, do_scroll)
            
            _enforce_min_font_in_widget_tree(self._tree_content)
            return

        # ── Legacy list-style rendering ──────────────────────────────────────
        title = QLabel(_tr("family_tree.title", name=cat.name))
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        root.addWidget(title)
        root.addWidget(QLabel(_tr("family_tree.click_hint"),
                               styleSheet="color:#666; font-size:11px;"))

        visible_tree_cats: list[Cat] = []

        def cat_box(c: Optional[Cat], highlight=False) -> QWidget:
            """Wrapper that uses the shared cat button function, with legacy gen_age info."""
            if c is None:
                return _make_cat_button(
                    cat=None,
                    on_click_cb=self._open_cat_from_tree,
                    is_selected=highlight,
                    show_thumbnail=self._show_thumbnails,
                )
            
            visible_tree_cats.append(c)
            
            # Build line2 with gen_age info for legacy view
            line2 = c.gender_display or ""
            if c.room_display:
                if line2:
                    line2 += f"  {c.room_display}"
                else:
                    line2 = c.room_display
            gen_age = self._gen_age_text(c)
            if gen_age:
                if line2:
                    line2 += f"  |  {gen_age}"
                else:
                    line2 = gen_age
            if c.status == "Gone":
                if line2:
                    line2 += f"  ({_tr('status.gone')})"
                else:
                    line2 = _tr("status.gone")
            
            # Create basic button first
            bg     = "#1d2f4a" if highlight else "#131326"
            border = "#3b5f95" if highlight else "#252545"
            
            btn = QPushButton(f"{c.name}\n{line2}" if line2 else c.name)
            icon = _make_tag_icon(_cat_tags(c), dot_size=14, spacing=4)
            if not icon.isNull():
                btn.setIcon(icon)
            btn.setStyleSheet(
                f"QPushButton {{ color:#ddd; font-size:10px; padding:7px 10px;"
                f" background:{bg}; border:1px solid {border}; border-radius:6px; }}"
                "QPushButton:hover { background:#1a2a46; }")
            btn.setMinimumWidth(120)
            
            if highlight:
                btn.setEnabled(False)
            else:
                btn.clicked.connect(lambda checked=False, target=c: self._open_cat_from_tree(target))
            
            # Add thumbnail if enabled
            if self._show_thumbnails and _SWF_RENDERER_AVAILABLE:
                try:
                    png = swf_cat_renderer.render_cat_thumbnail(c, size=_THUMB_SIZE)
                    if png:
                        pix = QPixmap()
                        pix.loadFromData(bytes(png))
                        card = QWidget()
                        card.setStyleSheet(
                            f"QWidget {{ background:{bg}; border:1px solid {border};"
                            " border-radius:6px; }")
                        cv = QVBoxLayout(card)
                        cv.setContentsMargins(4, 4, 4, 0)
                        cv.setSpacing(2)
                        img_lbl = QLabel()
                        img_lbl.setPixmap(pix.scaled(
                            _THUMB_SIZE, _THUMB_SIZE,
                            Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        img_lbl.setAlignment(Qt.AlignCenter)
                        img_lbl.setStyleSheet("border:none; background:transparent;")
                        btn.setStyleSheet(
                            f"QPushButton {{ color:#ddd; font-size:10px; padding:4px 6px;"
                            f" background:transparent; border:none; border-radius:0; }}"
                            "QPushButton:hover { color:#fff; }")
                        cv.addWidget(img_lbl)
                        cv.addWidget(btn)
                        return card
                except Exception:
                    pass
            
            return btn

        def row_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#444; font-weight:bold; letter-spacing:1px;")
            lbl.setFixedWidth(row_label_width)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lbl

        def add_generation_row(label: str, cats_row: list[Optional[Cat]],
                               highlight_self=False):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(row_label(label))
            for c in cats_row:
                row.addWidget(cat_box(c, highlight=highlight_self and c is cat))
            row.addStretch()
            root.addLayout(row)

        def add_arrow():
            a = QLabel("↓")
            a.setStyleSheet("color:#2f3f66; font-size:16px;")
            a.setAlignment(Qt.AlignCenter)
            root.addWidget(a)

        def _ancestor_row_label(level: int) -> str:
            if level == 1:
                return _tr("family_tree.level.parents")
            if level == 2:
                return _tr("family_tree.level.grandparents")
            if level == 3:
                return _tr("family_tree.level.great_grandparents")
            return _tr("family_tree.level.n_great_grandparents", count=level - 2)

        # Get ancestry and children using shared function
        root_cat, ancestor_levels, children_all = _get_lineage_cats(cat, max_ancestors=5)

        label_texts = ["SELF", "CHILDREN", "GRANDCHILDREN"] + [
            _ancestor_row_label(i) for i in range(1, len(ancestor_levels) + 1)
        ]
        label_font = QFont(self.font())
        label_font.setBold(True)
        fm = QFontMetrics(label_font)
        max_text_px = max(fm.horizontalAdvance(t) for t in label_texts)
        max_letter_spacing_px = max(max(len(t) - 1, 0) for t in label_texts)
        row_label_width = max(120, max_text_px + max_letter_spacing_px + 24)

        children = list(cat.children)
        grandchildren = [c for c in children_all if c not in children]

        for idx in range(len(ancestor_levels), 0, -1):
            level_nodes = ancestor_levels[idx - 1]
            add_generation_row(_ancestor_row_label(idx), level_nodes[:12])
            if len(level_nodes) > 12:
                root.addWidget(QLabel(
                    f"… and {len(level_nodes)-12} more in {_ancestor_row_label(idx)}",
                    styleSheet="color:#555; font-size:10px;"))
            add_arrow()
        add_generation_row("SELF", [cat], highlight_self=True)
        if children:
            add_arrow()
            add_generation_row("CHILDREN", children[:10])
            if len(children) > 10:
                root.addWidget(QLabel(f"… and {len(children)-10} more children",
                                      styleSheet="color:#555; font-size:10px;"))
        if grandchildren:
            add_arrow()
            add_generation_row("GRANDCHILDREN", grandchildren[:10])
            if len(grandchildren) > 10:
                root.addWidget(QLabel(f"… and {len(grandchildren)-10} more grandchildren",
                                      styleSheet="color:#555; font-size:10px;"))
        if not any([ancestor_levels, children, grandchildren]):
            root.addWidget(QLabel("No known lineage data for this cat yet.",
                                  styleSheet="color:#666; font-size:12px;"))

        root.addStretch()
        _enforce_min_font_in_widget_tree(self._tree_content)
        self._queue_thumbnail_preload(visible_tree_cats)

    def _gen_age_text(self, c: Optional[Cat]) -> str:
        if c is None or c.status == "Gone":
            return ""
        age = "?"
        if getattr(c, "age", None) is not None:
            age = str(c.age)
        return _tr("family_tree.gen_age", generation=c.generation, age=age)