# Tests for src/setupEM/run_with_overrides.py with a stand-in workflow module.
#
# The fake util_simulation_setup.py records the settings create_model() gets,
# so the test checks that overrides arrive there while the script itself and
# its own values stay untouched.

import json
import os
import subprocess
import sys
import textwrap

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "setupEM", "run_with_overrides.py")

FAKE_WORKFLOW = textwrap.dedent("""
    import json, os
    def create_model(excite_ports, settings):
        with open(os.path.join(os.path.dirname(__file__), "received.json"), "w") as f:
            json.dump({k: v for k, v in settings.items() if k != "out"}, f, default=str)
        return "config", "data"
    def create_palace(excite_ports, settings):
        settings["palace"] = True
        return create_model(excite_ports, settings)
""")

SCRIPT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "localflow"))
    import util_simulation_setup as simulation_setup
    assert __name__ == "__main__"
    settings = {}
    settings['fstop'] = 10e9
    settings['preview_only'] = False
    simulation_setup.create_palace([], settings)
""")


def run(tmp_path, *overrides, source=None):
    flow = tmp_path / "localflow"
    flow.mkdir()
    (flow / "util_simulation_setup.py").write_text(FAKE_WORKFLOW)
    script = tmp_path / "model.py"
    script.write_text(SCRIPT)
    before = script.read_bytes()
    args = [sys.executable, RUNNER, str(script)]
    if source is not None:
        args += ["--source", str(source)]
    for o in overrides:
        args += ["--set", o]
    result = subprocess.run(args, capture_output=True, text=True, cwd=str(tmp_path.parent))
    assert result.returncode == 0, result.stderr
    assert script.read_bytes() == before
    return json.loads((flow / "received.json").read_text())


def test_overrides_reach_create_model_through_create_palace(tmp_path):
    received = run(tmp_path, "preview_only=True", "no_preview=False")
    assert received == {"fstop": 10e9, "preview_only": True, "no_preview": False, "palace": True}


def test_without_overrides_script_values_are_kept(tmp_path):
    received = run(tmp_path)
    assert received["preview_only"] is False


SWEEP_SCRIPT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "localflow"))
    import util_stackup_reader as stackup_reader
    import util_simulation_setup as simulation_setup
    for T in [25, 85]:
        stackup_reader.read_substrate("x.xml", variable_overrides={'Temp_Celsius': T})
        settings = {'fstop': 10e9, 'sim_path': os.path.join(os.path.dirname(__file__), f"m_T{T}_data"),
                    'model_basename': f"m_T{T}", 'materials_list': object()}
        simulation_setup.create_palace([], settings)
        print("built", T)
""")


def run_sweep(tmp_path, *extra):
    flow = tmp_path / "localflow"
    flow.mkdir()
    (flow / "util_simulation_setup.py").write_text(FAKE_WORKFLOW)
    (flow / "util_stackup_reader.py").write_text("def read_substrate(xml, variable_overrides=None):\n    return 1, 2, 3\n")
    script = tmp_path / "sweep.py"
    script.write_text(SWEEP_SCRIPT)
    return subprocess.run([sys.executable, RUNNER, str(script), *extra], capture_output=True, text=True)


def test_record_lists_every_model_of_a_sweep(tmp_path):
    record = tmp_path / "out" / "sweep_models.json"
    result = run_sweep(tmp_path, "--record", str(record), "--set", "no_preview=True")
    assert result.returncode == 0, result.stderr
    models = json.loads(record.read_text())
    assert [m["model_basename"] for m in models] == ["m_T25", "m_T85"]
    assert models[1]["sim_path"].endswith("/m_T85_data") and models[0]["solver"] == "palace"
    assert models[1]["variable_overrides"] == {"Temp_Celsius": 85}
    assert models[0]["settings"] == {"fstop": 10e9, "palace": True}   # objects and run flags left out


def test_first_only_stops_before_the_second_model(tmp_path):
    result = run_sweep(tmp_path, "--first-only")
    assert result.returncode == 0, result.stderr
    assert "built 25" in result.stdout and "built 85" not in result.stdout
    assert "only the first is shown" in result.stdout


def test_source_runs_other_code_as_the_script(tmp_path):
    # unsaved changes: the code comes from another file, but __file__, the
    # folder on sys.path and the output names stay those of the script
    unsaved = tmp_path.parent / f"{tmp_path.name}_unsaved.py"
    unsaved.write_text(SCRIPT.replace("10e9", "20e9") +
                       "settings['file'] = os.path.basename(__file__)\n"
                       "simulation_setup.create_palace([], settings)\n")
    received = run(tmp_path, "preview_only=True", source=unsaved)
    assert received["fstop"] == 20e9 and received["file"] == "model.py" and received["preview_only"] is True
