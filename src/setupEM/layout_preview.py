########################################################################
#
# Copyright 2025-2026 Volker Muehlhaus and IHP PDK Authors
#
# Licensed under the GNU General Public License, Version 3.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.gnu.org/licenses/gpl-3.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################

"""
layout_preview.py

Layout Preview window (Tools > Layout Preview...): draws the 2D GDSII shapes
gds2palace's own reader would process for the current Input Files tab
settings (GDS file, cell name, datatype/purpose filter), in z (stackup)
order, colored per the stackup XML's material colors. "Marker" shapes - from
MainWindow.get_layout_preview_markers(): EM ports for setupEM, thermal
sources/constant-temperature boundaries for setupThermal - are drawn on top,
highlighted, labeled, and grouped separately (own legend section per kind).
"""

import os, io, contextlib
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel,
    QScrollArea, QCheckBox, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsItem, QGraphicsPolygonItem, QGraphicsSimpleTextItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QSlider, QSplitter,
    )
from PySide6.QtGui import QColor, QBrush, QPen, QPolygonF, QPainter, QFont, QPainterPath, QTransform
from PySide6.QtCore import Qt, QPointF, Signal

from gds2palace import gds_reader

DEFAULT_LAYER_COLOR = "#a0a0a0"   # stackup_material.color has no default (None) if XML omits Color=
PORT_OUTLINE_COLOR = "#ff33ff"
PORT_FILL_COLOR = QColor(255, 51, 255, 100)
SOURCE_OUTLINE_COLOR = "#ff8800"      # orange: thermal heat source
SOURCE_FILL_COLOR = QColor(255, 136, 0, 100)
BOUNDARY_OUTLINE_COLOR = "#33ccff"    # cyan: thermal constant-temperature boundary
BOUNDARY_FILL_COLOR = QColor(51, 204, 255, 100)
# a z-directed via port's (or a thermal source/boundary's) GDS marker polygon
# is often a zero-width sliver in one axis (a line, not a rectangle) - draw its
# outline with this fixed device-pixel width (a cosmetic pen, so it does not
# grow/shrink with canvas zoom) so it stays visible instead of vanishing,
# without faking the underlying geometry
MARKER_OUTLINE_WIDTH = 3
# initial layer-opacity slider position
DEFAULT_LAYER_OPACITY_PERCENT = 70

# cross-window highlight (see set_highlighted_layer()): a layer selected in the
# Stackup Preview/Editor gets this outline + hatch fill, above every regular
# layer but *below* every port/source/boundary marker (those must stay
# visible on top no matter what) - its actual z-value is computed per refresh()
# in self._highlight_zvalue, since it depends on how many layers are drawn.
# White (not red) since it's the one color none of the layer/marker palettes
# above use, so it reads clearly against any of them and the dark background.
HIGHLIGHT_COLOR = "white"
HIGHLIGHT_WIDTH = 3
HIGHLIGHT_ZVALUE = 100000

# legend section order for "marker" groups (see get_layout_preview_markers());
# a group only appears if the current app/data actually has items for it
MARKER_GROUP_ORDER = ["Ports", "Sources", "Boundaries"]
# per-marker-kind (fill, outline) colors, keyed by the "kind" tag each marker
# dict carries (see MainWindowBase.get_layout_preview_markers() docstring)
MARKER_STYLES = {
    "port": (PORT_FILL_COLOR, PORT_OUTLINE_COLOR),
    "source": (SOURCE_FILL_COLOR, SOURCE_OUTLINE_COLOR),
    "boundary": (BOUNDARY_FILL_COLOR, BOUNDARY_OUTLINE_COLOR),
}


def _format_value(value, unit):
    return f"{float(value):g} {unit}"

# in-plane port directions -> (dx, dy) unit vector in *scene* coordinates (y
# already flipped vs. GDS, matching _polygon_points()'s float(-y) convention -
# GDS +Y is "up", which is -y in scene/screen space). Z/-Z (via ports) have no
# entry here - a through-plane direction has no meaningful in-plane arrow.
_DIRECTION_VECTORS = {
    "X": (1, 0), "-X": (-1, 0),
    "Y": (0, -1), "-Y": (0, 1),
}


def _direction_vector(direction):
    return _DIRECTION_VECTORS.get(str(direction).strip().upper())


def _signed_direction(direction):
    """Compact direction with an explicit sign, e.g. "Z" -> "+Z" (a stored
    direction always has a sign for the negative case already, but not for
    the implied-positive case) - kept short for the legend list.
    """
    direction = str(direction).strip().upper()
    if direction.startswith("-") or direction.startswith("+"):
        return direction
    return "+" + direction if direction else direction


def _arrow_path(dx, dy, length=27, head_size=9):
    """An open arrow shape (shaft + V head) centered on (0, 0) and pointing
    towards (dx, dy), in local item coordinates - paired with
    ItemIgnoresTransformations, this keeps the arrow a fixed pixel size
    regardless of canvas zoom, same as the port label.
    """
    half = length / 2
    tail_x, tail_y = -dx * half, -dy * half
    tip_x, tip_y = dx * half, dy * half
    back_x, back_y = tip_x - dx * head_size, tip_y - dy * head_size
    perp_x, perp_y = -dy * head_size * 0.5, dx * head_size * 0.5

    path = QPainterPath()
    path.moveTo(tail_x, tail_y)
    path.lineTo(tip_x, tip_y)
    path.lineTo(back_x + perp_x, back_y + perp_y)
    path.moveTo(tip_x, tip_y)
    path.lineTo(back_x - perp_x, back_y - perp_y)
    return path


VIA_POSITIVE_COLOR = PORT_OUTLINE_COLOR  # pink/magenta: +Z, current out of the page
VIA_NEGATIVE_COLOR = "#3399ff"           # blue: -Z, current into the page


def _via_marker_items(negative):
    """Graphics item for a Z/-Z via port's marker: a filled circle at the port
    location - pink for current out of the page (+Z), blue for into the page
    (-Z). Positioned/parented by the caller, same as the in-plane marker.
    """
    color = QColor(VIA_NEGATIVE_COLOR if negative else VIA_POSITIVE_COLOR)
    circle = QGraphicsEllipseItem(-9, -9, 18, 18)
    circle.setBrush(QBrush(color))
    circle.setPen(Qt.NoPen)
    return [circle]


def _plain_marker_items(color):
    """Graphics item for a thermal source/boundary's marker: a plain filled
    circle at the location, no direction/polarity indicator - thermal objects
    have no orientation to show, unlike EM ports.
    """
    circle = QGraphicsEllipseItem(-9, -9, 18, 18)
    circle.setBrush(QBrush(QColor(color)))
    circle.setPen(Qt.NoPen)
    return [circle]


def _material_qcolor(material):
    color = getattr(material, "color", None) if material is not None else None
    if not color:
        return QColor(DEFAULT_LAYER_COLOR)
    return QColor(color if color.startswith("#") else "#" + color)


class _VisibilityGroup:
    """A plain (non-scene) grouping of graphics items that a single legend
    checkbox shows/hides together. Deliberately not a QGraphicsItemGroup: its
    marker/label items use ItemIgnoresTransformations (see refresh() below),
    and Qt documents that flag as unreliable on an item whose parent doesn't
    also carry it - so members stay direct top-level scene items instead, and
    this class just applies setVisible()/setZValue() to each individually.
    """

    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def setVisible(self, visible):
        for item in self.items:
            item.setVisible(visible)

    def setZValue(self, z):
        for item in self.items:
            item.setZValue(z)


class _ClickableLegendRow(QWidget):
    """A legend row for a drawn layer (not a marker group) that can be clicked
    to select/deselect that layer - anywhere except the visibility checkbox,
    which keeps its own click for show/hide. The swatch/label children are
    WA_TransparentForMouseEvents so a click on them still reaches this widget.
    """

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class LayoutCanvas(QGraphicsView):
    """Pan/zoom GDS canvas - plain PySide6 QGraphicsView, no new dependency.
    Polygon/label points are stored with y already negated (see
    LayoutPreviewWindow.refresh), so GDS "up" (+y) renders near the top of
    the view without a separate view-level flip transform.
    """

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#303030"))

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)


class LayoutPreviewWindow(QDialog):
    """Own top-level window (WA_DeleteOnClose, non-modal) - same lifecycle as
    StackupEditorWindow/ResultViewerWindow, so the user can keep it open while
    tweaking the Input Files/Ports tabs and click Refresh.
    """

    def __init__(self, MainWindow):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Layout Preview")
        self.resize(1100, 750)
        self.MainWindow = MainWindow

        self.canvas = LayoutCanvas()

        self.legend_layout = QVBoxLayout()
        self.legend_layout.setAlignment(Qt.AlignTop)
        legend_widget = QWidget()
        legend_widget.setLayout(self.legend_layout)
        legend_scroll = QScrollArea()
        legend_scroll.setWidgetResizable(True)
        legend_scroll.setWidget(legend_widget)

        legend_all_btn = QPushButton("All")
        legend_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        legend_none_btn = QPushButton("None")
        legend_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        legend_buttons_layout = QHBoxLayout()
        legend_buttons_layout.addWidget(legend_all_btn)
        legend_buttons_layout.addWidget(legend_none_btn)

        # layer opacity (not applied to port highlights, which stay fully
        # opaque so they keep standing out) - lets overlapping layers below
        # show through instead of being fully covered by the one on top
        self._layer_items = []
        # cross-window highlight: which layer name (if any) to outline in red,
        # set via set_highlighted_layer() by MainWindow when a layer is
        # selected in the Stackup Preview/Editor - persists across refresh()
        self._layer_items_by_name = {}
        self._highlighted_layer_name = None
        self._highlight_items = []
        self._highlight_zvalue = 0  # recomputed each refresh() from the layer count
        # legend rows for drawn layers (name -> _ClickableLegendRow), so a click
        # can select/deselect a layer directly from Layout Preview's own legend,
        # independent of the Stackup Preview/Editor cross-window highlight below
        self._legend_layer_rows = {}
        self._info_base_text = ""  # set by refresh(); _update_info_label() appends selection info
        self.opacity_label = QLabel()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(DEFAULT_LAYER_OPACITY_PERCENT)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._on_opacity_changed(self.opacity_slider.value())

        legend_panel = QWidget()
        legend_panel.setMinimumWidth(120)
        legend_panel_layout = QVBoxLayout(legend_panel)
        legend_panel_layout.setContentsMargins(0, 0, 0, 0)
        legend_panel_layout.addLayout(legend_buttons_layout)
        legend_panel_layout.addWidget(self.opacity_label)
        legend_panel_layout.addWidget(self.opacity_slider)
        legend_panel_layout.addWidget(legend_scroll, 1)

        # QSplitter (not a plain layout) so the user can drag the divider to
        # resize the legend panel width - it was getting too tight for longer
        # layer/port names once direction/inactive tags were added to them
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.addWidget(self.canvas)
        content_splitter.addWidget(legend_panel)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setSizes([900, 200])
        # a drag can otherwise shrink a pane past its minimumWidth down to 0,
        # collapsing it entirely - keep the legend panel from disappearing
        content_splitter.setCollapsible(1, False)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.info_label, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        button_layout.addWidget(refresh_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        main_layout = QVBoxLayout()
        main_layout.addWidget(content_splitter, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

        self.refresh()

    def _clear_legend(self):
        self._checkboxes = []
        self._layer_items = []
        self._layer_items_by_name = {}
        self._legend_layer_rows = {}
        # the highlight items themselves were just destroyed by scene.clear()
        # in refresh() (called right before this) - drop the stale references,
        # but keep _highlighted_layer_name itself so it survives a refresh
        self._highlight_items = []
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_all_checked(self, checked):
        for checkbox in self._checkboxes:
            checkbox.setChecked(checked)

    def _on_opacity_changed(self, value):
        self.opacity_label.setText(f"Layer opacity: {value}%")
        for item in self._layer_items:
            item.setOpacity(value / 100.0)

    def set_highlighted_layer(self, name):
        """Outline (+ hatch-fill) every drawn shape on layer `name` in white,
        above every regular layer but below every port/source/boundary marker
        - called by MainWindow when a layer is selected in the Stackup
        Preview/Editor (None/an unknown name clears it), and also by clicking
        a layer's own legend row (see _on_legend_layer_clicked()) - either way
        this is the single source of truth for "what's highlighted right now".
        Persists across refresh() via self._highlighted_layer_name. Also
        updates info_label with the selected layer name and its polygon count.
        """
        self._highlighted_layer_name = name
        scene = self.canvas.scene()
        for item in self._highlight_items:
            scene.removeItem(item)
        self._highlight_items = []
        for polygon_item in self._layer_items_by_name.get(name, []):
            outline = QGraphicsPolygonItem(polygon_item.polygon())
            # diagonal hatch fill (built into Qt, no new dependency) in
            # addition to the outline - draws the eye even on small/thin
            # regions where a border alone is easy to miss
            outline.setBrush(QBrush(QColor(HIGHLIGHT_COLOR), Qt.BDiagPattern))
            pen = QPen(QColor(HIGHLIGHT_COLOR), HIGHLIGHT_WIDTH)
            pen.setCosmetic(True)  # stays a thin fixed-pixel line at any zoom
            outline.setPen(pen)
            outline.setZValue(self._highlight_zvalue)
            scene.addItem(outline)
            self._highlight_items.append(outline)
        self._update_info_label()
        self._update_legend_selection_styling()

    def _update_legend_selection_styling(self):
        # visually mark whichever legend row (if any) matches the current
        # highlight, so clicking a row to select/deselect it has obvious
        # feedback beyond the canvas outline itself
        for name, row in self._legend_layer_rows.items():
            row.setStyleSheet("background-color: palette(highlight);" if name == self._highlighted_layer_name else "")

    def _on_legend_layer_clicked(self, name):
        # click the already-selected layer's row again to deselect it, same
        # convention as the canvas/Stackup Preview highlight; this only ever
        # changes Layout Preview's own highlight, it does not reach back into
        # an open Stackup Preview/Editor's selection (one-way: stackup->layout,
        # not layout->stackup)
        if self._highlighted_layer_name == name:
            self.set_highlighted_layer(None)
        else:
            self.set_highlighted_layer(name)

    def _update_info_label(self):
        text = self._info_base_text
        if self._highlighted_layer_name:
            count = len(self._highlight_items)
            text += (f"   Selected: {self._highlighted_layer_name} "
                     f"({count} polygon{'s' if count != 1 else ''})")
        self.info_label.setText(text)

    def _section_label(self, text):
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _add_legend_row(self, color_name, text, group, layer_name=None):
        # layer_name is only set for a drawn-layer row (not a marker group row,
        # e.g. Ports/Sources/Boundaries) - those aren't selectable, only shown
        selectable = layer_name is not None
        row = _ClickableLegendRow() if selectable else QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 2, 2, 2)

        checkbox = QCheckBox()
        checkbox.setChecked(True)
        checkbox.toggled.connect(lambda checked, g=group: g.setVisible(checked))
        self._checkboxes.append(checkbox)
        row_layout.addWidget(checkbox)

        swatch = QLabel()
        swatch.setFixedSize(14, 14)
        swatch.setStyleSheet(f"background-color: {color_name}; border: 1px solid #000000;")
        row_layout.addWidget(swatch)

        label = QLabel(text)
        row_layout.addWidget(label, 1)

        if selectable:
            # let clicks on the swatch/label reach the row itself (the
            # checkbox is left alone so it still toggles visibility)
            swatch.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip("Click to select/deselect this layer")
            row.clicked.connect(lambda name=layer_name: self._on_legend_layer_clicked(name))
            self._legend_layer_rows[layer_name] = row

        self.legend_layout.addWidget(row)

    def _polygon_points(self, poly):
        return QPolygonF([QPointF(float(x), float(-y)) for x, y in zip(poly.pts_x, poly.pts_y)])

    def refresh(self):
        # check the live field text, not just MainWindow.metals_list below - that
        # can be stale (holding a *previous* successful load) if the XML field was
        # since changed to a path that doesn't exist, since read_XML() silently
        # no-ops on a missing file rather than clearing the old stackup data
        gdsfile = self.MainWindow.file_tab.gds_file_edit.text()
        xmlfile = self.MainWindow.file_tab.XML_file_edit.text()
        if not os.path.isfile(gdsfile) or not os.path.isfile(xmlfile):
            QMessageBox.warning(self, "Error", "Load a GDSII file and XML stackup first")
            return

        if not self.MainWindow.file_tab.save_values():
            return  # save_values() already showed its own warning

        metals_list = self.MainWindow.metals_list
        dielectrics_list = self.MainWindow.dielectrics_list
        materials_list = self.MainWindow.materials_list
        if metals_list is None or dielectrics_list is None or materials_list is None:
            # both files exist but read_XML() couldn't parse the stackup - it
            # already showed a detailed QMessageBox.critical with the parse error
            return

        marker_dicts = self.MainWindow.get_layout_preview_markers()
        marker_by_layernum = {int(m["source_layernum"]): m for m in marker_dicts}

        saved_values = self.MainWindow.saved_values
        layernumbers = metals_list.getlayernumbers()
        layernumbers.extend(marker_by_layernum.keys())

        captured_stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured_stdout):
                allpolygons = gds_reader.read_gds(
                    saved_values["GdsFile"], layernumbers,
                    cellname=saved_values["cellname"],
                    purposelist=saved_values["purpose"],
                    metals_list=metals_list,
                    preprocess=saved_values["preprocess_gds"],
                    merge_polygon_size=saved_values["merge_polygon_size"],
                    gds_boundary_layers=dielectrics_list.get_boundary_layers(),
                    mirror=False, offset_x=0, offset_y=0, layernumber_offset=0)
        except (Exception, SystemExit) as e:
            details = captured_stdout.getvalue().strip() or str(e)
            QMessageBox.critical(self, "Error", f"Could not read GDSII layout:\n\n{details}")
            return

        polygons_by_layer = {}
        for poly in allpolygons.polygons:
            polygons_by_layer.setdefault(poly.layernum, []).append(poly)

        scene = self.canvas.scene()
        scene.clear()
        self._clear_legend()

        # regular stackup layers, bottom (lowest zmin) to top - so later (higher)
        # layers are drawn last and are not hidden by lower ones underneath
        regular_layers = []
        for layernum, polys in polygons_by_layer.items():
            if layernum in marker_by_layernum:
                continue
            metal = metals_list.getbylayernumber(layernum)
            regular_layers.append((metal, layernum, polys))
        regular_layers.sort(key=lambda entry: entry[0].zmin if entry[0] is not None else 0.0)
        # sits strictly above every regular layer's zValue (0..len-1) and
        # strictly below the marker tier (len+1 and up, see marker_zvalue
        # below) - the highlight must never cover a port/source/boundary
        self._highlight_zvalue = len(regular_layers)

        self.legend_layout.addWidget(self._section_label("Layers"))
        layer_legend_rows = []
        for zindex, (metal, layernum, polys) in enumerate(regular_layers):
            if metal is not None:
                material = materials_list.get_by_name(metal.material)
                color = _material_qcolor(material)
                name = metal.name
            else:
                color = QColor(DEFAULT_LAYER_COLOR)
                name = f"Layer {layernum}"

            group = _VisibilityGroup()

            tooltip = f"{name} [{layernum}]"
            for poly in polys:
                item = QGraphicsPolygonItem(self._polygon_points(poly))
                item.setBrush(QBrush(color))
                item.setPen(Qt.NoPen)
                item.setToolTip(tooltip)
                item.setAcceptHoverEvents(True)
                item.setZValue(zindex)
                item.setOpacity(self.opacity_slider.value() / 100.0)
                scene.addItem(item)
                group.add(item)
                self._layer_items.append(item)
                self._layer_items_by_name.setdefault(name, []).append(item)

            layer_legend_rows.append((color.name(), tooltip, group, name))

        # legend lists layers top-to-bottom (largest z first) - the reverse of
        # the ascending zmin order used just above for the actual draw/z-stack
        # order, which must stay bottom-to-top for correct on-canvas layering
        for color_name, tooltip, group, name in reversed(layer_legend_rows):
            self._add_legend_row(color_name, tooltip, group, layer_name=name)
        self._update_legend_selection_styling()

        # marker shapes (EM ports / thermal sources / thermal boundaries),
        # always drawn on top of every regular layer, highlighted and always
        # labeled (not just on hover), grouped into their own legend sections
        present_groups = [name for name in MARKER_GROUP_ORDER
                           if any(m["group"] == name for m in marker_by_layernum.values())]
        marker_zvalue = len(regular_layers) + 1
        for group_name in present_groups:
            self.legend_layout.addWidget(self._section_label(group_name))
            for layernum, polys in polygons_by_layer.items():
                marker = marker_by_layernum.get(layernum)
                if marker is None or marker["group"] != group_name:
                    continue

                kind = marker["kind"]
                fill_color, outline_color = MARKER_STYLES[kind]
                group = _VisibilityGroup()

                if kind == "port":
                    portnumber = marker["portnumber"]
                    label_text = f"P{portnumber}"
                    tooltip = f"Port {portnumber} [{layernum}] {_signed_direction(marker.get('direction', ''))}"
                    if float(marker.get("voltage", 1)) == 0:
                        tooltip += " (inactive)"
                elif kind == "source":
                    label_text = _format_value(marker["power"], "W")
                    tooltip = f"Source [{layernum}] {label_text}"
                else:  # "boundary"
                    label_text = _format_value(marker["temp"], "K")
                    tooltip = f"Boundary [{layernum}] {label_text}"

                for poly in polys:
                    item = QGraphicsPolygonItem(self._polygon_points(poly))
                    item.setBrush(QBrush(fill_color))
                    # cosmetic pen: stroke stays MARKER_OUTLINE_WIDTH device
                    # pixels regardless of canvas zoom, instead of scaling
                    # with it - what keeps a near-zero-width marker visible
                    marker_pen = QPen(QColor(outline_color), MARKER_OUTLINE_WIDTH)
                    marker_pen.setCosmetic(True)
                    item.setPen(marker_pen)
                    item.setToolTip(tooltip)
                    item.setAcceptHoverEvents(True)
                    item.setZValue(marker_zvalue)
                    scene.addItem(item)
                    group.add(item)

                    center_x = (poly.xmin + poly.xmax) / 2
                    center_y = -(poly.ymin + poly.ymax) / 2

                    # a real marker polygon is often a thin sliver that all but
                    # disappears at normal zoom, so also mark its centroid with
                    # a fixed-pixel-size symbol - stays visible at any zoom
                    # level, same trick as the label below. In-plane ports
                    # (X/Y/-X/-Y) get a direction arrow; Z/-Z via ports get a
                    # polarity marker; thermal sources/boundaries have no
                    # direction to show, so just a plain marker dot.
                    if kind == "port":
                        direction_text = marker.get("direction", "")
                        vector = _direction_vector(direction_text)
                        if vector is not None:
                            arrow = QGraphicsPathItem(_arrow_path(*vector))
                            arrow.setPen(QPen(QColor(outline_color), 3))
                            marker_items = [arrow]
                        else:
                            negative = str(direction_text).strip().upper() == "-Z"
                            marker_items = _via_marker_items(negative)
                    else:
                        marker_items = _plain_marker_items(outline_color)

                    for marker_item in marker_items:
                        marker_item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                        marker_item.setPos(center_x, center_y)
                        marker_item.setZValue(marker_zvalue + 1)
                        marker_item.setToolTip(tooltip)
                        scene.addItem(marker_item)
                        group.add(marker_item)

                    text = QGraphicsSimpleTextItem(label_text)
                    font = QFont()
                    font.setBold(True)
                    text.setFont(font)
                    text.setBrush(QBrush(QColor("white")))
                    text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                    # setPos() below places the *item's own origin* (top-left of
                    # its bounding rect, not its center) at the marker location -
                    # use a local setTransform() to center the glyph on that
                    # origin first; it combines independently of the setPos()
                    # that follows
                    text_rect = text.boundingRect()
                    text.setTransform(QTransform.fromTranslate(-text_rect.width() / 2, -text_rect.height() / 2))
                    text.setPos(center_x, center_y)
                    # a *uniform* z-value across every marker (not just "above
                    # this marker's own symbol") - two nearby markers' items
                    # interleave by scene insertion order at equal z, so
                    # without this a later-drawn marker could cover an
                    # earlier one's label
                    text.setZValue(marker_zvalue + 2)
                    scene.addItem(text)
                    group.add(text)

                self._add_legend_row(outline_color, tooltip, group)

        self._info_base_text = (
            f"GDS: {os.path.basename(saved_values['GdsFile'])}   "
            f"Cell: {saved_values['cellname'] or '(top cell)'}   "
            f"Purpose: {saved_values['purpose']}")

        # re-apply any active cross-window highlight - the polygon items it
        # outlines were just rebuilt from scratch above; this also refreshes
        # info_label (base text + selection, if any) via _update_info_label()
        self.set_highlighted_layer(self._highlighted_layer_name)

        rect = scene.itemsBoundingRect()
        if not rect.isEmpty():
            scene.setSceneRect(rect)
            self.canvas.fitInView(rect, Qt.KeepAspectRatio)
