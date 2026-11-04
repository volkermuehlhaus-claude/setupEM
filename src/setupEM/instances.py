########################################################################
#
# Copyright 2025-2026 Volker Muehlhaus and IHP PDK Authors
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

"""
instances.py

Hands a config file over to a setupEM that already has it open. KLayout's EM
Export plug-in starts "setupEM -simcfg <file>" after each export; for the
second export of the same model that would open a second window. Instead, the
new process asks the running setupEM windows first: the one whose loaded
config is that file reloads it and comes to the front, and the new process
ends. Any other file opens in a new window as before.

Every window listens on a local socket (QLocalServer, per user and process)
and lists itself as a file in REGISTRY. Request: one JSON line
{"open": path}; answer: one JSON line {"taken": true/false}.
"""

import os, json, getpass, tempfile
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

REGISTRY = os.path.join(tempfile.gettempdir(), f"setupEM_instances_{getpass.getuser()}")
TIMEOUT_MS = 2000


def same_file(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def hand_over(path):
    """Ask the running setupEM windows to take path; True if one did."""
    if not path or not os.path.isdir(REGISTRY):
        return False
    for name in sorted(os.listdir(REGISTRY)):
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(TIMEOUT_MS):
            try:
                os.remove(os.path.join(REGISTRY, name))     # left by a setupEM that ended without cleaning up
            except OSError:
                pass
            continue
        socket.write((json.dumps({"open": os.path.abspath(path)}) + "\n").encode())
        socket.waitForBytesWritten(TIMEOUT_MS)
        answer = b""
        while not answer.endswith(b"\n") and socket.waitForReadyRead(TIMEOUT_MS):
            answer += bytes(socket.readAll())
        socket.disconnectFromServer()
        try:
            if json.loads(answer.decode() or "{}").get("taken"):
                return True
        except ValueError:
            pass
    return False


class InstanceServer:
    """Listens for hand_over() requests to window; takes a file if it is window's loaded config
    (window.user_inputs_file) and calls window.reload_handed_over(path) once the answer is sent."""

    def __init__(self, window):
        self.window = window
        self.name = f"setupEM_{getpass.getuser()}_{os.getpid()}"
        self.server = QLocalServer()
        QLocalServer.removeServer(self.name)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)      # only this user's processes connect
        if not self.server.listen(self.name):
            return
        self.server.newConnection.connect(self._connection)
        try:
            os.makedirs(REGISTRY, exist_ok=True)
            open(os.path.join(REGISTRY, self.name), "w").close()
        except OSError:
            pass

    def close(self):
        self.server.close()
        try:
            os.remove(os.path.join(REGISTRY, self.name))
        except OSError:
            pass

    def _connection(self):
        socket = self.server.nextPendingConnection()
        buffer = []

        def read():
            buffer.append(bytes(socket.readAll()))
            data = b"".join(buffer)
            if not data.endswith(b"\n"):
                return
            try:
                path = json.loads(data.decode()).get("open", "")
            except ValueError:
                path = ""
            taken = same_file(path, getattr(self.window, "user_inputs_file", None))
            socket.write((json.dumps({"taken": taken}) + "\n").encode())
            socket.flush()
            if taken:
                # after the answer: the reload may ask the user, the asking process must not wait for that
                QTimer.singleShot(0, lambda: self.window.reload_handed_over(path))

        socket.readyRead.connect(read)
        socket.disconnected.connect(socket.deleteLater)
