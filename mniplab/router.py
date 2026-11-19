"""FRR-based Router class for IP routing."""

import os
from typing import Optional, Any, List, Dict
from mininet.node import Node
from mininet.log import error, info
from mniplab.constants import FRR_INIT_SCRIPT
from mniplab.errors import RouterError, FRRError
from mniplab.logging import get_logger
from mniplab.kernel import KernelConfigManager
from mniplab.namespace import NamespaceMixin
from mniplab.service import ServiceMixin
from mniplab.shell import BinarySafeShellMixin
from mniplab.switch import SubinterfaceMixin

logger = get_logger(__name__)


def ensure_frr_available() -> None:
    """Validate FRR installation and availability.
    
    Checks that the FRR init script exists at the configured path.
    Can be bypassed by setting MNIPLAB_SKIP_DAEMON_PROBES=1 (used by unit tests).
    
    Raises:
        FRRError: If FRR init script not found with installation instructions
    """
    if os.environ.get("MNIPLAB_SKIP_DAEMON_PROBES") == "1":
        logger.debug("Skipping FRR presence check (MNIPLAB_SKIP_DAEMON_PROBES=1)")
        return

    if not os.path.exists(FRR_INIT_SCRIPT):
        raise FRRError(
            f"FRR not installed or init script not found: {FRR_INIT_SCRIPT}\n"
            f"\n"
            f"Install FRR:\n"
            f"  Debian/Ubuntu: sudo apt-get install frr\n"
            f"  RHEL/CentOS: sudo yum install frr\n"
            f"  Alpine: sudo apk add frr\n"
            f"\n"
            f"Or set FRR_INIT_SCRIPT environment variable:\n"
            f"  export FRR_INIT_SCRIPT=/custom/path/to/frrinit.sh\n"
            f"\n"
            f"Verify installation: which zebra && which ospfd\n"
            f"Current FRR_INIT_SCRIPT: {FRR_INIT_SCRIPT}"
        )
    
    logger.debug(f"FRR available at {FRR_INIT_SCRIPT}")


class FRRouter(SubinterfaceMixin, ServiceMixin, BinarySafeShellMixin, NamespaceMixin, Node):
    """FRR-based router with IPv4 and IPv6 forwarding enabled.
    
    FRR reads hostnames from config files (frr.conf/vtysh.conf).
    
    Attributes:
        display_type (str): Node type for visualization ('router')
        frr_daemons (list): List of enabled FRR daemon names
        vrfs (dict): Mapping of VRF names to routing table IDs
        private_namespace_capable (bool): Has its own PID and UTS namespaces;
            see :mod:`mniplab.namespace`.
    """

    display_type = "router"
    private_namespace_capable = True
    
    def __init__(self, name: str, **params: Any) -> None:
        """Initialize FRRouter with FRR daemon tracking.
        
        Args:
            name: Router name (must be unique in the network)
            **params: Additional parameters passed to Node
        """
        super(FRRouter, self).__init__(name, **params)
        # Track enabled FRR daemons (set by addRouter)
        self.frr_daemons: List[str] = []
        # Track VRFs (name -> table_id)
        self.vrfs: Dict[str, int] = params.get('vrfs', {})
        # Router NAT declared at addRouter: port forwards and static mappings
        self.nat: List[Any] = list(params.get('nat') or [])
        # Track interface vrf attachments (intf_name -> vrf_name)
        self.vrf_attachments: Dict[str, str] = {}
        # Kernel networking configuration (sysctl, forwarding, VRF)
        self._kernel = KernelConfigManager(self)

    def attach_interface(self, intf: str, vrf: str) -> None:
        """Attach an interface to a VRF.
        
        Args:
            intf: Interface name
            vrf: VRF name
        """
        self.vrf_attachments[intf] = vrf

    def _apply_vrf_attachments(self) -> None:
        """Apply stored VRF attachments to interfaces, preserving existing IPs."""
        self._kernel.setup_vrfs(self.vrfs, self.vrf_attachments)
    
    def _apply_sysctl(self, setting: str, expected_value: str, critical: bool = False,
                      error_context: str = "") -> None:
        """Apply a sysctl setting with optional error checking.

        Delegates to :class:`KernelConfigManager`.
        """
        self._kernel.apply_sysctl(setting, expected_value, critical, error_context)
    
    def _enable_forwarding(self) -> None:
        """Enable IPv4 and IPv6 forwarding (critical for routing)."""
        self._kernel.enable_forwarding()
    
    def _configure_icmp_rate_limiting(self) -> None:
        """Disable ICMP rate limiting for lab environment."""
        self._kernel.disable_icmp_rate_limiting()
    
    def _configure_multipath(self) -> None:
        """Enable multipath hash policy for load balancing."""
        self._kernel.enable_multipath()
    
    def _setup_vrfs(self) -> None:
        """Setup VRFs and attach interfaces."""
        self._kernel.setup_vrfs(self.vrfs, self.vrf_attachments)

    def config(self, **params: Any) -> None:
        """Configure router with IP forwarding and start FRR daemons.
        
        This method enables IPv4/IPv6 forwarding in the kernel and starts
        the FRR routing daemon suite. Critical failures will raise exceptions.
        
        Args:
            **params: Configuration parameters
            
        Raises:
            RouterError: If IP forwarding cannot be enabled
            FRRError: If FRR init script is missing or fails to start
        """
        super(FRRouter, self).config(**params)
        
        # Enable IP forwarding (critical for routing)
        self._enable_forwarding()
        
        # Disable ICMP rate limiting for lab environment
        self._configure_icmp_rate_limiting()
        
        # Enable multipath hash policy for load balancing
        self._configure_multipath()
        
        # Setup VRFs and interface attachments
        self._setup_vrfs()

    def start_frr(self) -> None:
        """Start FRR daemons for this router.
        
        Called by mnIPLab.start() after the network is built so that
        FRR startup appears as a distinct Mininet-style step.
        
        Raises:
            FRRError: If FRR init script is missing or fails to start
        """
        ensure_frr_available()
        
        result = self.cmd(f'{FRR_INIT_SCRIPT} start')
        if result and ('error' in result.lower() or 'fail' in result.lower()):
            frr_dir = self.get_frr_dir()
            raise FRRError(
                f"{self.name}: FRR startup failed\n"
                f"Command: {FRR_INIT_SCRIPT} start\n"
                f"Output: {result}\n"
                f"Debug steps:\n"
                f"  1. Check FRR is installed: which zebra\n"
                f"  2. View daemon config: cat {frr_dir}/daemons\n"
                f"  3. Check logs: tail -f /var/log/frr/zebra.log\n"
                f"  4. Verify permissions on {frr_dir}\n"
                f"  5. Check if daemons already running: ps aux | grep frr"
            )
    
    def _read_frr_file(self, filename: str) -> Optional[str]:
        """Read content from an FRR config file.
        
        Args:
            filename: Name of file in FRR directory (e.g., 'daemons', 'frr.conf')
            
        Returns:
            File content as string, or None if file doesn't exist
        """
        frr_dir = self.get_frr_dir()
        if not frr_dir:
            return None
        
        file_path = os.path.join(frr_dir, filename)
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except OSError:
            return None
    
    def _write_frr_file(self, filename: str, content: str) -> bool:
        """Write content to an FRR config file.
        
        Args:
            filename: Name of file in FRR directory (e.g., 'daemons', 'frr.conf')
            content: Content to write
            
        Returns:
            True if successful, False otherwise
        """
        frr_dir = self.get_frr_dir()
        if not frr_dir:
            return False
        
        file_path = os.path.join(frr_dir, filename)
        
        try:
            with open(file_path, 'w') as f:
                f.write(content)
            return True
        except OSError:
            return False
    
    def _append_to_frr_file(self, filename: str, content: str) -> bool:
        """Append content to an FRR config file.
        
        Args:
            filename: Name of file in FRR directory (e.g., 'frr.conf')
            content: Content to append
            
        Returns:
            True if successful, False otherwise
        """
        frr_dir = self.get_frr_dir()
        if not frr_dir:
            return False
        
        file_path = os.path.join(frr_dir, filename)
        if not os.path.exists(file_path):
            return False
        
        try:
            with open(file_path, 'a') as f:
                f.write('\n' + content + '\n')
            return True
        except OSError:
            return False

    def terminate(self) -> None:
        """Stop FRR daemons and terminate router node."""
        if os.path.exists(FRR_INIT_SCRIPT):
            result = self.cmd(f'{FRR_INIT_SCRIPT} stop')
            if result and ('error' in result.lower() or 'fail' in result.lower()):
                error(f'{self.name}: FRR shutdown may have failed: {result}')
        super(FRRouter, self).terminate()

    def get_frr_dir(self) -> Optional[str]:
        """Resolve the host path of the router's FRR configuration directory.
        
        Returns:
            Path to FRR config directory, or None if not found
        """
        for d in self.privateDirs:
            if isinstance(d, tuple) and d[0] == '/etc/frr':
                return d[1].replace('%(name)s', self.name)
        return None

    def add_frr_config(self, config_str: str) -> 'FRRouter':
        """Append configuration snippets to frr.conf.
        
        Args:
            config_str: FRR configuration text to append
            
        Returns:
            Self for method chaining
        """
        self._append_to_frr_file('frr.conf', config_str)
        return self
