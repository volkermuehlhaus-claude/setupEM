# Allowed values of stackup Variables (gds2palace stackup reader 1.11.0, schemaVersion "3.2"): Min/Max (with Step) or
# List, and Description.
#  - stackup_writer: validation through the reader's own check, schemaVersion "3.2",
#  - Stackup Editor, Variables tab: the Allowed values summary and AllowedValuesDialog,
#  - setupEM's override table: Allowed values column, a dropdown for a List, the Description as tooltip.
#
# Run from the repo root:   python -m pytest tests

import os
import sys
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox  # noqa: E402

from setupEM import stackup_writer  # noqa: E402
from setupEM import stackupEditor  # noqa: E402

STACKUP = '''<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="{schema}">
  <Variables>
    <Variable Name="total_thickness" Value="{total}" Min="25" Max="750" Step="5" Description="chip thickness, um" />
    <Variable Name="bulk_thickness" Value="=total_thickness - 20" Min="0" />
    <Variable Name="table_choice" Value="Si_a" Type="string" List="Si_a;Si_b" />
    <Variable Name="air_thickness" Value="100" />
  </Variables>
  <Materials><Material Name="Si" Type="Dielectric" Permittivity="11.9" /></Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Air" Material="Si" Thickness="=air_thickness" />
      <Dielectric Name="Bulk" Material="Si" Thickness="=bulk_thickness" />
    </Dielectrics>
    <Layers></Layers>
  </ELayers>
</Stackup>'''


def text(total="200", schema="3.2"):
    return STACKUP.format(total=total, schema=schema)


# ---------- stackup_writer ----------

def test_valid_limits_need_schema_3_2():
    root = ET.fromstring(text(schema="2.0"))
    assert stackup_writer.validate_stackup(root) == []
    assert stackup_writer.required_schema_version(root) == "3.2"


@pytest.mark.parametrize("total, message", [
    ("1000", "outside its allowed range 25 .. 750"),
    ("15", "outside its allowed range 25 .. 750"),       # also makes bulk_thickness negative; the first error is shown
])
def test_value_outside_is_an_error(total, message):
    errors = stackup_writer.validate_stackup(ET.fromstring(text(total=total)))
    assert any(message in e for e in errors), errors


def test_list_and_range_together_is_an_error():
    root = ET.fromstring(text())
    next(v for v in root.iter("Variable") if v.get("Name") == "table_choice").set("Min", "0")
    assert any("both List and Min/Max" in e for e in stackup_writer.validate_stackup(root))


# ---------- Stackup Editor ----------

@pytest.fixture
def editor(tmp_path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "stackup.xml"
    path.write_text(text(), encoding="utf-8")
    window = stackupEditor.StackupEditorWindow(stackupEditor._StandaloneMainWindow(), initial_filename=str(path))
    yield window
    window.close()


def row_of(window, name):
    return next(i for i, el in enumerate(window.variables_editor.row_elements) if el.get("Name") == name)


def column(attr):
    return [a for a, _h, _k in stackupEditor.VARIABLE_COLUMNS].index(attr)


def test_allowed_values_summary(editor):
    table = editor.variables_editor.table
    assert table.item(row_of(editor, "total_thickness"), column("AllowedValues")).text() == "25 .. 750, step 5"
    assert table.item(row_of(editor, "table_choice"), column("AllowedValues")).text() == "one of Si_a, Si_b"
    assert table.item(row_of(editor, "air_thickness"), column("AllowedValues")).text() == ""
    assert table.item(row_of(editor, "total_thickness"), column("Description")).text() == "chip thickness, um"


def test_dialog_sets_a_list_and_undo(editor, monkeypatch):
    air = editor.variables_editor.row_elements[row_of(editor, "air_thickness")]

    def run(dialog):
        assert dialog.any_button.isChecked()
        dialog.list_button.click()
        dialog.fields["List"].setText(" 50; 100 ;200 ")
        return QDialog.Accepted

    monkeypatch.setattr(stackupEditor.AllowedValuesDialog, "exec", run)
    editor._edit_allowed_values(air)
    air = editor.variables_editor.row_elements[row_of(editor, "air_thickness")]
    assert air.get("List") == "50;100;200" and "Min" not in air.attrib
    assert editor.variables_editor.table.item(row_of(editor, "air_thickness"), column("AllowedValues")).text() == \
        "one of 50, 100, 200"
    editor.undo()
    assert "List" not in editor.variables_editor.row_elements[row_of(editor, "air_thickness")].attrib


def test_dialog_switches_a_range_to_any_value(editor, monkeypatch):
    total = editor.variables_editor.row_elements[row_of(editor, "total_thickness")]

    def run(dialog):
        assert dialog.range_button.isChecked() and dialog.fields["Max"].text() == "750"
        dialog.any_button.click()
        return QDialog.Accepted

    monkeypatch.setattr(stackupEditor.AllowedValuesDialog, "exec", run)
    editor._edit_allowed_values(total)
    total = editor.variables_editor.row_elements[row_of(editor, "total_thickness")]
    assert not any(a in total.attrib for a in ("Min", "Max", "Step", "List"))
    assert total.get("Description") == "chip thickness, um"


# ---------- setupEM override table ----------

def test_override_table(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))
    path = tmp_path / "stackup.xml"
    path.write_text(text(), encoding="utf-8")
    from setupEM.setupEM import MainWindow
    window = MainWindow()
    try:
        tab = window.file_tab
        tab.update_variable_overrides_grid(str(path))
        table = tab.variable_overrides_table
        rows = {table.item(r, 0).text(): r for r in range(table.rowCount())}
        assert table.horizontalHeaderItem(3).text() == "Allowed values"
        assert table.item(rows["total_thickness"], 3).text() == "25 .. 750, step 5"
        assert "chip thickness, um" in table.item(rows["total_thickness"], 0).toolTip()
        combo = table.cellWidget(rows["table_choice"], 2)
        assert isinstance(combo, QComboBox) and [combo.itemText(i) for i in range(combo.count())] == ["Si_a", "Si_b"]
        assert table.cellWidget(rows["total_thickness"], 2) is None
        combo.setCurrentText("Si_b")               # written into the override item underneath
        assert tab.get_variable_overrides() == {"table_choice": "Si_b"}
    finally:
        window.close()
        app.processEvents()
