# Datatype per stackup layer (gds2palace stackup reader 1.11.0, schemaVersion "3.2"): Datatype on <Layer> and on a
# derived layer's <Operand>, BoundaryDatatype on <Dielectric>.
#  - stackup_writer: validation, schemaVersion "3.2", operands written as "66/20",
#  - Stackup Editor: optional Datatype columns, the Operands cell,
#  - setupEM: the hint next to the datatype field of the Input Files tab, layer numbers for the port layer hints.
#
# Run from the repo root:   python -m pytest tests

import os
import sys
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from setupEM import stackup_writer  # noqa: E402
from setupEM import stackupEditor  # noqa: E402

STACKUP = '''<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="{schema}">
  <Materials>
    <Material Name="Ox" Type="Dielectric" Permittivity="4" />
    <Material Name="Cu" Type="Conductor" Conductivity="5.8e7" />
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Box" Material="Ox" Zmin="0" Zmax="10" {box} />
    </Dielectrics>
    <Layers>
      <Layer Name="M1" Type="conductor" Material="Cu" Layer="68" {m1} Zmin="1" Zmax="2" />
      <Layer Name="V1" Type="via" Material="Cu" Layer="68" Datatype="44" Zmin="2" Zmax="3" />
      <Layer Name="M2" Type="conductor" Material="Cu" Layer="69" Zmin="3" Zmax="4" />
      <Layer Name="MD" Type="conductor" Material="Cu" Layer="310" {md} Zmin="6" Zmax="7" />
      <Layer Name="MD2" Type="conductor" Material="Cu" Layer="311" Zmin="8" Zmax="9" />
    </Layers>
    <DerivedLayers>
      <DerivedLayer Name="MD" Layer="310" Operation="AND">
        <Operand Layer="68" Datatype="20" />
        <Operand Layer="70" />
      </DerivedLayer>
      <DerivedLayer Name="MD2" Layer="311" Operation="SIZE" Oversize="1">
        <Operand Layer="310" {operand} />
      </DerivedLayer>
    </DerivedLayers>
  </ELayers>
</Stackup>'''


def text(m1='Datatype="20"', box='Boundary="301" BoundaryDatatype="5"', md="", operand="", schema="3.2"):
    return STACKUP.format(m1=m1, box=box, md=md, operand=operand, schema=schema)


# ---------- stackup_writer ----------

def test_valid_and_schema():
    root = ET.fromstring(text(schema="3.0"))
    assert stackup_writer.validate_stackup(root) == []
    assert stackup_writer.required_schema_version(root) == "3.2"


@pytest.mark.parametrize("kwargs, message", [
    ({"m1": 'Datatype="a"'}, "invalid Datatype"),
    ({"box": 'BoundaryDatatype="5"'}, "BoundaryDatatype but no Boundary"),
    ({"md": 'Datatype="1"'}, "is a derived layer"),
    ({"operand": 'Datatype="1"'}, "can have no Datatype"),
])
def test_invalid(kwargs, message):
    errors = stackup_writer.validate_stackup(ET.fromstring(text(**kwargs)))
    assert any(message in e for e in errors), errors


def test_operands_with_datatype():
    root = ET.fromstring(text())
    derived = next(el for el in root.iter("DerivedLayer") if el.get("Name") == "MD")
    assert stackup_writer.get_operand_layers(derived) == ["68/20", "70"]
    stackup_writer.set_operands(derived, ["66/13", " 70 ", "5/0"])
    assert [dict(op.attrib) for op in derived.findall("Operand")] == [
        {"Layer": "66", "Datatype": "13"}, {"Layer": "70"}, {"Layer": "5", "Datatype": "0"}]


# ---------- Stackup Editor ----------

def test_editor_columns(tmp_path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "stackup.xml"
    path.write_text(text(), encoding="utf-8")
    window = stackupEditor.StackupEditorWindow(stackupEditor._StandaloneMainWindow(), initial_filename=str(path))
    try:
        layer_col = [a for a, _h, _k in stackupEditor.LAYER_COLUMNS].index("Datatype")
        rows = {el.get("Name"): i for i, el in enumerate(window.layers_editor.row_elements)}
        table = window.layers_editor.table
        assert table.horizontalHeaderItem(layer_col).text() == "Datatype (optional)"
        assert table.item(rows["V1"], layer_col).text() == "44" and table.item(rows["M2"], layer_col).text() == ""
        boundary_col = [a for a, _h, _k in stackupEditor.DIELECTRIC_COLUMNS].index("BoundaryDatatype")
        assert window.dielectrics_editor.table.item(0, boundary_col).text() == "5"
        operands_col = [a for a, _h, _k in stackupEditor.DERIVED_LAYER_COLUMNS].index("Operands")
        derived_rows = {el.get("Name"): i for i, el in enumerate(window.derived_layers_editor.row_elements)}
        assert window.derived_layers_editor.table.item(derived_rows["MD"], operands_col).text() == "68/20, 70"
    finally:
        window.close()


# ---------- setupEM ----------

def test_purpose_hint_and_port_layer_numbers(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))
    from gds2palace import stackup_reader
    from setupEM.setup_common import stackup_gds_layer_numbers
    from setupEM.setupEM import MainWindow
    path = tmp_path / "stackup.xml"
    path.write_text(text(), encoding="utf-8")
    _, _, metals = stackup_reader.read_substrate(str(path))
    assert stackup_gds_layer_numbers(metals) == {68, 69, 310, 311}
    window = MainWindow()
    try:
        tab = window.file_tab
        tab.update_purpose_hint(metals)
        assert "the stackup sets the datatype of 2 layer(s): M1 68/20, V1 68/44" in tab.purpose_label2.text()
        path.write_text(text(m1="").replace('Datatype="44"', ""), encoding="utf-8")
        _, _, plain = stackup_reader.read_substrate(str(path))
        tab.update_purpose_hint(plain)
        assert tab.purpose_label2.text() == " (comma-separated, default 0)"
    finally:
        window.close()
        app.processEvents()
