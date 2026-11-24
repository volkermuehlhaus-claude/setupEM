# Field viewer arrow length modes: Fixed, Scaled (legend scale and range), Log 2/3/4 decades below the largest
# value (weaker arrows hidden). arrow_lengths() is Qt-free; the window test runs off-screen on a small synthetic
# vector field.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import field_viewer                                    # noqa: E402
from setupEM.field_viewer import ARROW_LENGTH_MODES, arrow_lengths  # noqa: E402

MIN = field_viewer._VECTOR_ARROW_MIN_LENGTH_RATIO
MAGNITUDES = np.array([0.0, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0])   # peak 10


def test_modes_offered():
    assert [key for key, _ in ARROW_LENGTH_MODES] == ["fixed", "scaled", "log2", "log3", "log4"]


def test_fixed_draws_every_nonzero_point_at_full_length():
    keep, lengths = arrow_lengths(MAGNITUDES, "fixed", 2.0)
    assert keep.tolist() == [False] + [True] * 6          # a zero vector has no direction
    assert np.allclose(lengths, 2.0)


@pytest.mark.parametrize("mode, decades", [("log2", 2), ("log3", 3), ("log4", 4)])
def test_log_modes_span_decades_below_the_peak(mode, decades):
    keep, lengths = arrow_lengths(MAGNITUDES, mode, 1.0)
    assert keep.tolist() == (MAGNITUDES >= 10.0 * 10.0 ** -decades).tolist()   # weaker ones hidden
    kept = MAGNITUDES[keep]
    assert lengths[kept.argmax()] == pytest.approx(1.0)                       # peak: full length
    assert lengths[kept.argmin()] == pytest.approx(MIN)                       # bottom of the span: shortest
    expected = MIN + (1 - MIN) * (1 + np.log10(kept / 10.0) / decades)        # linear in log10
    assert np.allclose(lengths, expected)


def test_scaled_follows_the_legend_range():
    keep, lengths = arrow_lengths(MAGNITUDES, "scaled", 1.0, clim=(1e-2, 1.0), use_log=True)
    assert keep.tolist() == (MAGNITUDES >= 1e-2).tolist()                      # below the range hidden
    assert np.allclose(lengths, MIN + (1 - MIN) * np.array([0.0, 0.5, 1.0, 1.0]))   # above Max: full length
    keep, lengths = arrow_lengths(MAGNITUDES, "scaled", 1.0, clim=(0.0, 10.0))
    assert keep.all() and lengths[-1] == pytest.approx(1.0) and lengths[0] == pytest.approx(MIN)


def test_nothing_to_draw():
    assert arrow_lengths(np.zeros(4), "fixed", 1.0) == (None, None)
    assert arrow_lengths(np.zeros(4), "log2", 1.0) == (None, None)
    assert arrow_lengths(MAGNITUDES, "scaled", 1.0, clim=(20.0, 30.0)) == (None, None)
    assert arrow_lengths(np.array([]), "log2", 1.0) == (None, None)
    with pytest.raises(ValueError):
        arrow_lengths(MAGNITUDES, "linear", 1.0)


# ---------------------------------------------------------------- command line

@pytest.mark.parametrize("extra", [["--arrow-length", "bogus"],
                                    ["--arrows", "--arrows-scaled", "--arrow-length", "log2"]])
def test_command_line_errors_before_qt(monkeypatch, capsys, tmp_path, extra):
    field = tmp_path / "fields.vtu"
    field.write_text("")
    monkeypatch.setattr(field_viewer, "QApplication", lambda *a: pytest.fail("Qt started"))
    monkeypatch.setattr(sys, "argv", ["fieldViewer", str(field), *extra])
    with pytest.raises(SystemExit) as exit_info:
        field_viewer.main()
    assert exit_info.value.code == 2
    assert "--arrow" in capsys.readouterr().err


# ---------------------------------------------------------------- the window, off-screen

@pytest.fixture
def window(tmp_path):
    import pyvista as pv
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    grid = pv.ImageData(dimensions=(6, 6, 6), spacing=(1.0, 1.0, 1.0)).cast_to_unstructured_grid()
    x = grid.points[:, 0]
    strength = 10.0 ** (x - 5.0)                         # 1e-5 .. 1 along x: five decades
    grid.point_data["E_real"] = np.column_stack([strength, np.zeros_like(x), np.zeros_like(x)])
    path = str(tmp_path / "field.vtu")
    grid.save(path)
    win = field_viewer.FieldViewerWindow(field_viewer._StandaloneMainWindow(), [path], field_viewer._PALACE,
                                         off_screen=True)
    win.array_combo.setCurrentText("E_real")
    app.processEvents()
    yield win
    win.close()
    win.deleteLater()
    app.processEvents()


def test_window_draws_every_mode(window):
    combo = window.arrow_length_combo
    assert combo.currentData() == "fixed" and not combo.isEnabled()
    window.show_vectors_cb.setChecked(True)
    assert combo.isEnabled()
    mesh = window._full_mesh
    for index in range(combo.count()):
        combo.setCurrentIndex(index)
        actor = window._add_vector_glyphs(mesh, "E_real")
        assert actor is not None, combo.currentData()
        window.plotter.remove_actor(actor, render=False)
    window.show_vectors_cb.setChecked(False)
    assert not combo.isEnabled() and combo.currentData() == ARROW_LENGTH_MODES[-1][0]   # selection kept
