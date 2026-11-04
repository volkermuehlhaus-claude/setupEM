# GUI test (offscreen) of handing a config over to a running setupEM (instances.py): a second start with the
# .simcfg loaded in a window (KLayout's EM Export after a new export) makes that window reload it and ends;
# any other file is not taken; a registry entry of a setupEM that is gone is removed.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

CLIENT = ("import sys; sys.path.insert(0, sys.argv[1])\n"
          "from PySide6.QtCore import QCoreApplication\n"
          "from setupEM import instances\n"
          "app = QCoreApplication([])\n"
          "instances.REGISTRY = sys.argv[2]\n"
          "print(instances.hand_over(sys.argv[3]))\n")


def hand_over(registry, path):
    """hand_over(path) in another process, while this one serves its requests."""
    app = QApplication.instance()
    process = subprocess.Popen([sys.executable, "-c", CLIENT, SRC, str(registry), str(path)],
                               stdout=subprocess.PIPE, text=True)
    end = time.time() + 30
    while process.poll() is None and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    out = process.communicate(timeout=5)[0].strip()
    for _ in range(20):
        app.processEvents()
    return out


@pytest.fixture
def win(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    questions = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda p, t, text, *a, **k: questions.append(text)
                                                            or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda parent, title, *a, **k: questions.append(title) or QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: questions.append(self.text()) or QMessageBox.Ok)
    from setupEM import instances
    monkeypatch.setattr(instances, "REGISTRY", str(tmp_path / "registry"))
    from setupEM.setupEM import MainWindow
    window = MainWindow()
    monkeypatch.setattr(window, "_add_recent_file", lambda *a: None)
    window.questions = questions
    server = instances.InstanceServer(window)
    yield window
    server.close()
    window._script_dirty = False
    window.close()
    app.processEvents()


def test_hand_over(win, tmp_path):
    registry = tmp_path / "registry"
    simcfg = tmp_path / "chip_em.simcfg"
    saved = {"GdsFile": str(tmp_path / "chip_em.gds")}       # its own layout: no model scripts next to it
    simcfg.write_text(json.dumps(dict({"application": "setupEM", "data_format": "1.0", "saved_values": saved},
                                      **win.native_config_extra_struct())))
    win.load_configuration_from_file(str(simcfg))
    assert len(os.listdir(registry)) == 1 and getattr(win, "user_inputs_file", None) == str(simcfg), win.questions

    win.questions.clear()
    win.saved_values["changed_here"] = 1
    loads = []
    real = win.load_configuration_from_file
    win.load_configuration_from_file = lambda path, **k: loads.append(path) or real(path, **k)
    assert hand_over(registry, simcfg) == "True"
    assert win.questions == ["Reload"] and loads == [os.path.abspath(simcfg)]
    assert "changed_here" not in win.saved_values                       # the file's settings again
    assert os.path.normpath(win.saved_values["GdsFile"]) == os.path.normpath(saved["GdsFile"])

    assert hand_over(registry, tmp_path / "other.simcfg") == "False"
    assert win.questions == ["Reload"]

    (registry / "setupEM_gone_1").write_text("")
    assert hand_over(registry, tmp_path / "other.simcfg") == "False"
    assert not (registry / "setupEM_gone_1").exists()
