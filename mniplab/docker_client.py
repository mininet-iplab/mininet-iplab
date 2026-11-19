"""Docker client abstraction for Mininet-IPLab ContainerHost.

Centralises all ``subprocess.run`` calls that interact with the Docker CLI so
that :class:`~mniplab.container.ContainerHost` can focus on Mininet
integration rather than Docker command details.
"""

import subprocess
from typing import List, Optional, Sequence, Dict


class DockerClient:
    """Low-level Docker CLI adapter.

    The interface is intentionally narrow — pull, run, inspect, exec, rm —
    because mnIPLab only needs lifecycle control of a single container per
    node.
    """

    def __init__(self, container_name: str) -> None:
        self.container_name = container_name

    @staticmethod
    def _run(cmd: List[str], check: bool = True,
             capture: bool = True) -> subprocess.CompletedProcess:
        """Execute a subprocess command and return the result."""
        return subprocess.run(
            cmd, check=False, capture_output=capture, text=True
        )

    def image_exists(self, image: str) -> bool:
        """Return ``True`` if *image* is present locally."""
        proc = self._run(["docker", "image", "inspect", image], check=False)
        return proc.returncode == 0

    def pull(self, image: str) -> None:
        """Pull *image* from the registry."""
        proc = self._run(["docker", "pull", image])
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to pull image {image}: {proc.stderr.strip()}")

    def rm(self, force: bool = True) -> None:
        """Remove the container if it exists."""
        flags = ["-f"] if force else []
        self._run(["docker", "rm", *flags, self.container_name], check=False)

    def is_running(self) -> bool:
        """Return ``True`` if the container is currently running."""
        proc = self._run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"

    def get_pid(self) -> int:
        """Return the host PID of the container's init process."""
        proc = self._run(
            ["docker", "inspect", "-f", "{{.State.Pid}}", self.container_name],
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to get PID for container {self.container_name}"
            )
        try:
            return int(proc.stdout.strip())
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid container pid for {self.container_name}: {proc.stdout.strip()}"
            ) from exc

    def run(self, image: str, hostname: str, cmd: Optional[Sequence[str]] = None,
            volumes: Optional[Sequence[str]] = None,
            environment: Optional[Dict[str, str]] = None,
            ports: Optional[Sequence[str]] = None,
            port_bindings: Optional[Dict[str, str]] = None,
            cap_add: Optional[Sequence[str]] = None,
            network: str = "none") -> None:
        """Create and start the container in detached mode.

        Args:
            image: Docker image name.
            hostname: Container hostname.
            cmd: Command to run inside the container.
            volumes: ``-v`` flags (already formatted as ``host:container``).
            environment: Key/value pairs for ``-e`` flags.
            ports: Port mapping strings for ``-p`` flags.
            port_bindings: Dict of ``{container_port: host_port}``.
            cap_add: Capabilities to add (e.g. ``NET_ADMIN``).
            network: Docker network mode (default ``none``).

        Raises:
            RuntimeError: If ``docker run`` fails.
        """
        run_cmd: List[str] = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--hostname", hostname,
            "--network", network,
            "--cap-add", "NET_ADMIN",
            "--cap-add", "NET_RAW",
        ]

        for cap in (cap_add or []):
            run_cmd.extend(["--cap-add", cap])

        for k, v in (environment or {}).items():
            run_cmd.extend(["-e", f"{k}={v}"])

        for vol in (volumes or []):
            run_cmd.extend(["-v", vol])

        for port in (ports or []):
            run_cmd.extend(["-p", port])

        for cport, hport in (port_bindings or {}).items():
            run_cmd.extend(["-p", f"{hport}:{cport}"])

        run_cmd.append(image)
        if cmd:
            run_cmd.extend(list(cmd))

        proc = self._run(run_cmd)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create container {self.container_name}: {proc.stderr.strip()}"
            )

    def exec_interactive(self, *args: str, tty: bool = True) -> List[str]:
        """Build a ``docker exec`` command list.

        Returns:
            The command list (not executed) so that the caller can pass it to
            :func:`subprocess.Popen` or similar.
        """
        cmd = ["docker", "exec"]
        if tty:
            cmd.append("-it")
        else:
            cmd.append("-i")
        cmd.append(self.container_name)
        cmd.extend(args)
        return cmd
