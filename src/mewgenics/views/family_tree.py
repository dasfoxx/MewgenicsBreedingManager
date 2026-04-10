from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QFont, QFontMetrics, QPixmap

from save_parser import Cat
from mewgenics.models.cat_table_model import _SortKeyItem
from mewgenics.utils.localization import _tr
from mewgenics.utils.styling import _enforce_min_font_in_widget_tree, _sidebar_btn
from mewgenics.utils.tags import _make_tag_icon, _cat_tags
from mewgenics.utils.config import _load_app_config, _save_app_config
from CatAssets.CatAssetsLoader import ensure_defineshape_pngdata

try:
    import swf_cat_renderer
    _SWF_RENDERER_AVAILABLE = True
except Exception:
    _SWF_RENDERER_AVAILABLE = False

_FAMILY_TREE_THUMBNAILS_KEY = "family_tree_show_thumbnails"
_THUMB_SIZE = 96  # px


def _load_family_tree_show_thumbnails() -> bool:
    """Return persisted Family Tree thumbnail preference; default OFF."""
    return bool(_load_app_config().get(_FAMILY_TREE_THUMBNAILS_KEY, False))


def _save_family_tree_show_thumbnails(enabled: bool) -> None:
    data = _load_app_config()
    data[_FAMILY_TREE_THUMBNAILS_KEY] = bool(enabled)
    _save_app_config(data)


class FamilyTreeThumbnailPreloadWorker(QThread):
    """Warm thumbnail disk-cache for visible cats off the UI thread."""

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
            pass  # Thumbnails are decorative; never crash the app


class FamilyTreeBrowserView(QWidget):
    """
    Dedicated tree-browsing view:
    left side = cat list, right side = visual family tree for selected cat.
    """
    COL_NAME = 0
    COL_LOC = 1
    COL_GEN = 2
    COL_AGE = 3

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
        self._thumb_worker: Optional[FamilyTreeThumbnailPreloadWorker] = None
        
        # Pre-load DefinedShape PNG data if thumbnails are enabled
        if self._show_thumbnails and _SWF_RENDERER_AVAILABLE:
            ensure_defineshape_pngdata()

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Left pane: search + list
        left = QWidget()
        left.setFixedWidth(390)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(QLabel(_tr("family_tree.cats"), styleSheet="color:#666; font-size:10px; font-weight:bold;"))
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._all_btn = _sidebar_btn(_tr("family_tree.filter_all"))
        self._alive_btn = _sidebar_btn(_tr("family_tree.filter_alive"))
        self._all_btn.setToolTip(_tr("family_tree.filter_all.tooltip", default="Show all cats including gone"))
        self._alive_btn.setToolTip(_tr("family_tree.filter_alive.tooltip", default="Show only alive cats"))
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
        self._search.setToolTip(_tr("family_tree.search.tooltip", default="Filter cats by name"))
        lv.addWidget(self._search)
        self._list = QTableWidget(0, 4)
        self._list.setHorizontalHeaderLabels([
            "Name",
            "Location",
            "Generation",
            "Age",
        ])
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
        # Keep the name column compact by default; users can still widen it.
        hh.setSectionResizeMode(self.COL_NAME, QHeaderView.Interactive)
        self._list.setColumnWidth(self.COL_NAME, 150)
        hh.setSectionResizeMode(self.COL_LOC, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_GEN, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_AGE, QHeaderView.ResizeToContents)
        lv.addWidget(self._list, 1)
        root.addWidget(left)

        # Right pane: tree + thumbnail toggle
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self._thumb_toggle = QPushButton("Show Cat Images")
        self._thumb_toggle.setCheckable(True)
        self._thumb_toggle.setChecked(self._show_thumbnails)
        self._thumb_toggle.setEnabled(_SWF_RENDERER_AVAILABLE)
        self._thumb_toggle.setToolTip(_tr("family_tree.thumb_toggle.tooltip", default="Toggle cat image thumbnails in the tree"))
        if not _SWF_RENDERER_AVAILABLE:
            self._thumb_toggle.setToolTip(_tr("family_tree.thumb_toggle_disabled.tooltip", default="Cat image rendering requires Pillow and numpy (pip install Pillow numpy)"))
        self._thumb_toggle.setStyleSheet(
            "QPushButton { background:#131326; color:#aaa; border:1px solid #252545;"
            " border-radius:4px; padding:4px 10px; font-size:10px; }"
            "QPushButton:checked { background:#1d2f4a; color:#7fb3f5; border-color:#3b5f95; }"
            "QPushButton:hover { background:#1a2040; }"
            "QPushButton:disabled { color:#444; }"
        )
        self._thumb_toggle.clicked.connect(self._toggle_thumbnails)
        rv.addWidget(self._thumb_toggle, 0, Qt.AlignRight)
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

    def _refresh_filter_button_labels(self):
        total = len(self._cats)
        alive = sum(1 for c in self._cats if c.status != "Gone")
        self._all_btn.setText(f"{_tr('family_tree.filter_all')} ({total})")
        self._alive_btn.setText(f"{_tr('family_tree.filter_alive')} ({alive})")

    # ── Thumbnail helpers ──────────────────────────────────────────────────────

    def _toggle_thumbnails(self):
        self._show_thumbnails = self._thumb_toggle.isChecked()
        _save_family_tree_show_thumbnails(self._show_thumbnails)
        cur = self._list.currentItem()
        if cur is not None:
            cat = self._by_key.get(int(cur.data(Qt.UserRole)))
            self._render_tree(cat)
        else:
            self._render_tree(None)

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
        # If a gone cat is clicked while Alive filter is active, switch to All.
        if self._alive_only and cat.status == "Gone":
            self._set_alive_only(False)
        # Ensure search does not hide the clicked target.
        if self._search.text():
            self._search.clear()
        self.select_cat(cat)

    def _gen_age_text(self, c: Optional[Cat]) -> str:
        if c is None or c.status == "Gone":
            return ""
        age = "?"
        if getattr(c, "age", None) is not None:
            age = str(c.age)
        return _tr("family_tree.gen_age", generation=c.generation, age=age)

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
                location_text = _tr("status.gone") if cat.status == "Gone" else _tr("status.adventure")
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

    def _on_current_item_changed(self, current_row, current_column, previous_row, previous_column):
        if current_row < 0:
            self._render_tree(None)
            return
        current = self._list.item(current_row, self.COL_NAME)
        if current is None:
            self._render_tree(None)
            return
        cat = self._by_key.get(int(current.data(Qt.UserRole)))
        self._render_tree(cat)

    def _render_tree(self, cat: Optional[Cat]):
        self._tree_content = QWidget()
        self._tree_scroll.setWidget(self._tree_content)

        root = QVBoxLayout(self._tree_content)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(10)

        if cat is None:
            root.addWidget(QLabel(_tr("family_tree.no_match"), styleSheet="color:#666; font-size:12px;"))
            root.addStretch()
            return

        title = QLabel(_tr("family_tree.title", name=cat.name))
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        root.addWidget(title)
        root.addWidget(QLabel(_tr("family_tree.click_hint"), styleSheet="color:#666; font-size:11px;"))

        visible_tree_cats: list[Cat] = []

        def cat_box(c: Optional[Cat], highlight=False) -> QWidget:
            if c is None:
                btn = QPushButton(_tr("family_tree.unknown"))
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { color:#303040; font-size:10px; padding:7px 10px;"
                    " background:#0e0e1c; border:1px solid #18182a; border-radius:6px; }")
                return btn
            visible_tree_cats.append(c)
            line2 = c.gender_display
            if c.room_display:
                line2 += f"  {c.room_display}"
            gen_age = self._gen_age_text(c)
            if gen_age:
                line2 += f"  |  {gen_age}"
            if c.status == "Gone":
                line2 += f"  ({_tr('status.gone')})"
            bg = "#1d2f4a" if highlight else "#131326"
            border = "#3b5f95" if highlight else "#252545"
            btn = QPushButton(f"{c.name}\n{line2}")
            icon = _make_tag_icon(_cat_tags(c), dot_size=14, spacing=4)
            if not icon.isNull():
                btn.setIcon(icon)
            btn.setStyleSheet(
                f"QPushButton {{ color:#ddd; font-size:10px; padding:7px 10px;"
                f" background:{bg}; border:1px solid {border}; border-radius:6px; }}"
                "QPushButton:hover { background:#1a2a46; }")
            if c is not cat:
                btn.clicked.connect(lambda checked=False, target=c: self._open_cat_from_tree(target))
            else:
                btn.setEnabled(False)
            btn.setMinimumWidth(120)

            # Optionally wrap in a card with thumbnail image above the button
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
                            Qt.KeepAspectRatio, Qt.SmoothTransformation,
                        ))
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
                    pass  # Fall through to plain button

            return btn

        def row_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#444; font-weight:bold; letter-spacing:1px;")
            lbl.setFixedWidth(row_label_width)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lbl

        def add_generation_row(label: str, cats_row: list[Optional[Cat]], highlight_self=False):
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

        def _dedupe_keep_order(items: list[Cat]) -> list[Cat]:
            seen = set()
            out: list[Cat] = []
            for item in items:
                sid = id(item)
                if sid in seen:
                    continue
                seen.add(sid)
                out.append(item)
            return out

        def _ancestor_row_label(level: int) -> str:
            if level == 1:
                return _tr("family_tree.level.parents")
            if level == 2:
                return _tr("family_tree.level.grandparents")
            if level == 3:
                return _tr("family_tree.level.great_grandparents")
            return _tr("family_tree.level.n_great_grandparents", count=level - 2)

        # Build all known ancestor levels (1=parents, 2=grandparents, ...).
        ancestor_levels: list[list[Cat]] = []
        frontier: list[Cat] = [cat]
        for _ in range(8):
            nxt: list[Cat] = []
            for node in frontier:
                if node.parent_a is not None:
                    nxt.append(node.parent_a)
                if node.parent_b is not None:
                    nxt.append(node.parent_b)
            nxt = _dedupe_keep_order(nxt)
            if not nxt:
                break
            ancestor_levels.append(nxt)
            frontier = nxt

        # Dynamic row-label gutter width: based on the longest visible label and
        # current font metrics, so it tracks zoom/font-size changes.
        label_texts = ["SELF", "CHILDREN", "GRANDCHILDREN"] + [
            _ancestor_row_label(i) for i in range(1, len(ancestor_levels) + 1)
        ]
        label_font = QFont(self.font())
        label_font.setBold(True)
        fm = QFontMetrics(label_font)
        max_text_px = max(fm.horizontalAdvance(t) for t in label_texts)
        # Row labels use letter-spacing:1px in stylesheet; account for that so
        # long prefixes like "10x " are fully measured.
        max_letter_spacing_px = max(max(len(t) - 1, 0) for t in label_texts)
        row_label_width = max(120, max_text_px + max_letter_spacing_px + 24)

        children = list(cat.children)
        grandchildren: list[Cat] = []
        for child in children:
            grandchildren.extend(child.children)
        grandchildren = list({id(c): c for c in grandchildren}.values())

        # Render oldest ancestors at top, then down to self.
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
                root.addWidget(QLabel(f"… and {len(children)-10} more children", styleSheet="color:#555; font-size:10px;"))
        if grandchildren:
            add_arrow()
            add_generation_row("GRANDCHILDREN", grandchildren[:10])
            if len(grandchildren) > 10:
                root.addWidget(QLabel(f"… and {len(grandchildren)-10} more grandchildren", styleSheet="color:#555; font-size:10px;"))
        if not any([ancestor_levels, children, grandchildren]):
            root.addWidget(QLabel("No known lineage data for this cat yet.", styleSheet="color:#666; font-size:12px;"))

        root.addStretch()
        _enforce_min_font_in_widget_tree(self._tree_content)
        self._queue_thumbnail_preload(visible_tree_cats)
