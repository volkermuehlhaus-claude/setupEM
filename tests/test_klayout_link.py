# GUI test (offscreen) of the KLayout tab: setupEM starts a real KLayout with the Remote Link, runs the user's guide
# steps of klayout_em_export on its cascode example from the tab (stackup, ports picked in the layout, devices,
# EM region, proposed port) and loads the export.
# Needs, next to this repo: klayout_remote, klayout_em_export (with the klayout_em_export_tests PDK and the cascode
# layout, see its tests/testpaths.py) and gds2palace_ihp_sg13g2; and KLayout (KLAYOUT, default D:\KLayout\klayout_app.exe
# on Windows, klayout elsewhere). Skipped without them. KLayout runs with its own home, link folder and user folder
# under pytest's tmp_path, off the screen (Windows: window moved away, Linux: offscreen); setupEM's message boxes are
# answered by the test and listed.
#
# Run from the repo root:   python -m pytest tests/test_klayout_link.py -s

import os
import shutil
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
REMOTE_REPO = os.path.join(ROOT, "klayout_remote")
EM_REPO = os.path.join(ROOT, "klayout_em_export")
WINDOWS = os.name == "nt"
KLAYOUT = os.environ.get("KLAYOUT") or (r"D:\KLayout\klayout_app.exe" if WINDOWS else shutil.which("klayout") or "")
sys.path.insert(0, os.path.join(EM_REPO, "tests"))
try:
    from testpaths import STACKUP, palace_testcases, tests_data as em_tests_data
    SOURCE = palace_testcases("klayout_export", "1_St_Core_v1_forEM.gds")
except ImportError:
    STACKUP = SOURCE = ""
pytestmark = pytest.mark.skipif(
    not (KLAYOUT and os.path.isfile(KLAYOUT) and os.path.isdir(REMOTE_REPO) and os.path.isfile(SOURCE)
         and os.path.isfile(STACKUP)),
    reason="needs KLayout, klayout_remote, klayout_em_export and its test data")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

messages = []


def pump(seconds, until=lambda: False):
    end = time.time() + seconds
    app = QApplication.instance()
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.05)
    return until()


@pytest.fixture
def tab(request, monkeypatch, tmp_path):
    """The KLayout tab and a KLayout it starts. Parameter True: KLayout shows its tip dialog at start (closed by
    test_support.py after 2 s), as in a fresh KLayout installation."""
    tips = getattr(request, "param", False)
    links, home, user = tmp_path / "links", tmp_path / "klhome", tmp_path / "user"
    for folder in (links, home / "salt", user):
        folder.mkdir(parents=True)
    monkeypatch.setenv("KLAYOUT_REMOTE_DIR", str(links))
    monkeypatch.setenv("KLAYOUT_EM_EXPORT", os.path.join(EM_REPO, "python", "klayout_em_export"))
    if not tips:
        (home / "klayoutrc").write_text('<?xml version="1.0" encoding="utf-8"?>\n<config>\n <tip-window-hidden>'
                                        'editor-mode=4,editor-mode=0,hide-empty-layers=4,hide-empty-layers=0'
                                        '</tip-window-hidden>\n <full-hierarchy-new-cell>true</full-hierarchy-new-cell>'
                                        '\n</config>\n')
    if WINDOWS:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(home / "salt" / "klayout_remote"), REMOTE_REPO], check=True,
                       stdout=subprocess.DEVNULL)
    else:
        os.symlink(REMOTE_REPO, home / "salt" / "klayout_remote")

    app = QApplication.instance() or QApplication([])
    for name, answer in (("information", QMessageBox.Ok), ("warning", QMessageBox.Ok), ("critical", QMessageBox.Ok),
                         ("question", QMessageBox.Yes)):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda parent, title, text, *a, name=name, answer=answer, **k: messages.append((name, text)) or answer))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: messages.append(("box", self.text())) or QMessageBox.Ok)
    from setupEM import klayout_link
    import setupEM.klayout_remote_client as krc
    monkeypatch.setattr(krc, "FOLDER", str(links))
    monkeypatch.setattr(klayout_link, "set_preference", lambda *a: None)     # not the user's preferences
    from setupEM.setupEM import MainWindow
    window = MainWindow()
    monkeypatch.setattr(window, "_add_recent_file", lambda *a: None)        # nor the user's recent files
    tab = window.klayout_tab
    env = dict(os.environ, KLAYOUT_HOME=str(home), KLAYOUT_PATH=str(home), HOME=str(user), USERPROFILE=str(user))
    if WINDOWS:
        env.pop("QT_QPA_PLATFORM", None)        # KLayout hangs with it on Windows: test_support moves the window
    tab.start(KLAYOUT, ["-e", "-rm", os.path.join(REMOTE_REPO, "tests", "test_support.py")], env)
    process = tab._starting[0]
    yield tab
    try:
        if tab.link is not None:
            tab.link.load_package("support", os.path.join(EM_REPO, "tests", "remote_support"))
            tab.link.call("support", "exit_klayout")
    except Exception:
        pass
    try:
        process.wait(30)
    except subprocess.TimeoutExpired:
        process.kill()
    window._script_dirty = False
    window.close()
    app.processEvents()


def test_guide_steps(tab, tmp_path):
    messages.clear()
    assert pump(120, lambda: tab.link is not None), "KLayout did not connect"
    link = tab.link
    link.load_package("support", os.path.join(EM_REPO, "tests", "remote_support"))
    assert link.call("support", "load_pdk", tests_dir=em_tests_data())
    work = str(tmp_path / "cascode.gds")
    link.call("support", "make_clean", source=SOURCE, target=work, prefix="Cascode")
    tab.open_layout(work)
    link.call("support", "show_cell", prefix="Cascode")
    tab.refresh()
    assert "choose one" in tab.stackup_label.text()

    tab.run("set_stackup", path=STACKUP)
    assert os.path.basename(STACKUP) in tab.stackup_label.text()
    assert tab.devices_table.rowCount() == 5

    tab.pick_btn.setChecked(True)
    for x, y in ((-0.715, 20.63), (-0.715, -11.275)):
        link.call("support", "click_at", x=x, y=y)
        pump(5, lambda: tab.ports_table.rowCount() >= (1 if y > 0 else 2))
    tab.pick_btn.setChecked(False)
    pump(1)
    assert tab.ports_table.rowCount() == 2, [m for m in messages]

    tab.replace_devices()
    assert all(tab.devices_table.item(r, 4).text() == "replaced" for r in range(5))
    tab.run("mark_region", box=[-28.9, -11.275, 21.595, 20.63])
    tab.refresh(run_checks=True)
    kinds = [k for k, _ in tab.port_rows]
    assert kinds.count("proposed") == 1
    tab.create_proposed()
    assert [k for k, _ in tab.port_rows].count("port") == 15

    out = str(tmp_path / "cascode_em.gds")
    tab.output_edit.setText(out)
    tab.export()
    assert os.path.isfile(out[:-4] + ".simcfg")
    # the export is loaded as File > Load Config would
    assert tab.MainWindow.saved_values.get("GdsFile", "").replace("\\", "/") == out.replace("\\", "/")
    table = tab.MainWindow.ports_tab.portslist
    layers = [table.item(r, 2).text() for r in range(table.rowCount()) if table.item(r, 2) and table.item(r, 2).text()]
    assert layers == [str(201 + i) for i in range(15)], layers       # the guide's 15 ports, numbered without gaps
    assert not [m for m in messages if m[0] in ("critical", "warning")], messages
    print("messages:", messages)


@pytest.mark.parametrize("tab", [True], indirect=True)
def test_connects_after_start_dialog(tab, tmp_path):
    """A dialog at KLayout's start makes every request busy: the tab waits and connects once it is closed."""
    messages.clear()
    assert pump(120, lambda: tab.link is not None), "KLayout did not connect"
    closed = tmp_path / "links" / "closed_dialogs.txt"
    assert closed.exists() and "Tip" in closed.read_text(), "no start dialog: the test checks nothing"
    assert messages == [], messages
