# Field viewer: what is drawn of a 3D result is its outer surface without the face pairs between two bodies.
# A result written body by body (Elmer's Discontinuous Bodies) has its own node copies per body, so each interface
# is a pair of coincident faces; drawn, they fight with a clip's cut face that lies just beside them (shards).
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import numpy as np
import pyvista as pv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM.field_viewer import _outer_surface, _visible_surface   # noqa: E402


def two_bodies():
    """Two 2x2x2-cell blocks touching at x = 2, each with its own copy of the interface nodes. The second is
    the mirror image of the first, so both triangulate the shared face the same way."""
    left = pv.ImageData(dimensions=(3, 3, 3), spacing=(1.0, 1.0, 1.0)).cast_to_unstructured_grid().triangulate()
    right = left.reflect((1, 0, 0), point=(2, 0, 0))
    left.cell_data["GeometryIds"] = np.full(left.n_cells, 1)
    right.cell_data["GeometryIds"] = np.full(right.n_cells, 2)
    mesh = pv.merge([left, right], merge_points=False)
    mesh.point_data["temperature"] = mesh.points[:, 0]
    return mesh


def interface_faces(surface):
    centers = surface.cell_centers().points
    return int(np.sum(np.isclose(centers[:, 0], 2.0)))


def test_interface_pairs_are_dropped():
    mesh = two_bodies()
    raw = _outer_surface(mesh).triangulate()
    assert interface_faces(raw) == 16                     # 8 triangles per side of the shared face
    visible = _visible_surface(mesh)
    assert interface_faces(visible) == 0
    assert visible.n_cells == raw.n_cells - 16
    assert "temperature" in visible.point_data and set(np.unique(visible.cell_data["GeometryIds"])) == {1, 2}


def test_clip_just_beside_an_interface_shows_only_the_cut_face():
    mesh = two_bodies()
    clipped = mesh.clip(normal=(1, 0, 0), origin=(2.0 + 1e-9, 0, 0))   # keeps x < 2 + 1e-9: a sliver of body 2
    visible = _visible_surface(clipped)
    near = np.abs(visible.cell_centers().points[:, 0] - 2.0) < 1e-6
    assert near.any()                                                  # the cut face is drawn
    # and nothing else lies there: the drawn faces at x ~ 2 cover the face only once
    area = visible.extract_cells(np.flatnonzero(near)).compute_cell_sizes()["Area"].sum()
    assert np.isclose(area, 4.0)


def test_single_body_and_slices_are_unchanged():
    block = pv.ImageData(dimensions=(3, 3, 3)).cast_to_unstructured_grid().triangulate()
    assert _visible_surface(block).n_cells == _outer_surface(block).triangulate().n_cells
    plane = block.slice(normal=(0, 0, 1))
    assert _visible_surface(plane) is plane
