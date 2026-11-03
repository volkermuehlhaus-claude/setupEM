"""Client for klayout_remote (standard library only, so setupEM or any Python can copy it).

    link = connect()                         # the newest running KLayout with the link on, or start_klayout()
    link.load_package("mytools", "path/to/mytools")
    link.call("mytools", "add_port", undo="Add port", x=1.0)
    link.wait_event("pick", timeout=60)
"""
import json
import os
import socket
import subprocess
import time

FOLDER = os.environ.get("KLAYOUT_REMOTE_DIR") or os.path.join(os.path.expanduser("~"), ".klayout_remote")
PROTOCOL = 1


class RemoteError(Exception):
    def __init__(self, error):
        super().__init__(error.get("message", ""))
        self.type = error.get("type")
        self.traceback = error.get("traceback", "")


class BusyError(RemoteError):
    """KLayout shows a dialog; try again when the user has closed it."""


class LinkClosed(Exception):
    """The connection ended: KLayout was closed, the link switched off, or KLayout crashed."""


def instances():
    """Instance files of KLayouts with the link on, newest first (files of ended KLayouts are skipped)."""
    folder = os.path.join(FOLDER, "instances")
    found = []
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        try:
            with open(os.path.join(folder, name)) as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        info["mtime"] = os.path.getmtime(os.path.join(folder, name))
        found.append(info)
    return sorted(found, key=lambda i: -i["mtime"])


def connect(pid=None, timeout=10):
    """A Link to the KLayout with this pid, or to the newest one that answers."""
    for info in instances():
        if pid is not None and info["pid"] != pid:
            continue
        try:
            return Link(info, timeout)
        except (OSError, LinkClosed):
            continue
    raise LinkClosed("no KLayout with the Remote Link on" + (f" (pid {pid})" if pid else ""))


def start_klayout(exe="klayout", args=("-e",), timeout=120, env=None):
    """Starts KLayout with the link on (KLAYOUT_REMOTE=1) and returns (process, Link) once it answers."""
    env = dict(os.environ if env is None else env, KLAYOUT_REMOTE="1")
    process = subprocess.Popen([exe, *args], env=env)
    end = time.time() + timeout
    while time.time() < end:
        if process.poll() is not None:
            raise LinkClosed(f"KLayout ended at start (exit code {process.returncode})")
        try:
            return process, connect(process.pid, timeout=10)
        except LinkClosed:
            time.sleep(0.3)
    process.kill()
    raise LinkClosed(f"KLayout did not start the Remote Link within {timeout} s")


class Link:
    """request_timeout: how long a request may take in KLayout (an export of a large layout takes minutes)."""

    def __init__(self, info, timeout=10, request_timeout=600):
        self.info = info
        self.request_timeout = request_timeout
        self.sock = socket.create_connection(("127.0.0.1", info["port"]), timeout=timeout)
        self.sock.settimeout(request_timeout)
        self.buffer = b""
        self.events = []
        self._id = 0
        self.hello = self.request("hello", token=info["token"])
        if self.hello["protocol"] != PROTOCOL:
            raise LinkClosed(f"KLayout speaks protocol {self.hello['protocol']}, this client {PROTOCOL}")

    def close(self):
        self.sock.close()

    def _line(self):
        while b"\n" not in self.buffer:       # own buffer: a timeout in recv loses nothing
            try:
                data = self.sock.recv(1 << 16)
            except socket.timeout:
                raise
            except OSError as e:
                raise LinkClosed(str(e))
            if not data:
                raise LinkClosed("KLayout closed the connection")
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def request(self, op, **fields):
        self._id += 1
        try:
            self.sock.sendall((json.dumps({"id": self._id, "op": op, **fields}) + "\n").encode())
        except OSError as e:
            raise LinkClosed(str(e))
        while True:
            msg = self._line()
            if "event" in msg:
                self.events.append(msg)
            elif msg.get("id") == self._id:
                if "error" in msg:
                    raise (BusyError if msg["error"].get("type") == "busy" else RemoteError)(msg["error"])
                return msg["result"]

    def load_package(self, name, folder):
        """Sends the package in folder (all .py files below it) as importable `name`."""
        files = {}
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if n.endswith(".py"):
                    path = os.path.join(root, n)
                    with open(path, encoding="utf-8") as f:
                        files[os.path.join(name, os.path.relpath(path, folder)).replace(os.sep, "/")] = f.read()
        return self.request("load_module", name=name, files=files)

    def call(self, module, function, undo=None, **args):
        """module.function(view, **args) in KLayout; undo="<text>" makes a change one undo step."""
        fields = {"module": module, "function": function, "args": args}
        if undo:
            fields["undo"] = undo
        return self.request("call", **fields)

    def take_events(self, name=None):
        taken = [e for e in self.events if name is None or e["event"] == name]
        self.events = [e for e in self.events if e not in taken]
        return taken

    def wait_event(self, name, timeout=30):
        """The next event `name` (events that arrived earlier count too), or None after timeout seconds."""
        end = time.time() + timeout
        while True:
            taken = self.take_events(name)
            if taken:
                self.events = taken[1:] + self.events
                return taken[0]
            left = end - time.time()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                msg = self._line()
            except socket.timeout:
                return None
            finally:
                self.sock.settimeout(self.request_timeout)
            if "event" in msg:
                self.events.append(msg)

    def poll(self):
        """The events that arrived until now, without waiting (for a GUI timer). Raises LinkClosed."""
        self.sock.setblocking(False)
        try:
            while True:
                try:
                    data = self.sock.recv(1 << 16)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError as e:
                    raise LinkClosed(str(e))
                if not data:
                    raise LinkClosed("KLayout closed the connection")
                self.buffer += data
        finally:
            self.sock.settimeout(self.request_timeout)
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            msg = json.loads(line.decode("utf-8"))
            if "event" in msg:
                self.events.append(msg)
        return self.take_events()
