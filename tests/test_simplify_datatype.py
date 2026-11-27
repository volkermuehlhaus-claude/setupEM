"""Simplify GDS with a stackup layer that has a Datatype (stackup format 3.2): the layer list of the dialog keeps the
datatype, and "Exclude layers" takes a layer number or layer/datatype.

    python -m pytest tests/test_simplify_datatype.py
"""
import pytest

from setupEM import simplify_gds


def test_exclude_list_takes_numbers_and_layer_datatype():
    assert simplify_gds._parse_layer_list("") == set()
    assert simplify_gds._parse_layer_list("10, 11") == {10, 11}
    assert simplify_gds._parse_layer_list("68/20, 134") == {(68, 20), 134}
    with pytest.raises(ValueError):
        simplify_gds._parse_layer_list("68/x")


def test_excluded_by_number_or_by_its_datatype():
    excluded = simplify_gds._parse_layer_list("68/20, 134")
    assert simplify_gds._is_excluded((68, 20), excluded)          # a stackup layer with Datatype 20
    assert not simplify_gds._is_excluded((68, 44), excluded)      # the via on the same layer number
    assert simplify_gds._is_excluded(134, excluded)
    assert not simplify_gds._is_excluded(126, excluded)
    assert simplify_gds._is_excluded((68, 44), {68})              # the plain number excludes all its datatypes
