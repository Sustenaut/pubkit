"""How a walk reaches the cluster and the server: kubectl, and a command on the server.

    admin    --server HOST, or SUSTENAUT_SERVER, and no --kubectl: kubectl is
             `ssh root@HOST k3s kubectl`, and a command on the server `ssh
             root@HOST`. Everything a fulfil does it may do. Every command
             goes over one SSH connection the walk keeps (ssh's ControlMaster,
             its socket in a directory only this user may enter), opened
             before the first check: a new connection over the tailnet takes
             about a second, longer than some checks' budgets.
    deploy   --kubectl naming the server's read-only verb, `ssh ... deploy@HOST
             UNIT kubectl-read` ({unit} in it is each unit's namespace): reads
             only, so every write fulfil is unknown, and nothing runs on the server.
    plain    --kubectl CMD, or kubectl: what the kubeconfig may do. Nothing runs
             on the server.
    plane    PUBKIT_PLANE set: in-cluster kubectl. Nothing runs on the server yet.

A command's arguments reach the server as one quoted line, as ssh sends
them, and its standard input carries what is secret: a Secret manifest, a
script. Neither side writes it to a file, and no argv holds a value.

The deploy channel speaks the verb's protocol: each call is a line, a JSON
array of kubectl's arguments, and each answer a line, {"code", "stdout",
"stderr"}, over one SSH connection the walk keeps. The verb takes `get`,
`logs --tail=N`, `rollout status` and `secret-keys NAME`, in the unit's own
namespace, so the client moves a leading `-n NS` out of the arguments.
"""

from __future__ import annotations

import atexit
import json
import shlex
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .model import Kubectl, Ran, Server, no_server

TIMEOUT = 30
VERB = "kubectl-read"


def run(argv: list[str], input: bytes | None = None, timeout: float = TIMEOUT) -> Ran:  # noqa: A002
    """A command, `input` on its standard input; its output as text."""
    try:
        done = subprocess.run(argv, input=input if input is not None else b"", capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Ran(127, "", f"{argv[0] if argv else 'a command'} did not run ({type(e).__name__})")
    return Ran(done.returncode, done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace"))


def plain(command: str) -> Kubectl:
    """kubectl as `command` gives it, on this machine."""
    base = shlex.split(command)

    def kubectl(args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        return run([*base, *args], input)
    return kubectl


class Connection:
    """root@HOST over one SSH connection: the first command opens it, and
    each after it starts in about 0.1 s. The socket is in a directory of its
    own, made 0700, and the connection is closed when the walk ends. The
    path stays short, as a socket's must."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.dir: str | None = None

    def options(self) -> list[str]:
        if self.dir is None:
            # A socket's path has at most 104 bytes: "s" in mkdtemp's directory, even
            # under macOS's long $TMPDIR, is about 70.
            self.dir = tempfile.mkdtemp(prefix="pubkit-ssh-")
            atexit.register(self.close)
        return ["-o", "ControlMaster=auto", "-o", f"ControlPath={self.dir}/s", "-o", "ControlPersist=60"]

    def argv(self) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", *self.options(), f"root@{self.host}"]

    def open(self) -> Ran:
        """The connection, opened now, outside any check's budget."""
        return run([*self.argv(), "true"])

    def close(self) -> None:
        if self.dir is None:
            return
        run(["ssh", "-o", f"ControlPath={self.dir}/s", "-O", "exit", f"root@{self.host}"], timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir = None


def admin_server(connection: Connection) -> Server:
    """A command on the server, as root, its arguments one quoted line."""
    def server(args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        return run([*connection.argv(), shlex.join(args)], input, timeout=120)
    return server


def admin_kubectl(connection: Connection) -> Kubectl:
    on = admin_server(connection)

    def kubectl(args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        return on(["k3s", "kubectl", *args], input)
    return kubectl


class DeployChannel:
    """The server's read-only verb, over one SSH connection, a call at a time."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.Lock()

    def __call__(self, args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        if input is not None:
            return Ran(2, "", f"{VERB} reads only: it takes no input")
        if args[:1] == ["-n"]:
            args = args[2:]  # the verb runs in the unit's namespace
        if not self.lock.acquire(timeout=TIMEOUT):
            return Ran(124, "", f"{VERB}: the channel is busy with a call that was left")
        try:
            return self.call(args)
        finally:
            self.lock.release()

    def call(self, args: list[str]) -> Ran:
        try:
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE)
            assert self.process.stdin and self.process.stdout
            self.process.stdin.write((json.dumps(args) + "\n").encode())
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        except (OSError, ValueError) as e:
            return Ran(127, "", f"{VERB} did not run ({type(e).__name__})")
        if not line:
            code = self.process.wait(timeout=TIMEOUT)
            said = self.process.stderr.read().decode(errors="replace") if self.process.stderr else ""
            self.process = None
            return Ran(code or 255, "", said.strip().splitlines()[-1] if said.strip() else f"{VERB} closed")
        try:
            answer: Any = json.loads(line)
            return Ran(int(answer["code"]), str(answer.get("stdout", "")), str(answer.get("stderr", "")))
        except (ValueError, KeyError, TypeError):
            return Ran(1, "", f"{VERB}'s answer is not one line of JSON")

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


@dataclass
class Channels:
    name: str  # admin, deploy, plain or plane
    kubectl: Kubectl
    server: Server
    connection: Connection | None = None  # the admin channel's


def channels(kubectl_command: str | None, server_host: str | None, env: Mapping[str, str],
             unit: str = "") -> Channels:
    """The walk's channels, from --kubectl, --server and the environment, for a unit's namespace."""
    if env.get("PUBKIT_PLANE"):
        return Channels("plane", plain("kubectl"), no_server)
    if kubectl_command:
        argv = shlex.split(kubectl_command.replace("{unit}", unit))
        if argv and argv[-1] == VERB:
            return Channels("deploy", DeployChannel(argv), no_server)
        return Channels("plain", plain(shlex.join(argv)), no_server)
    host = server_host or env.get("SUSTENAUT_SERVER")
    if host:
        connection = Connection(host)
        return Channels("admin", admin_kubectl(connection), admin_server(connection), connection)
    return Channels("plain", plain("kubectl"), no_server)
