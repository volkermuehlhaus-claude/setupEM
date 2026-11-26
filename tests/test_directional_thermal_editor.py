# Direction-dependent thermal conductivity in the Stackup Editor (ThermalConductivity.xy / .z and
# ThermalConductivityTable.xy / .z, gds2palace stackup reader 1.11.0):
#  - stackup_writer: validation, schemaVersion "3.2" on save, a newer schemaVersion is never lowered,
#  - Materials tab: a "..." button on Thermal Cond. / Thermal Table, a read-only summary for a direction-dependent
#    material, DirectionalValueDialog writing the attributes (with undo), legacy conversion removing them,
#  - DirectionalValueDialog: one field per direction for a constant or a table, inherited value as placeholder.
#
# Run from the repo root:   python -m pytest tests

import os
import sys
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

from setupEM import stackup_writer  # noqa: E402
from setupEM import stackupEditor  # noqa: E402

STACKUP = '''<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="{schema}">
  <Tables>
    <Table Name="k_vert"><Point Temperature="250" Value="120" /><Point Temperature="350" Value="100" /></Table>
  </Tables>
  <Materials>
    <Material Name="SiO2" Type="Dielectric" Permittivity="4.1" ThermalConductivity="1.4" />
    <Material Name="Mold" Type="Dielectric" Permittivity="3.8" {mold} />
    <Material Name="RS" Type="Resistor" Rs="10" />
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Mold" Material="Mold" Thickness="20" />
      <Dielectric Name="SiO2" Material="SiO2" Thickness="10" />
    </Dielectrics>
    <Layers>
      <Layer Name="R" Type="sheet" Zmin="1" Zmax="1" Material="RS" Layer="5" />
    </Layers>
  </ELayers>
</Stackup>'''


def root_of(mold, schema="2.0"):
    return ET.fromstring(STACKUP.format(mold=mold, schema=schema))


# ---------- stackup_writer ----------

def test_valid_directional_material():
    assert stackup_writer.validate_stackup(
        root_of('ThermalConductivity.xy="0.9" ThermalConductivityTable.z="k_vert"')) == []


@pytest.mark.parametrize('mold, message', [
    ('ThermalConductivity.z="abc"', "invalid ThermalConductivity.z"),
    ('ThermalConductivityTable.xy="missing"', "undefined ThermalConductivityTable.xy"),
    ('ThermalConductivity.z="1" ThermalConductivityTable.z="k_vert"', "both ThermalConductivity.z"),
    ('ThermalConductivity.x="1"', "use ThermalConductivity.xy or ThermalConductivity.z"),
    ('Permittivity.z="3"', "unsupported attribute Permittivity.z"),
])
def test_invalid_directional_material(mold, message):
    errors = stackup_writer.validate_stackup(root_of(mold))
    assert any(message in e for e in errors), errors


def test_save_raises_schema_to_3_2(tmp_path):
    root = root_of('ThermalConductivity="1" ThermalConductivity.z="0.3"')
    assert stackup_writer.required_schema_version(root) == "3.2"
    path = str(tmp_path / "out.xml")
    stackup_writer.save_stackup_tree(ET.ElementTree(root), path)
    text = open(path, encoding="utf-8").read()
    assert 'schemaVersion="3.2"' in text
    assert stackup_writer.DIRECTIONAL_FORMAT_COMMENT_PREFIX in text


def test_save_never_lowers_the_schema(tmp_path):
    root = root_of('ThermalConductivity="1"', schema="3.2")
    assert stackup_writer.required_schema_version(root) == "2.0"
    path = str(tmp_path / "out.xml")
    stackup_writer.save_stackup_tree(ET.ElementTree(root), path)
    assert 'schemaVersion="3.2"' in open(path, encoding="utf-8").read()


# ---------- Materials tab ----------

@pytest.fixture
def editor(tmp_path, request):
    QApplication.instance() or QApplication([])
    path = tmp_path / "stackup.xml"
    path.write_text(STACKUP.format(mold=request.param, schema="3.2"), encoding="utf-8")
    window = stackupEditor.StackupEditorWindow(stackupEditor._StandaloneMainWindow(), initial_filename=str(path))
    yield window
    window._preview_window.deleteLater() if hasattr(window, "_preview_window") else None
    window.close()


def material_row(window, name):
    rows = window.materials_editor.row_elements
    return next(i for i, el in enumerate(rows) if el.get("Name") == name)


def column(window, attr):
    return next(i for i, (a, _h, _k) in enumerate(stackupEditor.MATERIAL_COLUMNS) if a == attr)


@pytest.mark.parametrize("editor", ['ThermalConductivity="0.9" ThermalConductivity.z="0.3"'], indirect=True)
def test_directional_material_shows_summary(editor):
    table = editor.materials_editor.table
    row = material_row(editor, "Mold")
    item = table.item(row, column(editor, "ThermalConductivity"))
    assert item.text() == "0.9 · z 0.3"
    assert not item.flags() & stackupEditor.Qt.ItemIsEditable
    assert "Lateral (x, y): 0.9, vertical (z): 0.3" in item.toolTip()
    # the plain material keeps an editable cell, with the "..." button drawn by the delegate
    sio2 = table.item(material_row(editor, "SiO2"), column(editor, "ThermalConductivity"))
    assert sio2.text() == "1.4" and sio2.flags() & stackupEditor.Qt.ItemIsEditable
    assert editor.materials_editor._has_dialog_button(material_row(editor, "SiO2"), "ThermalConductivity")
    # a resistor has no thermal values: no button
    assert not editor.materials_editor._has_dialog_button(material_row(editor, "RS"), "ThermalConductivity")
    # the table column of a direction-dependent material shows its (empty) table summary and a button
    cell = table.cellWidget(row, column(editor, "ThermalConductivityTable"))
    assert cell.findChild(QLabel) is not None


@pytest.mark.parametrize("editor", ['ThermalConductivity="1.4"'], indirect=True)
def test_dialog_writes_directions_and_undo(editor, monkeypatch):
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]

    def run(dialog):
        dialog.directional_button.click()
        dialog.fields["z"].setEditText("0.3")
        dialog.fields["xy"].setEditText("k_vert")       # a listed table
        return QDialog.Accepted

    monkeypatch.setattr(stackupEditor.DirectionalValueDialog, "exec", run)
    editor._edit_thermal_directions(mold)
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]
    assert mold.get("ThermalConductivity") == "1.4"
    assert mold.get("ThermalConductivity.z") == "0.3"
    assert mold.get("ThermalConductivityTable.xy") == "k_vert"
    assert "ThermalConductivity.xy" not in mold.attrib
    assert stackup_writer.validate_stackup(editor.tree.getroot()) == []

    editor.undo()
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]
    assert "ThermalConductivity.z" not in mold.attrib


@pytest.mark.parametrize("editor", ['ThermalConductivity="1.4" ThermalConductivity.z="0.3" '
                                    'ThermalConductivityTable.xy="k_vert"'], indirect=True)
def test_dialog_back_to_same_in_all_directions(editor, monkeypatch):
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    def run(dialog):
        assert dialog.directional_button.isChecked()
        assert dialog.fields["xy"].currentText() == "k_vert" and dialog.is_table("k_vert")
        assert dialog.fields["z"].currentText() == "0.3" and not dialog.is_table("0.3")
        dialog.same_button.click()
        return QDialog.Accepted

    monkeypatch.setattr(stackupEditor.DirectionalValueDialog, "exec", run)
    editor._edit_thermal_directions(mold)
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]
    assert dict(mold.attrib) == {"Name": "Mold", "Type": "Dielectric", "Permittivity": "3.8",
                                 "ThermalConductivity": "1.4"}
    item = editor.materials_editor.table.item(material_row(editor, "Mold"), column(editor, "ThermalConductivity"))
    assert item.text() == "1.4" and item.flags() & stackupEditor.Qt.ItemIsEditable


@pytest.mark.parametrize("editor", ['ThermalConductivity="1.4" ThermalConductivity.z="0.3"'], indirect=True)
def test_legacy_conversion_removes_directions(editor):
    editor._convert_to_legacy_format()
    mold = next(el for el in editor.tree.getroot().iter("Material") if el.get("Name") == "Mold")
    assert not any("ThermalConductivity" in attr for attr in mold.attrib)


@pytest.mark.parametrize("editor", ['ThermalConductivity="1.4" ThermalConductivity.z="0.3"'], indirect=True)
def test_dialog_field_constant_or_table(editor):
    mold = editor.materials_editor.row_elements[material_row(editor, "Mold")]
    dialog = stackupEditor.DirectionalValueDialog(
        editor, "t", mold, "ThermalConductivity", "ThermalConductivityTable", unit="W/m/K",
        table_names=["k_vert"], variable_names=["k_tab", "k_num"],
        is_table_expression_fn=lambda text: text == "=k_tab")
    assert dialog.directional_button.isChecked()
    # an empty direction shows the inherited value
    assert dialog.fields["xy"].lineEdit().placeholderText() == "1.4"
    for text, table in (("k_vert", True), ("=k_tab", True), ("=k_num * 2", False), ("2.5", False),
                        ("1e-3", False), ("missing_table", True), ("", False)):
        assert dialog.is_table(text) == table, text
    dialog.fields["xy"].setEditText("=k_tab")
    dialog.fields[""].setEditText("")
    assert dialog.fields["xy"].lineEdit().styleSheet() != ""        # a table is shown in blue
    assert dialog.fields["z"].lineEdit().placeholderText() == "not set"
    changes = dialog.changes()
    assert changes["ThermalConductivityTable.xy"] == "=k_tab" and changes["ThermalConductivity.xy"] == ""
    assert changes["ThermalConductivity.z"] == "0.3" and changes["ThermalConductivityTable.z"] == ""
    assert changes["ThermalConductivity"] == "" and changes["ThermalConductivityTable"] == ""
    dialog.deleteLater()
