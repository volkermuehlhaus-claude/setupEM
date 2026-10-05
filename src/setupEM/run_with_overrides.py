########################################################################
#
# Copyright 2025 Volker Muehlhaus and IHP PDK Authors
#
# Licensed under the GNU General Public License, Version 3.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.gnu.org/licenses/gpl-3.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################

"""Run a gds2palace model script with some settings[] values overridden.

    python run_with_overrides.py model.py --set preview_only=True --set no_preview=False

Used by setupEM's preserve mode, so Preview / Create Mesh don't have to write
their control flags into the user's script. The script runs unchanged, as
__main__ with its own __file__ and its own folder first on sys.path, exactly
like "python model.py". The overrides are applied to the settings dict when
the script calls the workflow's create_model() (also through create_palace(),
create_elmer() or create_elmer_thermal()). That function is patched when the
workflow module is imported, from wherever the script imports it (installed
package or a local copy put on sys.path by the script).
"""

import argparse
import ast
import importlib.abc
import importlib.machinery
import os
import runpy
import sys

WORKFLOW_MODULE_SUFFIX = "util_simulation_setup"


def _parse_value(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _patch(module, overrides):
    original = getattr(module, "create_model", None)
    if original is None or getattr(original, "_setupEM_overrides", False):
        return

    def create_model(*args, **kwargs):
        settings = kwargs.get("settings")
        if settings is None:
            settings = next((a for a in reversed(args) if isinstance(a, dict)), None)
        if settings is not None:
            settings.update(overrides)
        return original(*args, **kwargs)

    create_model._setupEM_overrides = True
    module.create_model = create_model


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, loader, overrides):
        self.loader = loader
        self.overrides = overrides

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        _patch(module, self.overrides)


class _PatchingFinder(importlib.abc.MetaPathFinder):
    def __init__(self, overrides):
        self.overrides = overrides

    def find_spec(self, fullname, path, target=None):
        if not fullname.split(".")[-1] == WORKFLOW_MODULE_SUFFIX:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                    spec.loader = _PatchingLoader(spec.loader, self.overrides)
                return spec
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", help="model script to run")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="settings[KEY] = VALUE (a Python literal) when the model is created")
    args = parser.parse_args(argv)

    overrides = {}
    for item in args.set:
        key, sep, value = item.partition("=")
        if not sep or not key:
            parser.error(f"--set expects KEY=VALUE, got {item!r}")
        overrides[key.strip()] = _parse_value(value.strip())

    script = os.path.abspath(args.script)
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").split(".")[-1] == WORKFLOW_MODULE_SUFFIX:
            _patch(module, overrides)
    sys.meta_path.insert(0, _PatchingFinder(overrides))

    # same environment as "python model.py"
    sys.argv = [script]
    sys.path[0] = os.path.dirname(script)
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
