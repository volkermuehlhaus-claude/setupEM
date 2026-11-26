"""Saving a stackup with the Stackup Editor's writer: the comment that names the format 3.2 features a file uses,
comments in <Layers> that stay with their layer when the layers are sorted, and the newline at the end of the file.

    python -m pytest tests/test_stackup_save.py
"""
import xml.etree.ElementTree as ET

from setupEM import stackup_writer

READER = "gds2palace"

STACKUP = '''<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="{schema}">
  {stamp}
  <Variables>
    <Variable Name="total_thickness" Value="200" {limits} />
  </Variables>
  <Materials>
    <Material Name="Ox" Type="Dielectric" Permittivity="4" {material} />
    <Material Name="Cu" Type="Conductor" Conductivity="5.8e7" />
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Ox" Material="Ox" Zmin="0" Zmax="=total_thickness" {dielectric} />
    </Dielectrics>
    <Layers>
      <!-- at the top of the section -->
      <Substrate Offset="0" />
      <!-- the lowest metal -->
      <Layer Name="M1" Type="conductor" Material="Cu" Layer="8" Zmin="1" Zmax="2" {layer} />
      <!-- the via, first line -->
      <!-- the via, second line -->
      <Layer Name="V1" Type="via" Material="Cu" Layer="19" Zmin="2" Zmax="3" />
      <Layer Name="M2" Type="conductor" Material="Cu" Layer="10" Zmin="3" Zmax="4" />
      <!-- after the last layer -->
    </Layers>
  </ELayers>
</Stackup>'''

OLD_STAMP = "<!-- " + stackup_writer.DIRECTIONAL_FORMAT_COMMENT_PREFIX + " 1.11.0 or newer -->"


def saved(tmp_path, schema="3.1", stamp="", limits="", material="", dielectric="", layer=""):
    root = ET.fromstring(STACKUP.format(schema=schema, stamp=stamp, limits=limits, material=material,
                                        dielectric=dielectric, layer=layer),
                         parser=stackup_writer._comment_preserving_parser())
    path = str(tmp_path / "out.xml")
    stackup_writer.save_stackup_tree(ET.ElementTree(root), path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def stamps(text):
    return [line.strip() for line in text.splitlines() if "util_stackup_reader.py version" in line]


def test_stamp_names_only_the_features_used(tmp_path):
    text = saved(tmp_path, limits='Min="25"')
    assert 'schemaVersion="3.2"' in text
    assert stamps(text) == [f"<!-- Stackup format 3.2 features used (allowed values of Variables) require {READER} "
                            "util_stackup_reader.py version 1.11.0 or newer -->"]


def test_stamp_lists_all_features_used(tmp_path):
    text = saved(tmp_path, limits='Description="chip thickness"', material='ThermalConductivity.z="1"',
                 dielectric='Priority="-1"', layer='Datatype="20"')
    assert stamps(text) == ["<!-- Stackup format 3.2 features used (thermal conductivity per direction, Priority, "
                            f"allowed values of Variables, Datatype) require {READER} util_stackup_reader.py version "
                            "1.11.0 or newer -->"]


def test_no_stamp_without_a_3_2_feature(tmp_path):
    text = saved(tmp_path)
    assert 'schemaVersion="3.1"' in text and stamps(text) == []


def test_stamp_in_the_earlier_wording_is_replaced(tmp_path):
    text = saved(tmp_path, schema="3.2", stamp=OLD_STAMP, limits='Min="25"')
    assert len(stamps(text)) == 1 and "(allowed values of Variables)" in stamps(text)[0]
    assert "Direction-dependent" not in text


def test_stamp_follows_a_later_change(tmp_path):
    # the file is 3.2 already and has a stamp: a feature added later appears in it
    first = saved(tmp_path, limits='Min="25"')
    root = ET.fromstring(first, parser=stackup_writer._comment_preserving_parser())
    next(el for el in root.iter("Dielectric")).set("Priority", "1")
    path = str(tmp_path / "second.xml")
    stackup_writer.save_stackup_tree(ET.ElementTree(root), path)
    with open(path, encoding="utf-8") as f:
        second = f.read()
    assert len(stamps(second)) == 1 and "(Priority, allowed values of Variables)" in stamps(second)[0]


def test_3_2_file_without_stamp_gets_none(tmp_path):
    # written by hand as 3.2: saving does not add a stamp
    assert stamps(saved(tmp_path, schema="3.2", limits='Min="25"')) == []


def test_comments_stay_with_their_layer(tmp_path):
    text = saved(tmp_path)
    lines = [line.strip() for line in text.splitlines()]
    section = lines[lines.index("<Layers>") + 1:lines.index("</Layers>")]
    kinds = [line if line.startswith("<!--") else line.split('"')[1] if "Name=" in line else "Substrate"
             for line in section]
    # sorted with the highest layer first; each comment still in front of its layer
    assert kinds == ["<!-- at the top of the section -->", "Substrate", "M2",
                     "<!-- the via, first line -->", "<!-- the via, second line -->", "V1",
                     "<!-- the lowest metal -->", "M1", "<!-- after the last layer -->"]


def test_file_ends_with_a_newline(tmp_path):
    text = saved(tmp_path)
    assert text.endswith("</Stackup>\n")


def test_saving_twice_changes_nothing(tmp_path):
    first = saved(tmp_path, limits='Min="25"', dielectric='Priority="-1"')
    root = ET.fromstring(first, parser=stackup_writer._comment_preserving_parser())
    path = str(tmp_path / "again.xml")
    stackup_writer.save_stackup_tree(ET.ElementTree(root), path)
    with open(path, encoding="utf-8") as f:
        assert f.read() == first
