# Priority on <Dielectric> and <Layer> (gds2palace stackup reader 1.11.0, schemaVersion "3.2"): where volumes overlap,
# the higher Priority keeps the overlap. Checks the stackup_writer validation and schema version, the Priority
# columns of the Dielectric Stack and Layers tabs (gray for a sheet layer), and the preview tooltips.
#
# Run from the repo root:   python -m pytest tests

import os
import sys
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from setupEM import stackup_writer  # noqa: E402
from setupEM import stackupEditor  # noqa: E402

STACKUP = '''<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="{schema}">
  <Materials>
    <Material Name="Mold" Type="Dielectric" Permittivity="3.8" />
    <Material Name="Si" Type="Semiconductor" Permittivity="11.9" Conductivity="2" />
    <Material Name="Cu" Type="Conductor" Conductivity="5.8e7" />
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Mold" Material="Mold" Zmin="0" Zmax="50" {mold} />
      <Dielectric Name="Die" Material="Si" Zmin="0" Zmax="20" Boundary="302" />
    </Dielectrics>
    <Layers>
      <Layer Name="M1" Type="conductor" Zmin="20" Zmax="21" Material="Cu" Layer="10" {m1} />
      <Layer Name="GND" Type="sheet" Zmin="0" Zmax="0" Material="PEC" Layer="13" />
    </Layers>
  </ELayers>
</Stackup>'''


def root_of(mold="", m1="", schema="2.0"):
    return ET.fromstring(STACKUP.format(mold=mold, m1=m1, schema=schema))


def test_priority_needs_schema_3_2():
    assert stackup_writer.required_schema_version(root_of()) == "2.0"
    assert stackup_writer.required_schema_version(root_of(mold='Priority="-1"')) == "3.2"
    assert stackup_writer.required_schema_version(root_of(m1='Priority="2"')) == "3.2"


@pytest.mark.parametrize("mold, m1, ok", [
    ('Priority="-1"', 'Priority="3"', True),
    ('Priority="=p"', '', False),            # undefined variable
    ('Priority="0.5"', '', False),
    ('', 'Priority="high"', False),
])
def test_priority_validation(mold, m1, ok):
    errors = stackup_writer.validate_stackup(root_of(mold=mold, m1=m1))
    assert (not any("Priority" in e for e in errors)) == ok, errors


def test_priority_columns(tmp_path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "stackup.xml"
    path.write_text(STACKUP.format(mold='Priority="-1"', m1='', schema="3.2"), encoding="utf-8")
    window = stackupEditor.StackupEditorWindow(stackupEditor._StandaloneMainWindow(), initial_filename=str(path))
    try:
        dielectric_col = [a for a, _h, _k in stackupEditor.DIELECTRIC_COLUMNS].index("Priority")
        rows = window.dielectrics_editor.row_elements
        mold_row = next(i for i, el in enumerate(rows) if el.get("Name") == "Mold")
        assert window.dielectrics_editor.table.item(mold_row, dielectric_col).text() == "-1"
        assert "higher Priority" in window.dielectrics_editor.table.horizontalHeaderItem(dielectric_col).toolTip()

        layer_col = [a for a, _h, _k in stackupEditor.LAYER_COLUMNS].index("Priority")
        layers = window.layers_editor.row_elements
        gnd_row = next(i for i, el in enumerate(layers) if el.get("Name") == "GND")
        m1_row = next(i for i, el in enumerate(layers) if el.get("Name") == "M1")
        gray = stackupEditor.ElementTableEditor._GRAY
        assert window.layers_editor.table.item(gnd_row, layer_col).background().color() == gray
        assert window.layers_editor.table.item(m1_row, layer_col).background().color() != gray
    finally:
        window.close()


def test_preview_tooltips_show_priority():
    from gds2palace import stackup_reader
    from setupEM import setup_common
    _, dielectrics, metals = stackup_reader.parse_substrate(root_of(mold='Priority="-1"', m1='Priority="2"'))
    assert "Priority: -1" in setup_common._build_dielectric_tooltip(dielectrics.get_by_name("Mold"))
    assert "Priority" not in setup_common._build_dielectric_tooltip(dielectrics.get_by_name("Die"))
    assert "Priority: 2" in setup_common._build_layer_tooltip(metals.getbylayername("M1"))
