# Temperature per heat source from an Elmer thermal result (thermal_results.py): the volume-weighted average and the
# hottest node of each source_<layer> body, the instance names of a thermal export's map, the CSV for the circuit
# simulation, the command line, and setupThermal writing them after a run. The result is a small synthetic one in
# Elmer's own .vtu format (raw appended data, which meshio cannot read) with a boundary element that must not count.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import struct
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import thermal_results  # noqa: E402

# a unit cube corner tetrahedron has volume 1/6; scaled by s it has s^3/6
POINTS = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],      # tet A (source_205), volume 1/6
                   [2, 0, 0], [4, 0, 0], [2, 2, 0], [2, 0, 2],      # tet B (source_205), volume 8/6
                   [5, 0, 0], [6, 0, 0], [5, 1, 0], [5, 0, 1]],     # tet C (SiO2)
                  dtype=np.float64) * 1e-6
TEMPERATURE = np.array([400, 400, 400, 400, 310, 310, 310, 330, 305, 305, 305, 305], dtype=np.float64)
# cells: 3 tetrahedra (VTK 10) and one boundary triangle (VTK 5) of the source, Elmer's GeometryIds = body / boundary
CELLS = [(10, [0, 1, 2, 3], 3), (10, [4, 5, 6, 7], 3), (10, [8, 9, 10, 11], 1), (5, [4, 5, 6], 3)]
NAMES = """! ----- names for bodies -----
$ SiO2 = 1
$ source_202 = 2
$ source_205 = 3
! ----- names for boundaries -----
$ constanttemp_201 = 3
"""


def write_raw_vtu(path, flux_arrays_first=False):
    """An Elmer-style .vtu: all arrays in one <AppendedData encoding="raw"> block, each with a UInt32 byte count.
    flux_arrays_first: with "temperature flux" and "temperature grad" (gds2palace heat_flux_output) before the
    temperature, so the temperature array must be found by its name, not by "temp" in it."""
    connectivity = np.array([n for _, nodes, _ in CELLS for n in nodes], dtype=np.int32)
    offsets = np.cumsum([len(nodes) for _, nodes, _ in CELLS]).astype(np.int32)
    types = np.array([t for t, _, _ in CELLS], dtype=np.uint8)
    ids = np.array([i for _, _, i in CELLS], dtype=np.int32)
    vectors = np.full((len(POINTS), 3), 1e9)       # far above any temperature: picked as one, they would show
    flux = [("PointData", f'type="Float64" Name="temperature {name}" NumberOfComponents="3"', vectors.ravel())
            for name in ("flux", "grad")] if flux_arrays_first else []
    arrays = flux + [("PointData", 'type="Float64" Name="temperature" NumberOfComponents="1"', TEMPERATURE),
              ("CellData", 'type="Int32" Name="GeometryIds"', ids),
              ("Points", 'type="Float64" NumberOfComponents="3"', POINTS.ravel()),
              ("Cells", 'type="Int32" Name="connectivity"', connectivity),
              ("Cells", 'type="Int32" Name="offsets"', offsets),
              ("Cells", 'type="UInt8" Name="types"', types)]
    blob, sections, offset = b"", {}, 0
    for section, attributes, data in arrays:
        raw = data.tobytes()
        sections.setdefault(section, []).append(f'<DataArray {attributes} format="appended" offset="{offset}"/>')
        blob += struct.pack("<I", len(raw)) + raw
        offset += 4 + len(raw)
    header = ('<?xml version="1.0"?>\n<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">\n'
              f'<UnstructuredGrid>\n<Piece NumberOfPoints="{len(POINTS)}" NumberOfCells="{len(CELLS)}">\n' +
              "".join(f"<{s}>\n" + "\n".join(sections[s]) + f"\n</{s}>\n"
                      for s in ("PointData", "CellData", "Points", "Cells")) +
              '</Piece>\n</UnstructuredGrid>\n<AppendedData encoding="raw">\n_')
    with open(path, "wb") as f:
        f.write(header.encode() + blob + b"\n</AppendedData>\n</VTKFile>\n")


def make_result(run_path, flux_arrays_first=False):
    os.makedirs(os.path.join(run_path, "mesh"))
    with open(os.path.join(run_path, "mesh", "mesh.names"), "w") as f:
        f.write(NAMES)
    write_raw_vtu(os.path.join(run_path, "thermal_results_t0001.vtu"), flux_arrays_first)


THERMAL = [{"type": "source", "source_layernum": 205, "power": 0.1, "target_layername": "Activ"},
           {"type": "source", "source_layernum": 202, "power": 0.01, "target_layername": "Activ"},
           {"type": "constanttemp", "source_layernum": 201, "temp": 300.0, "target_layername": "BACKSIDEGND"}]
MAP = {"format": 1, "outline_layer": 201,
       "sources": [{"layer": 205, "device": "npn13G2@-7230,590", "pcell": "npn13G2", "instance": "Q1"},
                   {"layer": 202, "device": "rhigh_CDNS_1@-28550,405", "pcell": "rhigh", "instance": None}]}
# volume-weighted: tet A (1/6, 400 K), tet B (8/6, mean of 310, 310, 310, 330 = 315 K)
T_MEAN = (400 * 1 + 315 * 8) / 9


def test_source_temperatures(tmp_path):
    run = str(tmp_path / "run")
    make_result(run)
    assert thermal_results.read_body_names(run) == {1: "SiO2", 2: "source_202", 3: "source_205"}
    temperatures = thermal_results.source_temperatures(run)
    assert list(temperatures) == [205], "a source without tetrahedra (202) has no temperature"
    t_mean, t_max = temperatures[205]
    assert t_mean == pytest.approx(T_MEAN) and t_max == 400.0


def test_temperature_key_is_not_flux_or_gradient():
    keys = ["temperature flux", "temperature grad", "temperature loads", "temperature", "GeometryIds"]
    assert thermal_results.temperature_key(keys) == "temperature"
    assert thermal_results.temperature_key(["Temperature"]) == "Temperature"
    assert thermal_results.temperature_key(["temperature flux", "temperature grad"]) is None
    assert thermal_results.is_temperature_array("temperature")
    assert not thermal_results.is_temperature_array("temperature flux")


def test_source_temperatures_with_heat_flux_output(tmp_path):
    """gds2palace heat_flux_output: two more arrays whose names start with "temperature"."""
    run = str(tmp_path / "run")
    make_result(run, flux_arrays_first=True)
    t_mean, t_max = thermal_results.source_temperatures(run)[205]
    assert t_mean == pytest.approx(T_MEAN) and t_max == 400.0
    x, y, z = thermal_results.find_max_temperature_location(run)
    assert (x, y, z) == pytest.approx((0, 0, 0)), "a node of tet A (400 K), not of the 1e9 vectors"


HEATFLOW_NAMES = """Metadata for SaveScalars file: mesh/../thermal_heatflow.dat
Elmer version: 26.2
Variables in columns of matrix:
   1: boundary sum: temperature loads over bc heatflow_constanttemp_202 : mpi_sum
   2: boundary sum: temperature loads over bc heatflow_constanttemp_203 : mpi_sum
"""


def test_heat_flow_per_boundary(tmp_path):
    run = str(tmp_path / "run")
    os.makedirs(run)
    assert thermal_results.read_heatflow(run) is None, "no file: a model from gds2palace before 0.13.0"
    with open(os.path.join(run, "thermal_results.dat"), "w") as f:
        f.write("   2.979973691948E+002   3.460700588622E+002\n")
    assert "Heat flow" not in thermal_results.build_thermal_summary(run)
    with open(os.path.join(run, "thermal_heatflow.dat"), "w") as f:
        f.write("  -1.082133187026E+000   4.323221702750E-001\n")
    with open(os.path.join(run, "thermal_heatflow.dat.names"), "w") as f:
        f.write(HEATFLOW_NAMES)
    heatflow = thermal_results.read_heatflow(run)
    assert heatflow == [("constanttemp_202", pytest.approx(1.082133)), ("constanttemp_203", pytest.approx(-0.432322))]
    summary = thermal_results.build_thermal_summary(run)
    assert "GDS layer 202   : 1.082 W  (166.5 %)" in summary
    assert "GDS layer 203   : -0.4323 W  (-66.5 %)" in summary
    assert "Total           : 0.6498 W (= input power)" in summary


def test_device_temperatures_and_csv(tmp_path):
    run = str(tmp_path / "run")
    make_result(run)
    rows, t_ref = thermal_results.device_temperatures(run, THERMAL, MAP)
    assert t_ref == 300.0 and len(rows) == 1
    row = rows[0]
    assert (row["instance"], row["device"], row["pcell"], row["power_W"]) == ("Q1", "npn13G2@-7230,590", "npn13G2",
                                                                              0.1)
    assert row["dT_mean_K"] == pytest.approx(T_MEAN - 300) and row["dT_max_K"] == 100.0
    assert thermal_results.device_temperatures(str(tmp_path / "none"), THERMAL, MAP) == ([], None)

    csv_path = str(tmp_path / "t.csv")
    thermal_results.write_temperatures_csv(csv_path, rows, t_ref)
    lines = open(csv_path).read().splitlines()
    assert lines[0].startswith("#") and "300 K" in lines[1]
    assert lines[2] == "instance,device,pcell,layer,power_W,T_mean_K,T_max_K,dT_mean_K,dT_max_K"
    assert lines[3].startswith('Q1,"npn13G2@-7230,590",npn13G2,205,0.1,'), "the device id has a comma: quoted"
    table = thermal_results.format_temperature_table(rows)
    assert table[0].split()[0] == "Instance" and table[2].startswith("Q1")


def test_command_line(tmp_path, capsys):
    sim = tmp_path / "model"
    make_result(str(sim / "elmer_model" / "cascode_thermal_data"))
    gds = tmp_path / "cascode_thermal.gds"
    (tmp_path / "cascode_thermal_thermalmap.json").write_text(json.dumps(MAP))
    tsimcfg = tmp_path / "cascode_thermal.tsimcfg"
    tsimcfg.write_text(json.dumps({"application": "setupThermal", "thermal": THERMAL, "saved_values": {
        "sim_path": str(sim), "model_basename": "cascode_thermal", "GdsFile": str(gds)}}))
    assert thermal_results.main([str(tsimcfg)]) == 0
    out = capsys.readouterr().out
    assert "Q1" in out and "100.0 K" in out
    assert (sim / "cascode_thermal_temperatures.csv").is_file()
    capsys.readouterr()
    assert thermal_results.main([str(tmp_path / "missing.tsimcfg")]) == 1
    captured = capsys.readouterr()
    assert "missing.tsimcfg" in captured.err and captured.out == "", "a handled error goes to stderr"
    for arguments, code in (([], 2), (["--help"], 0), (["--version"], 0)):
        with pytest.raises(SystemExit) as exit_info:
            thermal_results.main(arguments)
        assert exit_info.value.code == code


def test_setupthermal_writes_temperatures(tmp_path, monkeypatch):
    """After a run, setupThermal logs the table and writes <model>_temperatures.csv next to the model script."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    import setupEM.setupThermal as setupthermal
    win = setupthermal.MainWindow()
    sim = tmp_path / "model"
    run = sim / "elmer_model" / "cascode_thermal_data"
    make_result(str(run))
    (tmp_path / "cascode_thermal_thermalmap.json").write_text(json.dumps(MAP))
    win.file_tab.gds_file_edit.setText(str(tmp_path / "cascode_thermal.gds"))
    win.saved_values["sim_path"] = str(sim)
    win.saved_values["model_basename"] = "cascode_thermal"
    monkeypatch.setattr(setupthermal.thermal_objects, "objects", [
        setupthermal.simulation_setup.heatsource(power=0.1, source_layernum=205, target_layername="Activ"),
        setupthermal.simulation_setup.constanttemp(temp=300.0, source_layernum=201, target_layername="BACKSIDEGND")])
    tab = win.create_model_tab
    tab.log_area.clear()
    tab._append_device_temperatures(str(run))
    log = tab.log_area.toPlainText()
    assert "Temperature per heat source" in log and "Q1" in log and "dT relative to 300 K" in log
    csv_path = sim / "cascode_thermal_temperatures.csv"
    assert csv_path.is_file() and 'Q1,"npn13G2@-7230,590"' in csv_path.read_text()
    win._script_dirty = False
    win._config_baseline = None
    win.close()


def test_field_choice_shows_units():
    """The field viewer's Field choice shows Elmer thermal arrays with their unit, but works with the plain names."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from setupEM import field_viewer
    names = ["temperature", "temperature flux", "temperature grad", "GeometryIds"]
    combo = field_viewer._ArrayCombo(lambda: field_viewer._ELMER_THERMAL)
    combo.addItems(names)
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "temperature [K]", "temperature flux [W/m²]", "temperature grad [K/m]", "GeometryIds"]
    combo.setCurrentText("temperature grad")
    assert combo.currentText() == "temperature grad" and combo.currentIndex() == 2
    palace = field_viewer._ArrayCombo(lambda: field_viewer._PALACE)
    palace.addItems(["E_real", "temperature"])
    assert palace.itemText(1) == "temperature", "units only for Elmer thermal results"


def test_field_viewer_layers_from_mesh(tmp_path):
    """Z clip: the layers are the bodies of the Elmer mesh with their z range, top down, in the slider's right-click
    menu; a layer moves the plane to its middle and turns clipping on. Only volume cells count, not the boundary
    triangle."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import pyvista as pv
    from setupEM import field_viewer
    run = str(tmp_path / "run")
    make_result(run)
    vtu = os.path.join(run, "thermal_results_t0001.vtu")
    ranges = field_viewer._body_z_ranges(pv.read(vtu), thermal_results.read_body_names(run))
    assert ranges == [("source_205", 0.0, pytest.approx(2e-6)), ("SiO2", 0.0, pytest.approx(1e-6))]
    assert field_viewer._body_z_ranges(pv.read(vtu), {}) == []

    window = field_viewer.FieldViewerWindow(field_viewer._StandaloneMainWindow(), [vtu], "elmer_thermal",
                                            off_screen=True)
    try:
        assert window.clip_slider.toolTip() == "Right-click: clip at a layer"

        def actions():
            return [a for a in window._layer_menu().actions() if not a.isSeparator()]

        layers = {a.text().split()[0]: a for a in actions() if "µm" in a.text()}
        assert list(layers) == ["source_205", "SiO2"], "top down"
        layers["SiO2"].trigger()
        assert window._slider_value_to_position() == pytest.approx(0.5e-6, abs=1e-11), "a layer: its middle"
        assert window.clip_enabled_cb.isChecked()
        # inside SiO2 (0..1 um) and source_205 (0..2 um): the thinner one is named, ticked and offered first
        assert window.clip_position_label.text().endswith("· in SiO2 (50 %)")
        menu = actions()
        assert [a.text() for a in menu[:2]] == ["Top of SiO2", "Bottom of SiO2"]
        assert [a.text().split()[0] for a in menu if a.isChecked()] == ["SiO2"]
        menu[0].trigger()
        assert window._slider_value_to_position() == pytest.approx(0.99e-6, abs=1e-11), "1 % inside the layer"
        window.axis_radio_x.setChecked(True)
        assert window._layer_menu() is None and window.clip_slider.toolTip() == "", "Z axis only"
    finally:
        window.close()
