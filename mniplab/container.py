"""Container-backed host node support for Mininet-IPLab.

Provides :class:`ContainerHost` (a Mininet node whose shell runs inside a
Docker container), :class:`ContainerIntf` (iproute2-based interface for
Alpine/BusyBox compatibility), and :class:`ContainerLink` (host-side veth  
pair creation that reaches container network namespaces).
"""

from __future__ import annotations

import os
import select
import subprocess
import time
import pty
from typing import Any, Dict, List, Optional, Sequence

from mininet.link import Link, Intf
from mininet.log import debug, error, warn
from mininet.util import quietRun
from mniplab.constants import CONTAINER_NAME_PREFIX, NETNS_DIR, SERVICE_READY_TIMEOUT
from mniplab.host import mnIPLabHost
from mniplab.docker_client import DockerClient
from mniplab.errors import ConfigurationError

# Standard network capabilities required for network namespaces
REQUIRED_NET_CAPS = {"NET_ADMIN", "NET_RAW"}

# Default startup command to keep container alive
DEFAULT_CONTAINER_CMD = "while true; do sleep 3600; done"

# Shell configuration
SHELL_PATH = "/bin/sh"
SHELL_FLAGS = "-lc"
INTERACTIVE_SHELL_FLAGS = "-i"

# Sentinel character for shell prompt detection
SHELL_PROMPT_SENTINEL = chr(127)

# Timeout waiting for container shell prompt sentinel
SHELL_READY_TIMEOUT = SERVICE_READY_TIMEOUT


class ContainerIntf(Intf):
    """Mininet interface that uses iproute2 instead of ifconfig.

    Alpine Linux and other minimal images ship BusyBox ifconfig which rejects
    CIDR notation.  This subclass uses ``ip addr`` / ``ip link`` throughout.
    """

    def setIP(self, ipstr: str, prefixLen: Optional[int] = None) -> str:
        if "/" in ipstr:
            self.ip, self.prefixLen = ipstr.split("/")
            cidr = ipstr
        else:
            if prefixLen is None:
                raise ValueError(f"No prefix length for IP address {ipstr}")
            self.ip, self.prefixLen = ipstr, str(prefixLen)
            cidr = f"{ipstr}/{prefixLen}"
        self.cmd("ip", "addr", "flush", "dev", self.name)
        result = self.cmd("ip", "addr", "add", cidr, "dev", self.name)
        self.cmd("ip", "link", "set", self.name, "up")
        return result

    def isUp(self, setUp: bool = False) -> bool:
        if setUp:
            self.cmd("ip", "link", "set", self.name, "up")
            return True
        links, _, _ = self.node.pexec("ip link show")
        return self.name in links

    def config(self, mac: Optional[str] = None, ip: Optional[str] = None,
               up: bool = True, **_params: Any) -> dict:
        # setIP already calls ip link set up; skip a separate isUp call
        # to avoid a spurious warning from the container shell.
        r: dict = {}
        if mac:
            self.setMAC(mac)
            r["mac"] = mac
        if ip:
            r["ip"] = self.setIP(ip)
        elif up:
            self.isUp(setUp=True)
        return r


class ContainerHost(mnIPLabHost):
    """Mininet host whose process namespace is backed by a Docker container.

    The container is created before Mininet's shell setup so the PID is
    available when veth pairs are moved into the namespace.  Command
    execution uses ``docker exec`` and is fully compatible with standard
    Mininet CLI flows.

    Inherits ``private_namespace_capable = True`` from :class:`mnIPLabHost`
    unchanged: Docker already gives a container its own PID and UTS
    namespaces, so this class needs no override to report the property
    truthfully -- see :mod:`mniplab.namespace`.
    """

    display_type = "container"

    def __init__(self, name: str, dimage: str, dcmd: Optional[str] = None,
                 environment: Optional[Dict[str, str]] = None,
                 volumes: Optional[Sequence[str]] = None,
                 ports: Optional[Sequence[str]] = None,
                 port_bindings: Optional[Dict[str, str]] = None,
                 cap_add: Optional[Sequence[str]] = None,
                 pull_image: bool = True,
                 remove_existing: bool = True,
                 **params: Any) -> None:
        self.dimage = dimage
        self.dcmd = dcmd
        self.environment = environment or {}
        self.volumes = list(volumes or [])
        self.ports = list(ports or [])
        self.port_bindings = dict(port_bindings or {})
        self.cap_add = list(cap_add or ["NET_ADMIN"])
        self.pull_image = pull_image
        self.remove_existing = remove_existing
        self.net = params.get('net', None)
        # The container and its namespace link live on the host, so the Lab
        # names them with its key; without a Lab they keep the sweepable prefix.
        if self.net is not None and hasattr(self.net, 'container_name'):
            self.container_name: str = self.net.container_name(name)
            self.netns_link_path: str = self.net.netns_link_path(name)
        else:
            self.container_name = f"{CONTAINER_NAME_PREFIX}{name}"
            self.netns_link_path = os.path.join(NETNS_DIR, self.container_name)
        self._docker = DockerClient(self.container_name)

        # Convert Mininet privateDirs to Docker volume mounts before the
        # container is created so the bind mounts are visible inside it.
        private_dirs = params.pop('privateDirs', None)
        if private_dirs:
            self._convert_private_dirs_to_volumes(private_dirs, name)

        # Container must exist before Mininet references self.pid for veth creation.
        self._create_container(name)

        # inNamespace=False prevents Node.__init__ from calling mnexec -n
        # (which would create a new netns instead of joining the container's).
        params['inNamespace'] = False
        # Suppress Mininet's mountPrivateDirs: bind mounts are already handled
        # via Docker volume flags above.
        params.setdefault('privateDirs', [])
        super().__init__(name, **params)
        # Flip back so ContainerLink moves veths into the container namespace.
        self.inNamespace = True

    def _convert_private_dirs_to_volumes(self, private_dirs: List[Any], name: str) -> None:
        """Translate Mininet privateDirs into Docker volume mounts.

        Mininet privateDirs supports two forms:
        - ``('/container/path', '/host/path')`` → bind-mount host dir into container
        - ``'/path'`` → mount a private tmpfs on that path (ignored for Docker)

        The host path may contain ``%(name)s`` which is expanded here.
        """
        for entry in private_dirs:
            if isinstance(entry, tuple) and len(entry) == 2:
                container_path, host_path = entry
                host_path_expanded = host_path.replace('%(name)s', name)
                self.volumes.append(f"{host_path_expanded}:{container_path}")
            elif isinstance(entry, str):
                # For bare-string tmpfs entries, back the path from the
                # Node's tree in the Lab directory so the container sees an
                # isolated filesystem.
                if self.net is None:
                    raise ConfigurationError(
                        f"ContainerHost '{name}': a bare privateDirs entry "
                        f"({entry!r}) is backed from the Lab directory, so the "
                        f"Node needs a Lab (add it with net.addContainer)"
                    )
                tmp_dir = self.net.container_dir(name, entry)
                os.makedirs(tmp_dir, exist_ok=True)
                self.volumes.append(f"{tmp_dir}:{entry}")

    def _create_container(self, name: str) -> None:
        """Create and start Docker container for this node."""
        debug(f'ContainerHost: Creating container {self.container_name} with image {self.dimage}\n')
        if self.remove_existing:
            self._docker.rm(force=True)
        if self.pull_image and not self._docker.image_exists(self.dimage):
            debug(f'ContainerHost: Pulling Docker image {self.dimage}\n')
            self._docker.pull(self.dimage)

        cmd: Optional[List[str]] = None
        if self.dcmd:
            cmd = [SHELL_PATH, SHELL_FLAGS, self.dcmd]
        else:
            cmd = [SHELL_PATH, SHELL_FLAGS, DEFAULT_CONTAINER_CMD]

        self._docker.run(
            image=self.dimage,
            hostname=name,
            cmd=cmd,
            volumes=self.volumes,
            environment=self.environment,
            ports=self.ports,
            port_bindings=self.port_bindings,
            cap_add=self.cap_add,
        )
        self._setup_netns_symlink()

    def _setup_netns_symlink(self) -> None:
        """Create symlink to expose container netns to host ip-netns tools."""
        pid = self._docker.get_pid()
        os.makedirs(NETNS_DIR, exist_ok=True)
        netns_path = self.netns_link_path
        if os.path.exists(netns_path) or os.path.islink(netns_path):
            try:
                os.unlink(netns_path)
            except OSError:
                pass
        try:
            os.symlink(f"/proc/{pid}/ns/net", netns_path)
        except OSError as e:
            warn(f"Warning: Could not create netns symlink for {self.name}: {e}\n")

    def _is_container_running(self) -> bool:
        return self._docker.is_running()

    def _get_container_pid(self) -> int:
        return self._docker.get_pid()

    def startShell(self, *_args: Any, **_kwargs: Any) -> None:
        """Start shell via ``docker exec -it`` inside the container.

        ``-it`` gives the container a real TTY so chr(3) from sendInt() is
        delivered as SIGINT by the TTY line discipline.  ``start_new_session=True``
        (equivalent to ``mnexec -d``) puts the docker exec process in a new
        session with no controlling terminal, preventing the host PTY slave
        from becoming a second controlling terminal and conflicting with the
        container PTY.

        The sentinel chr(127) is checked with ``in`` rather than ``[-1]``
        because the container TTY may append escape sequences (e.g. DSR
        ``\x1b[6n``) after it.
        """
        self.install_binary_safe_decoder()

        if self.shell:
            error(f"{self.name}: shell is already running\n")
            return

        if not self._is_container_running():
            raise RuntimeError(
                f"Container {self.container_name} is not running for node {self.name}"
            )

        cmd = self._docker.exec_interactive(
            "env", f"PS1={SHELL_PROMPT_SENTINEL}", SHELL_PATH, INTERACTIVE_SHELL_FLAGS
        )
        debug(f"*** {self.name}: startShell cmd={cmd}\n")

        self.master, self.slave = pty.openpty()
        self.shell = self._popen(
            cmd,
            stdin=self.slave, stdout=self.slave, stderr=self.slave,
            close_fds=True, start_new_session=True,
        )
        self.stdin = os.fdopen(self.master, "r")
        self.stdout = self.stdin
        self.pid = self._get_container_pid()
        debug(f"*** {self.name}: shell pid={self.shell.pid} container pid={self.pid}\n")

        self.pollOut = select.poll()
        self.pollOut.register(self.stdout)
        self.outToNode[self.stdout.fileno()] = self
        self.inToNode[self.stdin.fileno()] = self
        self.execed = False
        self.lastCmd = None
        self.lastPid = None
        self.readbuf = ""

        timeout = SHELL_READY_TIMEOUT
        deadline = time.monotonic() + timeout
        while True:
            self.shell.poll()
            if self.shell.returncode is not None:
                error(f"{self.name}: shell exited (rc={self.shell.returncode}) before prompt\n")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if self.stdout:
                    try:
                        self.pollOut.unregister(self.stdout)
                    except (KeyError, ValueError, OSError):
                        pass
                    self.outToNode.pop(self.stdout.fileno(), None)
                if self.stdin:
                    self.inToNode.pop(self.stdin.fileno(), None)
                    try:
                        self.stdin.close()
                    except OSError:
                        pass
                if self.slave is not None:
                    try:
                        os.close(self.slave)
                    except OSError:
                        pass
                if self.shell:
                    try:
                        self.shell.kill()
                        self.shell.wait()
                    except (OSError, subprocess.SubprocessError):
                        pass
                self.shell = None
                self.stdin = None  # type: ignore[assignment]
                self.stdout = None  # type: ignore[assignment]
                self.master = None  # type: ignore[assignment]
                self.slave = None  # type: ignore[assignment]
                self.waiting = False
                raise RuntimeError(
                    f"{self.name}: timed out waiting for container shell prompt"
                )
            # Short slices so a shell that exits is reported as such, not as a timeout
            events = self.pollOut.poll(max(1, int(min(remaining, 0.1) * 1000)))
            if events:
                data = self.read(1024)
                debug(f"*** {self.name}: read {len(data)} bytes: {data!r}\n")
                if data and SHELL_PROMPT_SENTINEL in data:
                    break

        self.waiting = False
        self.cmd("unset HISTFILE; stty -echo; set +m")

    def _check_shell(self) -> None:
        if self._is_container_running():
            if self.shell:
                self.shell.poll()
                if self.shell.returncode is not None:
                    debug(f"*** {self.name}: shell died, restarting\n")
                    self.shell = None
                    self.startShell()
            else:
                self.startShell()
        else:
            warn(f"Container {self.container_name} is not running for {self.name}\n")
            self.shell = None

    def sendInt(self, intr: str = chr(3)) -> None:
        self.write(intr)

    def sendCmd(self, *args: Any, **kwargs: Any) -> None:
        self._check_shell()
        if not self.shell:
            return
        super().sendCmd(*args, **kwargs)

    def popen(self, *args: Any, **kwargs: Any):
        if not self._is_container_running():
            error(f"ERROR: Can't connect to container '{self.container_name}' "
                  f"for host '{self.name}'\n")
            return None
        exec_cmd = self._docker.exec_interactive(tty=False)
        return super().popen(*args, mncmd=exec_cmd, **kwargs)

    def cmd(self, *args: Any, **kwargs: Any) -> str:
        self.sendCmd(*args, **kwargs)
        return self.waitOutput(kwargs.get("verbose", False))

    def terminate(self) -> None:
        netns_path = self.netns_link_path
        if os.path.exists(netns_path) or os.path.islink(netns_path):
            try:
                os.unlink(netns_path)
            except OSError:
                pass
        if self._is_container_running():
            self._docker.rm(force=True)
        super().terminate()


class ContainerLink(Link):
    """Veth-pair link that creates interfaces from the host namespace.

    Mininet's default Link runs ``ip link add`` inside node1's namespace via
    ``node1.cmd``.  Docker containers cannot reach other container namespaces
    that way.  This subclass runs all ``ip link`` calls via ``quietRun``
    (host side), mirroring mininet-sec's approach for Kubernetes pods.

    ``fast=True`` is forced so ``makeIntfPair`` receives the node objects
    and ``moveIntfFn`` is a no-op — all namespace placement is handled
    inside ``makeIntfPair``.
    """

    def __init__(self, node1: Any, node2: Any, **params: Any) -> None:
        if isinstance(node1, ContainerHost):
            params.setdefault("cls1", ContainerIntf)
        if isinstance(node2, ContainerHost):
            params.setdefault("cls2", ContainerIntf)
        params.setdefault("fast", True)
        super().__init__(node1, node2, **params)

    @classmethod
    def makeIntfPair(
        cls,
        intfname1: str,
        intfname2: str,
        addr1: Optional[str] = None,
        addr2: Optional[str] = None,
        node1: Optional[Any] = None,
        node2: Optional[Any] = None,
        deleteIntfs: bool = True,
    ) -> None:
        if deleteIntfs:
            quietRun(f"ip link del {intfname1} 2>/dev/null")
            quietRun(f"ip link del {intfname2} 2>/dev/null")

        if isinstance(node2, ContainerHost):
            netns2 = node2._get_container_pid()
        elif node2 is not None:
            netns2 = node2.pid
        else:
            netns2 = 1  # host namespace

        addr1_part = f"address {addr1} " if addr1 else ""
        addr2_part = f"address {addr2} " if addr2 else ""

        # Create pair from host: intfname1 in root ns, intfname2 straight into netns2.
        out = quietRun(
            f"ip link add name {intfname1} {addr1_part}"
            f"type veth peer name {intfname2} {addr2_part}"
            f"netns {netns2}"
        )
        if out:
            raise Exception(f"Error creating interface pair ({intfname1},{intfname2}): {out}")

        # Move intfname1 into node1's namespace if needed.
        if isinstance(node1, ContainerHost):
            out = quietRun(f"ip link set {intfname1} netns {node1._get_container_pid()}")
            if out:
                raise Exception(f"Error moving {intfname1} to container {node1.name}: {out}")
        elif node1 is not None and getattr(node1, "inNamespace", False):
            out = quietRun(f"ip link set {intfname1} netns {node1.pid}")
            if out:
                raise Exception(f"Error moving {intfname1} to node {node1.name}: {out}")
        # else: intfname1 stays in host namespace (switch, bridge, etc.)
