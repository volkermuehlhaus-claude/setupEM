"""KLayout tab (draft): the EM export steps in a running KLayout, driven over the klayout_remote link.

The user keeps the layout open in KLayout and draws, selects and clicks there; this tab shows what the EM panel of
the klayout_em_export plug-in showed (stackup, region, ground, devices, ports) and runs its steps. Export and Load
writes the EM layout, .simcfg and port map and loads the .simcfg here, as File > Load Config would.

Needs:
- in KLayout: the klayout_remote package (Tools > Remote Link). A KLayout started from this tab has the link on;
  in a KLayout the user started, the user switches it on in that menu.
- here: the klayout_em_export package (pip). Its code is sent into KLayout over the link (load_module), so the
  plug-in itself need not be installed in KLayout.
"""
import html
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

if __package__ in (None, ""):
    import klayout_remote_client as krc
    from setup_common import EDIT_STYLE_OPTIONAL, get_preference, set_preference
else:
    from . import klayout_remote_client as krc
    from .setup_common import EDIT_STYLE_OPTIONAL, get_preference, set_preference

PACKAGE = "klayout_em_export"
REMOTE = PACKAGE + ".remote"
PREF_PROGRAM = "klayout_program"
PREF_TECHNOLOGY = "klayout_technology"
PREF_PDK = "circuit_pdk"
START_TIMEOUT = 120                 # s for a KLayout started here to switch the link on
MARKS = {"ok": ("✓", "#1a8f1a"), "warn": ("⚠", "#c87800"), "error": ("✗", "#c00000")}
MENU_HINT = "In KLayout: Tools > Remote Link > Enable Remote Link (needs the klayout_remote package)."


def em_package_folder():
    """Folder of the klayout_em_export package to send into KLayout: KLAYOUT_EM_EXPORT (a folder), else the
    installed package, else None."""
    folder = os.environ.get("KLAYOUT_EM_EXPORT")
    if folder and os.path.isdir(folder):
        return folder
    spec = importlib.util.find_spec(PACKAGE)
    if spec is not None and spec.submodule_search_locations:
        return list(spec.submodule_search_locations)[0]
    return None


def find_klayout():
    """The KLayout program: the one chosen before, else on the PATH, else in the usual install folders, or ""."""
    chosen = get_preference("setupEM", PREF_PROGRAM, "")
    if chosen and os.path.isfile(chosen):
        return chosen
    for name in ("klayout_app", "klayout"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for base in (os.environ.get("APPDATA", ""), os.environ.get("ProgramFiles", ""), "C:\\KLayout", "D:\\KLayout"):
            for candidate in (os.path.join(base, "KLayout", "klayout_app.exe"), os.path.join(base, "klayout_app.exe")):
                if base and os.path.isfile(candidate):
                    return candidate
    return ""


def marked(state, text):
    """Rich text: a coloured status mark and the text."""
    mark, color = MARKS.get(state, MARKS["error"])
    return f'<span style="color:{color}; font-weight:bold">{mark}</span>&nbsp; {html.escape(text)}'


class PortSettingsDialog(QDialog):
    """Port settings for one or more ports, as the plug-in's Port Settings dialog: direction, layers, Z0, excitation.
    choices: remote.port_choices()."""

    def __init__(self, parent, ports, choices):
        super().__init__(parent)
        self.choices = choices
        self.setWindowTitle("Port Settings")
        form = QFormLayout(self)
        form.addRow(QLabel(f"Port(s) {', '.join(map(str, ports))}: {choices['shapes']} shape(s)"))
        form.addRow("Drawn at the port", QLabel(", ".join(choices["available"]) or "no stackup metal"))
        self.show_all = QCheckBox("offer all stackup metals")
        self.show_all.toggled.connect(lambda checked: self._fill())
        form.addRow("", self.show_all)
        self.direction = QComboBox()
        self.direction.addItems(choices["directions"])
        self.direction.setCurrentText(choices["direction"])
        self.direction.currentIndexChanged.connect(lambda index: self._update())
        self.from_layer, self.to_layer, self.target = QComboBox(), QComboBox(), QComboBox()
        self.z0 = QLineEdit(f"{choices['z0']:g}")
        self.excited = QCheckBox("excited (voltage 1)")
        self.excited.setChecked(choices["excited"])
        form.addRow("Direction", self.direction)
        form.addRow("From layer (via port)", self.from_layer)
        form.addRow("To layer (via port)", self.to_layer)
        form.addRow("Target layer (in-plane port)", self.target)
        form.addRow("Z0 (Ohm)", self.z0)
        form.addRow("", self.excited)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._fill(initial=True)
        self._update()

    def _fill(self, initial=False):
        c = self.choices
        names = c["metals"] if self.show_all.isChecked() or len(c["available"]) < 2 else c["available"]
        stored_from, stored_to = c["from"], c["to"]
        if stored_from == stored_to or stored_from not in names or stored_to not in names:
            stored_from = stored_to = ""
        for box, stored, default in ((self.from_layer, stored_from, c["defaults"]["from"]),
                                     (self.to_layer, stored_to, c["defaults"]["to"]),
                                     (self.target, c["target"], c["defaults"]["target"])):
            chosen = "" if initial else box.currentText()
            wanted = next((n for n in (chosen, stored, default) if n and n in names), names[0] if names else "")
            box.clear()
            box.addItems(names)
            box.setCurrentText(wanted)

    def _update(self):
        via = "Z" in self.direction.currentText()
        self.from_layer.setEnabled(via)
        self.to_layer.setEnabled(via)
        self.target.setEnabled(not via)

    def values(self):
        try:
            z0 = float(self.z0.text())
        except ValueError:
            z0 = 50.0
        return {"direction": self.direction.currentText(), "from_layer": self.from_layer.currentText(),
                "to_layer": self.to_layer.currentText(), "target": self.target.currentText(), "z0": z0,
                "excited": self.excited.isChecked()}


class KLayoutTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()
        self.MainWindow = MainWindow
        self.link = None
        self.process = None             # a KLayout started here
        self.state = {}
        self.port_rows = []             # ("port", row) / ("proposed", row) / ("cut", row) per table row
        self.device_rows = []
        self._starting = None           # (process, deadline) while a started KLayout comes up
        self.last_export = None         # remote.export() result of the last Export and Load

        # in a scroll area: the tab is taller than setupEM's window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignTop)
        layout.addWidget(self._link_group())
        layout.addWidget(self._setup_group())
        layout.addWidget(self._devices_group())
        layout.addWidget(self._ports_group())
        layout.addWidget(self._export_group())
        layout.addWidget(self._circuit_group())
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(200)
        self.poll_timer.timeout.connect(self._poll)
        self.refresh_timer = QTimer(self)        # events come in bursts: refresh once they stop
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(300)
        self.refresh_timer.timeout.connect(lambda: self.refresh())
        self.start_timer = QTimer(self)
        self.start_timer.setInterval(500)
        self.start_timer.timeout.connect(self._try_started)
        self._show_disconnected("not connected")

    # ---------- layout of the tab ----------

    def _link_group(self):
        group = QGroupBox("KLayout")
        box = QVBoxLayout(group)
        self.link_label = QLabel()
        self.link_label.setWordWrap(True)
        box.addWidget(self.link_label)
        row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip("Connect to a running KLayout with the Remote Link on, or start KLayout")
        self.connect_btn.clicked.connect(self.connect_or_start)
        self.open_btn = QPushButton("Open Layout in KLayout ...")
        self.open_btn.clicked.connect(self.browse_layout)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect)
        for b in (self.connect_btn, self.open_btn, self.disconnect_btn):
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("KLayout program"))
        self.program_edit = QLineEdit(find_klayout())
        self.program_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        row.addWidget(self.program_edit, 1)
        browse = QPushButton("Browse ...")
        browse.clicked.connect(self.browse_program)
        row.addWidget(browse)
        row.addWidget(QLabel("Technology"))
        self.technology_edit = QLineEdit(get_preference("setupEM", PREF_TECHNOLOGY, "sg13g2"))
        self.technology_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.technology_edit.setToolTip("KLayout technology for layouts opened from here (the PDK's device library "
                                        "binds only with it); empty: none")
        self.technology_edit.setFixedWidth(90)
        row.addWidget(self.technology_edit)
        box.addLayout(row)
        return group

    def _setup_group(self):
        group = QGroupBox("Stackup, EM region, ground")
        form = QFormLayout(group)
        self.stackup_label, row = self._line_with_buttons((("Choose ...", self.choose_stackup),))
        form.addRow("Stackup", row)
        self.region_label, row = self._line_with_buttons((("Mark from Selection", lambda: self.run("mark_region")),
                                                          ("Remove", lambda: self.run("clear_region"))))
        form.addRow("Region", row)
        self.ground_label, row = self._line_with_buttons((("Mark Selected as Ground", lambda: self.run("mark_ground")),
                                                          ("Clear Marks", lambda: self.run("clear_ground")),
                                                          ("Run Checks", lambda: self.refresh(run_checks=True))))
        self.show_ground = QCheckBox("show in layout")
        self.show_ground.toggled.connect(self._show_ground)
        row.addWidget(self.show_ground)
        form.addRow("Ground", row)
        self.cleanup_label, row = self._line_with_buttons((("Remove Dummy Fill", self.remove_dummy_fill),
                                                           ("Fill Density Holes and Merge Metal", self.fill_and_merge)))
        self.cleanup_label.setText("for layouts with fill: one undo step each")
        form.addRow("Layout", row)
        return group

    def _line_with_buttons(self, buttons):
        label = QLabel()
        label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(label, 1)
        for text, handler in buttons:
            b = QPushButton(text)
            b.clicked.connect(handler)
            row.addWidget(b)
        return label, row

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _devices_group(self):
        group = QGroupBox("Devices")
        box = QVBoxLayout(group)
        self.devices_label = QLabel()
        self.devices_label.setWordWrap(True)
        self.devices_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        box.addWidget(self.devices_label)
        self.devices_table = self._table(["#", "Type", "Value", "Ports", "Status"])
        self.devices_table.setMaximumHeight(160)
        self.devices_table.itemSelectionChanged.connect(self._devices_selected)
        self.devices_table.cellDoubleClicked.connect(self._zoom_to_device)
        box.addWidget(self.devices_table)
        row = QHBoxLayout()
        self.include_resistors = QCheckBox("include resistors")
        self.include_resistors.setChecked(True)
        self.include_resistors.setToolTip("unreplaced, a resistor's R in the EM model is too low (no width offset and "
                                          "contact resistance of the PDK model)")
        row.addWidget(self.include_resistors)
        for text, handler in (("Replace All by Ports", self.replace_devices),
                              ("Undo Replace", lambda: self.run("unreplace_devices"))):
            b = QPushButton(text)
            b.clicked.connect(handler)
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        return group

    def _ports_group(self):
        group = QGroupBox("Ports in the layout (Export and Load copies them to the Ports tab)")
        box = QVBoxLayout(group)
        self.ports_label = QLabel()
        self.ports_label.setWordWrap(True)
        box.addWidget(self.ports_label)
        self.ports_table = self._table(["Port", "Layer", "Settings", "Export #", "Problems"])
        self.ports_table.setMinimumHeight(220)
        self.ports_table.itemSelectionChanged.connect(self._ports_selected)
        self.ports_table.cellDoubleClicked.connect(self._port_activated)
        box.addWidget(self.ports_table)
        row = QHBoxLayout()
        self.pick_btn = QPushButton("Pick Ports in KLayout")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("Click a metal edge in KLayout for a via port, Shift+click into a gap for an in-plane "
                                 "port (visible layers only)")
        self.pick_btn.toggled.connect(self._pick)
        row.addWidget(self.pick_btn)
        for text, handler in (("Settings ...", self.edit_ports), ("Delete", self.delete_ports),
                              ("Create Proposed Ports", self.create_proposed)):
            b = QPushButton(text)
            b.clicked.connect(handler)
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        return group

    def _export_group(self):
        group = QGroupBox("Export")
        box = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(QLabel("EM layout"))
        self.output_edit = QLineEdit()
        self.output_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.output_edit.setToolTip("The .simcfg and _portmap.json are written next to it, with the same name")
        row.addWidget(self.output_edit, 1)
        browse = QPushButton("Browse ...")
        browse.clicked.connect(self.browse_output)
        row.addWidget(browse)
        box.addLayout(row)
        self.shapes_only = QCheckBox("selected shapes only (+ connecting vias and ports)")
        self.shapes_only.toggled.connect(lambda checked: self._update_export())
        box.addWidget(self.shapes_only)
        self.export_label = QLabel()
        self.export_label.setWordWrap(True)
        box.addWidget(self.export_label)
        row = QHBoxLayout()
        self.export_btn = QPushButton("Export and Load")
        self.export_btn.setToolTip("Export the EM layout, .simcfg and port map, and load the .simcfg here")
        self.export_btn.clicked.connect(self.export)
        row.addWidget(self.export_btn)
        row.addStretch(1)
        box.addLayout(row)
        return group

    def _circuit_group(self):
        group = QGroupBox("Create Circuit: the EM result with the PDK devices put back, for a circuit simulator")
        form = QFormLayout(group)
        self.portmap_edit, self.snp_edit, self.pdk_edit, self.circuit_out_edit = (QLineEdit() for _ in range(4))
        self.pdk_edit.setText(get_preference("setupEM", PREF_PDK, ""))
        for label, edit, browse in (("Port map", self.portmap_edit, self._browse_portmap),
                                    ("S-parameters (.sNp, with 0 Hz)", self.snp_edit, self._browse_snp),
                                    ("PDK folder", self.pdk_edit, self._browse_pdk),
                                    ("Output", self.circuit_out_edit, self._browse_circuit_out)):
            edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            b = QPushButton("Browse ...")
            b.clicked.connect(browse)
            row.addWidget(b)
            form.addRow(label, row)
        self.pdk_edit.setToolTip("Optional: the ihp-sg13g2 or ihp-sg13cmos5l folder of IHP-Open-PDK, for the model "
                                 "library paths")
        self.simulator_box = QComboBox()
        self.circuit_choices = []
        try:
            from klayout_em_export import circuit
            self.circuit_choices = list(circuit.CHOICES)
        except Exception:               # an older klayout_em_export: its circuit module needs KLayout
            pass
        self.simulator_box.addItems([label for label, _, _ in self.circuit_choices])
        self.simulator_box.currentIndexChanged.connect(lambda index: self._update_circuit_output())
        self.snp_edit.textChanged.connect(lambda text: self._update_circuit_output())
        form.addRow("Simulator", self.simulator_box)
        self.circuit_btn = QPushButton("Create Circuit")
        self.circuit_btn.setEnabled(bool(self.circuit_choices))
        self.circuit_btn.setToolTip(f"runs the circuit wrapper of {PACKAGE} with this Python ({sys.executable}), "
                                    "which needs snp2le")
        self.circuit_btn.clicked.connect(self.create_circuit)
        form.addRow("", self.circuit_btn)
        return group

    def _browse_portmap(self):
        path, _ = QFileDialog.getOpenFileName(self, "Port map of the export", self.portmap_edit.text(),
                                              "Port map (*_portmap.json);;All files (*)")
        if path:
            self.portmap_edit.setText(path)

    def _browse_snp(self):
        path, _ = QFileDialog.getOpenFileName(self, "EM result with a 0 Hz point", self.snp_edit.text(),
                                              "Touchstone (*.s*p);;All files (*)")
        if path:
            self.snp_edit.setText(path)

    def _browse_pdk(self):
        path = QFileDialog.getExistingDirectory(self, "IHP-Open-PDK folder (ihp-sg13g2 or ihp-sg13cmos5l)",
                                                self.pdk_edit.text())
        if path:
            self.pdk_edit.setText(path)
            set_preference("setupEM", PREF_PDK, path)

    def _browse_circuit_out(self):
        path, _ = QFileDialog.getSaveFileName(self, "Circuit file", self.circuit_out_edit.text(), "All files (*)")
        if path:
            self.circuit_out_edit.setText(path)

    def _update_circuit_output(self):
        if not self.circuit_choices:
            return
        from klayout_em_export import circuit
        _, simulator, em = self.circuit_choices[self.simulator_box.currentIndex()]
        self.circuit_out_edit.setText(circuit.default_output(self.snp_edit.text().strip(), simulator, em))

    def create_circuit(self):
        from klayout_em_export import circuit
        portmap, snp = self.portmap_edit.text().strip(), self.snp_edit.text().strip()
        output = self.circuit_out_edit.text().strip()
        missing = [name for name, path in (("port map", portmap), ("S-parameter file", snp)) if not os.path.isfile(path)]
        if missing or not output:
            QMessageBox.warning(self, "Create Circuit", "Choose the " + (" and ".join(missing) or "output file") +
                                " first.")
            return
        _, simulator, em = self.circuit_choices[self.simulator_box.currentIndex()]
        self.circuit_btn.setEnabled(False)
        self.circuit_btn.setText("Creating ...")
        QApplication.processEvents()
        try:
            ok, report = circuit.run_wrapper(sys.executable, portmap, snp, output, simulator, em,
                                             self.pdk_edit.text().strip())
        finally:
            self.circuit_btn.setEnabled(True)
            self.circuit_btn.setText("Create Circuit")
        box = QMessageBox(QMessageBox.Information if ok else QMessageBox.Warning, "Create Circuit",
                          f"Circuit for {simulator}: {output}" if ok else "The circuit wrapper failed.",
                          QMessageBox.Ok, self)
        box.setDetailedText(report)
        box.exec()
        return ok

    # ---------- link ----------

    def connect_or_start(self):
        if self.link is not None:
            self.refresh()
            return
        try:
            self._connected(krc.connect())
            return
        except krc.LinkClosed:
            pass
        program = self.program_edit.text().strip()
        if not program or not os.path.isfile(program):
            QMessageBox.warning(self, "KLayout", "No KLayout with the Remote Link on is running, and the KLayout "
                                "program is not set: choose it with Browse ... (or start KLayout and switch the "
                                "link on). " + MENU_HINT)
            return
        answer = QMessageBox.question(self, "KLayout", "No KLayout with the Remote Link on is running.\n\nStart "
                                      f"{program}?\n\n(A KLayout you started yourself: {MENU_HINT})")
        if answer == QMessageBox.Yes:
            self.start(program)

    def start(self, program, args=("-e",), env=None):
        """Start KLayout (editable mode) with the link on; connects without blocking setupEM."""
        env = dict(os.environ if env is None else env, KLAYOUT_REMOTE="1")
        env.pop("PYTHONHOME", None)     # this Python's settings must not leak into KLayout's
        env.pop("PYTHONPATH", None)
        try:
            process = subprocess.Popen([program, *args], env=env)
        except OSError as e:
            QMessageBox.critical(self, "KLayout", f"Failed to start {program}:\n{e}")
            return
        self._starting = (process, time.time() + START_TIMEOUT)
        self.link_label.setText(marked("warn", f"starting KLayout (pid {process.pid}) ..."))
        self.start_timer.start()

    def _try_started(self):
        process, deadline = self._starting
        if process.poll() is not None:
            self.start_timer.stop()
            self._starting = None
            self._show_disconnected(f"KLayout ended at start (exit code {process.returncode})")
            return
        try:
            link = krc.connect(process.pid)
        except krc.LinkClosed:
            if time.time() > deadline:
                self.start_timer.stop()
                self._starting = None
                self._show_disconnected("KLayout runs, but the Remote Link did not start: is the klayout_remote "
                                        "package installed in KLayout?")
            return
        if self._connected(link, quiet_busy=True) == "busy":      # a start dialog in KLayout: try again
            return
        self.start_timer.stop()
        self._starting = None
        self.process = process

    def _connected(self, link, quiet_busy=False):
        """Load the plug-in into the connected KLayout. Returns True, False, or "busy" (KLayout shows a dialog;
        with quiet_busy nothing is said, the caller tries again)."""
        folder = em_package_folder()
        if folder is None:
            link.close()
            QMessageBox.critical(self, "KLayout", f"The {PACKAGE} package is not installed in this Python "
                                 "(pip install klayout_em_export), or set KLAYOUT_EM_EXPORT to its folder.")
            return False
        try:
            link.load_package(PACKAGE, folder)
            version = link.call(REMOTE, "version")
        except krc.BusyError:
            link.close()
            if quiet_busy:
                return "busy"
            QMessageBox.information(self, "KLayout", "KLayout is waiting for a dialog to be closed: close it in "
                                    "KLayout, then connect again.")
            return False
        except (krc.RemoteError, krc.LinkClosed) as e:
            link.close()
            QMessageBox.critical(self, "KLayout", f"Could not load {PACKAGE} into KLayout:\n{e}")
            return False
        self.link = link
        self.em_version = version
        if not link.hello.get("editable"):
            QMessageBox.warning(self, "KLayout", "This KLayout runs in viewer mode: it cannot change the layout "
                                "(ports, devices, region). Start KLayout in editor mode (klayout -e, or File > "
                                "Setup > Application > Editing mode).")
        self.poll_timer.start()
        self.refresh()
        return True

    def disconnect(self):
        self._stop_pick_button()
        if self.link is not None:
            try:
                self.link.request("highlight", shapes=[])
            except (krc.RemoteError, krc.LinkClosed):
                pass
            self.link.close()
        self.link = None
        self.poll_timer.stop()
        self._show_disconnected("not connected")

    def _lost(self, why):
        self.link = None
        self.poll_timer.stop()
        self._stop_pick_button()
        self._show_disconnected(f"connection lost: {why}")

    def _show_disconnected(self, why):
        self.link_label.setText(marked("error", f"KLayout: {why}. Connect finds a KLayout with the Remote Link on, "
                                                "or starts one."))
        for w in (self.stackup_label, self.region_label, self.ground_label, self.devices_label, self.ports_label,
                  self.export_label):
            w.setText("")
        self.devices_table.setRowCount(0)
        self.ports_table.setRowCount(0)
        self.port_rows, self.device_rows = [], []

    def _poll(self):
        if self.link is None:
            return
        try:
            events = self.link.poll()
        except krc.LinkClosed as e:
            self._lost(e)
            return
        for e in events:
            self._event(e)

    def _event(self, e):
        name = e.get("event")
        if name == "pick":
            if "error" in e:
                self.MainWindow.statusBar().showMessage(e["error"]["message"], 8000)
            else:
                r = e["result"]
                self.MainWindow.statusBar().showMessage(f"port {r['port']}: {r['text']}", 8000)
            self.refresh_timer.start()
        elif name == "pick_stopped":
            self._stop_pick_button()
        elif name in ("selection_changed",):
            self._update_export()
        else:                       # cell, view, layout changes
            self.refresh_timer.start()

    def call(self, function, quiet=False, **args):
        """remote.<function>(**args) in KLayout; None after a problem (shown to the user unless quiet)."""
        if self.link is None:
            if not quiet:
                QMessageBox.warning(self, "KLayout", "Not connected to KLayout: press Connect first.")
            return None
        try:
            return self.link.call(REMOTE, function, **args)
        except krc.BusyError:
            if not quiet:
                QMessageBox.information(self, "KLayout", "KLayout is waiting for a dialog to be closed: close it in "
                                        "KLayout, then try again.")
        except krc.LinkClosed as e:
            self._lost(e)
        except krc.RemoteError as e:
            if quiet:
                return None
            if e.type == "NeedsConfirmation":
                if QMessageBox.question(self, "KLayout", str(e)) == QMessageBox.Yes:
                    return "confirm"
            elif e.type == "Problem":
                QMessageBox.warning(self, "KLayout", str(e))
            else:
                box = QMessageBox(QMessageBox.Critical, "KLayout", f"{e.type}: {e}", QMessageBox.Ok, self)
                box.setDetailedText(e.traceback)
                box.exec()
        return None

    def run(self, function, **args):
        result = self.call(function, **args)
        if self.link is not None:
            self.refresh()
        return result

    # ---------- state ----------

    def refresh(self, run_checks=False):
        if self.link is None:
            return
        state = self.call("state", quiet=True, run_checks=run_checks)
        if state is None:
            return
        self.state = state
        hello = self.link.hello
        layout = state.get("layout")
        where = (f"{os.path.basename(layout['file']) or '(not saved)'}, cell {layout['cell']}" if layout
                 else "no layout open")
        self.link_label.setText(marked("ok" if layout else "warn",
                                       f"{hello['klayout']} (pid {hello['pid']}), {PACKAGE} {state['version']}: "
                                       f"{where}"))
        if not layout:
            for w in (self.stackup_label, self.region_label, self.ground_label, self.devices_label, self.ports_label,
                      self.export_label):
                w.setText("")
            self.devices_table.setRowCount(0)
            self.ports_table.setRowCount(0)
            return
        s = state["stackup"]
        if s["state"] == "error":
            self.stackup_label.setText(marked("error", f"{s['error']}: choose one"))
        elif s["unresolved"]:
            self.stackup_label.setText(marked("warn", f"{os.path.basename(s['path'])}: height of "
                                                      f"{', '.join(s['unresolved'])} is not resolved"))
        else:
            self.stackup_label.setText(marked("ok", os.path.basename(s["path"])))
        self.stackup_label.setToolTip(s["path"])
        r = state["region"]
        self.region_label.setText(marked("warn" if r["from_selection"] else "ok",
                                         r["source"] + ("" if r["box"] is None else
                                                        " [" + ", ".join(f"{v:g}" for v in r["box"]) + "] µm")))
        g = state["ground"]
        self.ground_label.setText(marked(g["state"], g["text"]))
        self.ground_label.setToolTip(g.get("detail", ""))
        self._fill_devices(state["devices"])
        self._fill_ports(state["ports"])
        if not self.output_edit.text().strip():
            info = self.call("export_info", quiet=True)
            if info:
                self.output_edit.setText(info["output"])
        self._update_export()
        if self.show_ground.isChecked():
            self._show_ground(True)

    def _item(self, text, tip="", grey=False):
        item = QTableWidgetItem(text)
        item.setToolTip(tip or text)
        if grey:
            item.setForeground(QBrush(QColor("#888888")))
        return item

    def _fill_devices(self, d):
        self.devices_label.setText(marked(d["state"], d["text"]) + "".join(
            f"<br><span style='color:#888'>{html.escape(n)}</span>" for n in d.get("notes", [])))
        self.devices_label.setToolTip(f"technology: {d.get('technology', '')}")
        self.device_rows = d.get("rows", [])
        self.devices_table.setRowCount(len(self.device_rows))
        for i, row in enumerate(self.device_rows):
            ports = ", ".join(f"{t} {n}" for t, n in row["ports"].items())
            values = ("" if row["number"] is None else str(row["number"]), row["type"], row["value"], ports,
                      row["status"])
            for col, value in enumerate(values):
                self.devices_table.setItem(i, col, self._item(value, f"{row['where']}\n{row['why']}"))

    def _fill_ports(self, p):
        types = self.state.get("devices", {}).get("types", {})     # device id -> type (npn13G2, rhigh, ...)
        self.ports_label.setText(marked(p["state"], p["text"]))
        self.port_rows = [("port", r) for r in p["rows"]] + [("proposed", r) for r in p["proposed"]] + \
                         [("cut", r) for r in p["cut_without_ground"]]
        self.ports_table.setRowCount(len(self.port_rows))
        for i, (kind, r) in enumerate(self.port_rows):
            if kind == "port":
                text = r["text"]
                if r["device"]:
                    k = r["device"]["number"]
                    name = types.get(r["device"]["id"]) or r["device"]["id"].split("@")[0]
                    text += f"  [{r['device']['terminal']} of {'#' + str(k) + ' ' if k else ''}{name}]"
                export = "– (outside)" if r["export_number"] is None else str(r["export_number"])
                values = (str(r["port"]), str(r["layer"]), text, export, "; ".join(r["problems"]) or "ok")
            elif kind == "proposed":
                values = ("–", "–", f"{r['metal']} cut ({r['side']} edge) → {r['ground_metal']}", "",
                          "proposed: double-click or Create Proposed Ports")
            else:
                values = ("–", "–", f"{r['metal']} cut ({r['side']} edge)", "",
                          "signal cut, but no ground metal at the cut: add a port by hand")
            for col, value in enumerate(values):
                self.ports_table.setItem(i, col, self._item(value, grey=kind != "port"))

    def _update_export(self):
        if self.link is None:
            return
        info = self.call("export_info", quiet=True, shapes_only=self.shapes_only.isChecked())
        if info is None:
            return
        self.shapes_only.setEnabled(info["has_selection"])
        if not info["has_selection"] and self.shapes_only.isChecked():
            self.shapes_only.setChecked(False)
            return
        if info["stackup_error"]:
            self.export_label.setText(marked("error", "choose a stackup first"))
            self.export_btn.setEnabled(False)
            return
        region = "selected shapes" if info["shapes_only"] else info["source"]
        notes = []
        if info.get("left_out"):
            notes.append(f"port(s) {', '.join(map(str, info['left_out']))}: no settings, left out of the .simcfg")
        notes += [p for p in info.get("problems", []) if not p.endswith(": no port settings")]
        if info.get("numbering_error"):
            notes.append(f"{info['numbering_error']}: the export is not possible")
        elif info.get("renumbered"):
            notes.append("renumbered for the export (.snp port ← layout port): " +
                         ", ".join(f"{a} ← {b}" for a, b in info["renumbered"]))
        state = "error" if info.get("numbering_error") else "warn" if notes or not info.get("ports") else "ok"
        text = f"{region}: {info.get('ports', 0)} port(s)"
        self.export_label.setText(marked(state, text) + "".join(
            f"<br><span style='color:#888'>• {html.escape(n)}</span>" for n in notes))
        self.export_btn.setEnabled(not info.get("numbering_error"))

    # ---------- actions ----------

    def browse_program(self):
        filt = "Programs (*.exe);;All files (*)" if os.name == "nt" else "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "KLayout program", self.program_edit.text(), filt)
        if path:
            self.program_edit.setText(path)
            set_preference("setupEM", PREF_PROGRAM, path)

    def browse_layout(self):
        if self.link is None:
            self.connect_or_start()
            if self.link is None:
                return
        path, _ = QFileDialog.getOpenFileName(self, "Open layout in KLayout", "", "Layouts (*.gds *.gds2 *.oas);;"
                                                                                  "All files (*)")
        if path:
            self.open_layout(path)

    def open_layout(self, path):
        technology = self.technology_edit.text().strip()
        set_preference("setupEM", PREF_TECHNOLOGY, technology)
        try:
            self.link.request("open_layout", path=path, technology=technology, mode=1)
        except krc.RemoteError as e:
            QMessageBox.warning(self, "KLayout", str(e))
        self.output_edit.setText("")
        self.refresh()

    def choose_stackup(self):
        current = (self.state.get("stackup") or {}).get("path") or ""
        path, _ = QFileDialog.getOpenFileName(self, "gds2palace stackup (XML)", current,
                                              "Stackup XML (*.xml);;All files (*)")
        if path:
            self.run("set_stackup", path=path)

    def _show_ground(self, shown):
        self.call("show_ground", quiet=True, show=bool(shown))

    def _devices_selected(self):
        rows = sorted({i.row() for i in self.devices_table.selectedItems()})
        ids = [self.device_rows[r]["id"] for r in rows if r < len(self.device_rows)]
        self.call("highlight", quiet=True, devices=ids)

    def _zoom_to_device(self, row, column):
        if row < len(self.device_rows):
            self.call("zoom_to", quiet=True, box=self.device_rows[row]["box"])

    def replace_devices(self):
        r = self.run("replace_devices", resistors=self.include_resistors.isChecked())
        if r is not None:
            self.MainWindow.statusBar().showMessage(f"{len(r['replaced'])} device(s) replaced by ports", 8000)

    def _selected_port_rows(self):
        rows = sorted({i.row() for i in self.ports_table.selectedItems()})
        return [self.port_rows[r] for r in rows if r < len(self.port_rows)]

    def _ports_selected(self):
        chosen = self._selected_port_rows()
        self.call("highlight", quiet=True, ports=[r["port"] for k, r in chosen if k == "port"],
                  proposed=[r["index"] for k, r in chosen if k != "port"])

    def _port_activated(self, row, column):
        kind, r = self.port_rows[row]
        if kind == "port":
            self.edit_ports([r["port"]])
        elif kind == "proposed":
            self.run("create_proposed", indices=[r["index"]])

    def _pick(self, on):
        if self.link is None:
            self._stop_pick_button()
            return
        try:
            if on:
                self.link.request("pick", module=REMOTE, prefix="port",
                                  hint="EM Port (setupEM): click a metal edge for a via port, Shift+click into a gap "
                                       "for an in-plane port (visible layers only)")
            else:
                self.link.request("stop_pick")
        except krc.RemoteError as e:
            QMessageBox.warning(self, "KLayout", str(e))
            self._stop_pick_button()
        except krc.LinkClosed as e:
            self._lost(e)

    def _stop_pick_button(self):
        self.pick_btn.blockSignals(True)
        self.pick_btn.setChecked(False)
        self.pick_btn.blockSignals(False)

    def edit_ports(self, ports=None):
        if not ports:
            ports = [r["port"] for k, r in self._selected_port_rows() if k == "port"] or \
                    (self.call("selected_ports", quiet=True) or [])
        if not ports:
            QMessageBox.information(self, "KLayout", "Select one or more ports in the table (or their shapes in "
                                    "KLayout) first.")
            return
        choices = self.call("port_choices", ports=ports)
        if choices is None:
            return
        dialog = PortSettingsDialog(self, ports, choices)
        if dialog.exec() != QDialog.Accepted:
            return
        r = self.run("set_ports", ports=ports, **dialog.values())
        if r and r["problems"]:
            QMessageBox.warning(self, "Port Settings", "\n".join(r["problems"]))

    def delete_ports(self):
        ports = [r["port"] for k, r in self._selected_port_rows() if k == "port"]
        if not ports:
            return
        if QMessageBox.question(self, "KLayout", f"Delete the shapes of port(s) {', '.join(map(str, ports))}?") \
                == QMessageBox.Yes:
            self.run("delete_ports", ports=ports)

    def create_proposed(self):
        created = self.run("create_proposed")
        if created is not None:
            self.MainWindow.statusBar().showMessage(f"port(s) {', '.join(map(str, created)) or 'none'} created at "
                                                    "the cut", 8000)

    def remove_dummy_fill(self):
        r = self.run("remove_dummy_fill")
        if r is None:
            return
        if not r["removed"]:
            QMessageBox.information(self, "Remove Dummy Fill", "No dummy fill found: no floating shapes on a stackup "
                                    f"metal that repeat {r['mincount']} times or more.")
            return
        lines = [f"{l['name']} ({l['layer']}): {l['shapes']}" for l in r["layers"]]
        QMessageBox.information(self, "Remove Dummy Fill", f"Removed {r['removed']} dummy fill shapes" +
                                (f" ({r['deleted_cells']} empty fill cells deleted)" if r["deleted_cells"] else "") +
                                ":\n" + "\n".join(lines) + "\n\nEdit > Undo in KLayout takes it back.")

    def fill_and_merge(self):
        r = self.run("fill_and_merge")
        if r is None:
            return
        lines = [f"{l['name']}: {l['filled']} holes filled" + (f" ({l['kept']} other kept)" if l["kept"] else "") +
                 f", {l['before']} -> {l['after']} polygons" for l in r["layers"]]
        QMessageBox.information(self, "Fill Density Holes and Merge Metal",
                                f"{r['filled']} density holes filled, metal merged" +
                                (f", with the metal of {r['subcells']} subcell(s)" if r["subcells"] else "") + ":\n" +
                                ("\n".join(lines) or "nothing to change") + "\n\nEdit > Undo in KLayout takes it back. "
                                "Do not tape out this layout once saved: the fill breaks the density rules.")

    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export EM layout", self.output_edit.text(), "GDSII (*.gds)")
        if path:
            self.output_edit.setText(path)

    def export(self):
        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(self, "Export", "Choose the EM layout file (.gds) first.")
            return
        args = {"output": output, "shapes_only": self.shapes_only.isChecked()}
        r = self.call("export", **args)
        if r == "confirm":
            r = self.call("export", overwrite=True, **args)
        if not isinstance(r, dict):
            return
        self.last_export = r
        self.refresh()
        self.MainWindow.statusBar().showMessage(f"Exported {r['gds']} ({r['simcfg_ports']} of {r['ports']} port(s) in "
                                                "the .simcfg)", 10000)
        self.load_export(r)

    def load_export(self, result):
        """Load the exported .simcfg here, as File > Load Config does; warn about older model scripts."""
        if result.get("stale_scripts"):
            names = ", ".join(os.path.basename(s["path"]) for s in result["stale_scripts"])
            box = QMessageBox(QMessageBox.Warning, "Export", f"Model scripts with other ports than this export: "
                              f"{names}.", QMessageBox.Ok, self)
            box.setInformativeText("Choose \"Continue with the script\" for one of them and press Create Model, so "
                                   "that it takes the new ports.")
            box.setDetailedText("\n\n".join(os.path.basename(s["path"]) + ":\n  " + "\n  ".join(s["differences"])
                                            for s in result["stale_scripts"]))
            box.exec()
        self.MainWindow.load_configuration_from_file(result["simcfg"])
        self.MainWindow.ports_tab.update_klayout_note()
        self.portmap_edit.setText(result["portmap"])

    def is_last_export(self, gds):
        """Whether gds is the EM layout of this tab's last Export and Load."""
        r = self.last_export
        return bool(r and gds) and os.path.normcase(os.path.abspath(str(gds))) == os.path.normcase(os.path.abspath(r["gds"]))

    def highlight_exported_port(self, layer):
        """Highlight in KLayout the layout port that became the exported port on GDS layer `layer` (setupEM's Ports
        tab), if the loaded model is the last export from this tab."""
        r = self.last_export
        if self.link is None or not self.is_last_export(self.MainWindow.saved_values.get("GdsFile")):
            return
        try:
            with open(r["portmap"]) as f:
                ports = json.load(f)["ports"]
        except (OSError, ValueError, KeyError):
            return
        layout_ports = [p["layout_port"] for p in ports if str(p.get("layer")) == str(layer)]
        if layout_ports:
            self.call("highlight", quiet=True, ports=layout_ports)
