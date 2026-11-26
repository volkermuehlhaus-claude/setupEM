# setupThermal's stackup preview labels: a material with a temperature-dependent thermal table shows "κ=table"
# whether it is a dielectric, a metal (e.g. Activ, a conductor with the silicon table) or a via, not its scalar
# thermalcond (0.0 when only the table is given).
#
# Run from the repo root:   python -m pytest tests

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM.setupThermal import MainWindow  # noqa: E402


def material(thermalcond, table=""):
    return SimpleNamespace(thermalcond=thermalcond, thermaltablename=table)


def test_table_material_labelled_table_everywhere():
    activ = material(0.0, "SiliconThermalCond")
    assert MainWindow.stackup_dielectric_label(None, None, activ) == "κ=table"
    assert MainWindow.stackup_metal_label(None, None, activ, False) == "κ=table"
    assert MainWindow.stackup_via_label_suffix(None, None, activ).strip() == "κ=table"


def test_scalar_material_labelled_with_value():
    metal = material(237.0)
    assert MainWindow.stackup_dielectric_label(None, None, metal) == "κ=237.0"
    assert MainWindow.stackup_metal_label(None, None, metal, False) == "κ=237.0"
    assert MainWindow.stackup_metal_label(None, None, metal, True) == ""
    assert MainWindow.stackup_via_label_suffix(None, None, None).strip() == ""


def test_directional_material_labelled_and_colored_by_direction():
    # ThermalConductivity.xy / .z (gds2palace stackup reader 1.11.0): both values in the label, colored by z
    import xml.etree.ElementTree as ET
    from gds2palace import stackup_reader
    from setupEM.setupThermal import _thermal_conductivity_for_color
    mold = stackup_reader.stackup_material(ET.fromstring(
        '<Material Name="Mold" Type="Dielectric" ThermalConductivity="0.9" ThermalConductivity.z="0.3" />'))
    assert MainWindow.stackup_dielectric_label(None, None, mold) == "κ xy=0.9, z=0.3"
    assert _thermal_conductivity_for_color(mold) == 0.3
    plain = stackup_reader.stackup_material(ET.fromstring(
        '<Material Name="SiO2" Type="Dielectric" ThermalConductivity="1.4" ThermalConductivity.z="1.4" />'))
    assert MainWindow.stackup_dielectric_label(None, None, plain) == "κ=1.4"
