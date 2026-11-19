"""ExaBGP node wrapper to run ExaBGP inside a Mininet namespace."""
import os
import shutil
from typing import Any, Optional
from mniplab.host import mnIPLabHost
from mniplab.exabgp.config import ExaBGPConfigBuilder
from mniplab.exabgp.control_client import SpeakerControlClient
from mniplab.exabgp.models import BGPRoute, BGPNeighbor
from mniplab.errors import ExaBGPError, ConfigurationError
from mniplab.constants import SERVICE_READY_TIMEOUT
from mniplab.logging import get_logger
from mniplab.utils import wait_until

logger = get_logger(__name__)


class ExaBGPNode(mnIPLabHost):
    """Host node that runs ExaBGP as an external process inside its namespace.

    The node expects a privateDirs mapping that includes /etc/exabgp -> host path.
    If not provided, the Lab's speaker private dirs (under the Lab directory)
    are applied by `mnIPLab.addExaBGP`.

    Attributes:
        display_type (str): Node type for visualization ('exabgp')
    """

    display_type = "bgp-speaker"

    def config(self, **params: Any) -> None:
        """Configure node and prepare ExaBGP config directory.

        Relevant keys passed via **params (or stored on the node at creation):
            neighbors: List of BGPNeighbor instances
            routes: List of BGPRoute instances (global fallback)
            local_as: Local AS number
        """
        super(ExaBGPNode, self).config(**params)

        # Pre-flight check: ensure exabgp binary is present on the host
        self._verify_exabgp_installation()

        # Resolve ExaBGP host-side directory from privateDirs
        exabgp_dir = self.get_exabgp_dir()
        if exabgp_dir is None:
            raise ExaBGPError(
                "ExaBGP config directory not found in privateDirs. "
                "Expected mapping for /etc/exabgp."
            )

        # Build ExaBGP configuration
        variables = self._prepare_config_variables(params)
        self._build_config(exabgp_dir, variables)

        # Setup runtime directories (FIFOs, log/run dirs)
        self._setup_runtime_directories()

    def _verify_exabgp_installation(self) -> None:
        """Verify ExaBGP binary is available.
        
        Raises:
            ExaBGPError: If exabgp is not found in PATH
        """
        if not shutil.which('exabgp'):
            raise ExaBGPError(
                "ExaBGP binary not found in PATH. Install via: pip install exabgp"
            )

    def _prepare_config_variables(self, params: dict) -> dict:
        """Prepare template variables from node params and config-time params.
        
        Args:
            params: Configuration parameters passed at config time
        
        Returns:
            Dictionary of template variables
        """
        # Merge params provided at config time with node-level params stored on creation
        return {
            'name': self.name,
            'neighbors': params.get('neighbors', self.params.get('neighbors', [])),
            'routes': params.get('routes', self.params.get('routes', [])),
            'local_as': params.get('local_as', self.params.get('local_as', None)),
        }

    def _build_config(self, exabgp_dir: str, variables: dict) -> None:
        """Build and write ExaBGP configuration files.
        
        Args:
            exabgp_dir: Host-side configuration directory path
            variables: Template variables
        
        Raises:
            ExaBGPError: If configuration build fails
        """
        builder = ExaBGPConfigBuilder()
        try:
            builder.setup_config_dir(self.name, exabgp_dir, variables)
        except ConfigurationError as exc:
            raise ExaBGPError(f"Failed to setup ExaBGP config: {exc}") from exc

    def _setup_runtime_directories(self) -> None:
        """Setup log and run directories for ExaBGP runtime."""
        # Resolve host-side log and run directories from privateDirs mappings
        host_log_dir = self._resolve_private_dir('/var/log/exabgp')
        host_run_dir = self._resolve_private_dir('/var/run/exabgp')

        # Create host-side directories with permissive permissions
        if host_log_dir:
            self._ensure_directory(host_log_dir, 0o755)

        if host_run_dir:
            self._ensure_run_dir(host_run_dir)
            self.ensure_fifos()
            # Also make sure the directory exists inside the namespace
            self.cmd('mkdir -p /var/run/exabgp && chmod 0755 /var/run/exabgp')

    def start_exabgp(self) -> None:
        """Start the ExaBGP daemon for this node.

        Called by mnIPLab.start() after the network is built so that
        ExaBGP startup appears as a distinct Mininet-style step. Returns
        only once the daemon answers on its pipe (see :meth:`await_ready`).
        """
        exabgp_dir = self.get_exabgp_dir()
        if exabgp_dir is None:
            raise ExaBGPError(
                "ExaBGP config directory not found in privateDirs. "
                "Expected mapping for /etc/exabgp."
            )
        self._start_exabgp_daemon(exabgp_dir)
        self.await_ready()

    def _start_exabgp_daemon(self, exabgp_dir: str) -> None:
        """Start ExaBGP daemon process.
        
        Args:
            exabgp_dir: Host-side configuration directory path
        """
        conf_path = os.path.join(exabgp_dir, 'exabgp.conf')
        host_log_dir = self._resolve_private_dir('/var/log/exabgp')
        target_log = host_log_dir if host_log_dir else '/var/log/exabgp'

        # ExaBGP resolves exabgp.env relative to its own binary, so the file
        # written beside exabgp.conf is only read when passed explicitly with
        # --env-file; that flag is what makes user='root' and cli=true take
        # effect.
        env_path = '/etc/exabgp/exabgp.env'
        # Use nohup to detach and redirect output to the log directory (inside namespace)
        cmd = (f"nohup exabgp --env-file {env_path} server {conf_path} "
               f"> {target_log}/{self.name}.log 2>&1 &")
        self.cmd(cmd)
        logger.debug(f"Started ExaBGP {self.name}: {cmd}")

    def _ensure_directory(self, path: str, mode: int = 0o755) -> None:
        """Ensure a directory exists with specified permissions.
        
        Args:
            path: Directory path to create
            mode: Permission mode (default: 0o755)
        """
        try:
            os.makedirs(path, exist_ok=True)
            os.chmod(path, mode)
        except OSError:
            pass

    def terminate(self) -> None:
        """Terminate ExaBGP process and clean up FIFOs."""
        # Kill exabgp processes started in this namespace
        try:
            self.cmd("pkill -f 'exabgp.*exabgp.conf' || true")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"Failed to stop ExaBGP process {self.name}: {exc}")

        # Cleanup host-side FIFOs
        self._cleanup_fifos()
        super().terminate()

    def get_exabgp_dir(self) -> Optional[str]:
        """Resolve the host-side ExaBGP configuration directory mapping.

        Returns:
            Path to the host directory mapped to /etc/exabgp in namespace.
        """
        return self._resolve_private_dir('/etc/exabgp')

    def get_exabgp_run_dir(self) -> Optional[str]:
        """Resolve the host-side ExaBGP run directory mapping.

        Returns:
            Path to the host directory mapped to /var/run/exabgp in namespace.
        """
        return self._resolve_private_dir('/var/run/exabgp')

    def _resolve_private_dir(self, namespace_path: str) -> Optional[str]:
        """Resolve a private directory mapping from namespace path to host path.

        Args:
            namespace_path: The path inside the namespace (e.g., '/etc/exabgp').

        Returns:
            Host-side path with %(name)s placeholder replaced, or None if not found.
        """
        for item in self.privateDirs:
            if isinstance(item, tuple) and item[0] == namespace_path:
                return item[1].replace('%(name)s', self.name)
        return None

    def _ensure_run_dir(self, host_run_dir: str) -> None:
        """Ensure the ExaBGP run directory exists with proper permissions.

        Args:
            host_run_dir: Host-side path to the run directory.
        """
        try:
            os.makedirs(host_run_dir, exist_ok=True)
            os.chmod(host_run_dir, 0o755)
        except OSError as exc:
            logger.warning(f"Failed to create run directory {host_run_dir}: {exc}")

    def ensure_fifos(self) -> None:
        """Create named pipes (FIFOs) for ExaBGP CLI communication.

        Creates exabgp.in and exabgp.out FIFOs in the node's run directory.
        Safe to call multiple times (idempotent).
        """
        host_run_dir = self.get_exabgp_run_dir()
        if not host_run_dir:
            return

        fifo_paths = {
            'exabgp.in': os.path.join(host_run_dir, 'exabgp.in'),
            'exabgp.out': os.path.join(host_run_dir, 'exabgp.out'),
        }
        
        for fifo_name, fifo_path in fifo_paths.items():
            try:
                if not os.path.exists(fifo_path):
                    os.mkfifo(fifo_path)
                    os.chmod(fifo_path, 0o600)
            except OSError as exc:
                logger.warning(f"Failed to create FIFO {fifo_name} in {host_run_dir}: {exc}")

    def _cleanup_fifos(self) -> None:
        """Clean up host-side FIFO files."""
        run_dir = self.get_exabgp_run_dir()
        if not run_dir or not os.path.exists(run_dir):
            return

        fifo_names = ('exabgp.in', 'exabgp.out')
        for fifo_name in fifo_names:
            fifo_path = os.path.join(run_dir, fifo_name)
            self._remove_fifo(fifo_path)

    def _remove_fifo(self, fifo_path: str) -> None:
        """Remove a FIFO file if it exists.
        
        Args:
            fifo_path: Path to the FIFO file
        """
        try:
            if os.path.exists(fifo_path) and not os.path.isfile(fifo_path):
                os.unlink(fifo_path)
        except OSError as exc:
            logger.warning(f"Failed to remove FIFO {fifo_path}: {exc}")

    def control_client(self) -> SpeakerControlClient:
        """Return this Speaker's control client, one for the Node's lifetime.

        One client per Speaker is what makes its lock one lock: the CLI in
        the main thread and the Web API in its executor both serialise on
        it, so no two commands share the pipe's byte stream.

        Returns:
            The client on this Node's FIFO pair.

        Raises:
            ExaBGPError: The Node has no ``/var/run/exabgp`` mapping, so it
                never prepared a FIFO pair.
        """
        client = getattr(self, '_control_client', None)
        if client is None:
            run_dir = self.get_exabgp_run_dir()
            if run_dir is None:
                raise ExaBGPError(
                    f"ExaBGP run directory not found in privateDirs of {self.name}. "
                    "Expected mapping for /var/run/exabgp."
                )
            client = SpeakerControlClient(run_dir, speaker=self.name)
            self._control_client = client
        return client

    def await_ready(self, timeout: float = SERVICE_READY_TIMEOUT) -> None:
        """Block until the daemon reads its command pipe and answers.

        Readiness is the lifecycle's post-condition, as it is for the DHCP
        and DNS Services: a caller may read the Speaker the moment the Lab
        has started.

        Args:
            timeout: Seconds to wait for the first answer.

        Raises:
            ExaBGPError: The daemon did not answer within the timeout; the
                message names its log.
        """
        if not wait_until(self.control_client().is_running, timeout):
            host_log_dir = self._resolve_private_dir('/var/log/exabgp') or '/var/log/exabgp'
            raise ExaBGPError(
                f"ExaBGP on {self.name} did not answer on its control pipe within "
                f"{timeout:.0f}s. Check {os.path.join(host_log_dir, self.name + '.log')}."
            )
