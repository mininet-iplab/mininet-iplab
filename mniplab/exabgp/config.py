"""ExaBGP configuration builder using Mako templates.

Provides a simple, strict configuration builder that renders ExaBGP config files
from Mako templates with validation of required variables.
"""
import os
from typing import Any, Dict, List
from mniplab.config_utils import ConfigBuilder
from mniplab.exabgp.models import BGPNeighbor
from mniplab.errors import ConfigurationError
from mniplab.logging import get_logger

logger = get_logger(__name__)


DEFAULT_EXABGP_ENV_CONTENT = (
    "[exabgp.api]\n"
    "cli = true\n"
    "\n"
    "[exabgp.daemon]\n"
    "user = 'root'\n"
    "daemonize = false\n"
)


class ExaBGPConfigBuilder(ConfigBuilder):
    """Render ExaBGP config files from templates.

    Designed to be small and strict: required variables are checked and the
    rendered file is written to the target directory.
    Inherits common template rendering logic from ConfigBuilder base class.
    """

    REQUIRED_VARS = {
        'name': str,
        'neighbors': list,
        'routes': list,
    }

    def _get_template_dir(self) -> str:
        """Get default ExaBGP template directory path.
        
        Returns:
            Path to the ExaBGP templates directory
        """
        return os.path.join(os.path.dirname(__file__), 'templates')

    def setup_config_dir(self, name: str, config_dir: str,
                          variables: Dict[str, Any]) -> None:
        """Set up the ExaBGP configuration directory and write config files.

        Args:
            name: ExaBGP node name; sets variables['name'] if not already present.
            config_dir: Host-side directory to store configuration files.
            variables: Template variables (neighbors, routes, etc.).

        Raises:
            ConfigurationError: If directory creation or file writing fails.
        """
        # Ensure 'name' is set in variables
        variables_with_defaults = dict(variables)
        variables_with_defaults.setdefault('name', name)

        self._check_distinct_local_addresses(
            variables_with_defaults['name'], variables_with_defaults.get('neighbors', [])
        )
        self._ensure_config_dir(config_dir, mode=0o755)

        # Render and write configuration files
        self._write_main_config(config_dir, variables_with_defaults)
        self._write_default_env(config_dir)

        logger.debug(f"ExaBGP config written to {config_dir}")

    def _write_main_config(self, config_dir: str, variables: Dict[str, Any]) -> None:
        """Write the main exabgp.conf file.
        
        Args:
            config_dir: Directory to write the file to
            variables: Template variables
        
        Raises:
            ConfigurationError: If file writing fails
        """
        template_file = os.path.join(self.template_dir, 'exabgp.conf.mako')
        rendered = self.render_template(template_file, variables)
        conf_path = os.path.join(config_dir, 'exabgp.conf')
        
        try:
            with open(conf_path, 'w', encoding='utf-8') as f:
                f.write(rendered)
        except IOError as exc:
            raise ConfigurationError(
                f"Failed to write ExaBGP config file {conf_path}: {exc}"
            ) from exc

    def _write_default_env(self, config_dir: str) -> None:
        """Write default exabgp.env configuration if missing.

        Args:
            config_dir: Directory to write the file to.
        """
        env_path = os.path.join(config_dir, 'exabgp.env')
        if os.path.exists(env_path):
            return
        try:
            with open(env_path, 'w', encoding='utf-8') as ef:
                ef.write(DEFAULT_EXABGP_ENV_CONTENT)
        except IOError as exc:
            logger.warning(f"Failed to write exabgp.env: {exc}")

    @staticmethod
    def _check_distinct_local_addresses(name: str, neighbors: List[BGPNeighbor]) -> None:
        """Refuse two Neighbors on one Speaker that share a ``local-address``.

        Every Neighbor renders ``passive``, so each needs its own
        listening address: a shared one is the listener ExaBGP 5 kills at
        start, and the configuration can never work.

        Args:
            name: The Speaker's name, for the message.
            neighbors: The Speaker's ``BGPNeighbor`` list.

        Raises:
            ConfigurationError: Naming the Speaker, both Neighbors and the
                address they share.
        """
        seen: Dict[str, BGPNeighbor] = {}
        for neighbor in neighbors:
            earlier = seen.get(neighbor.local_address)
            if earlier is not None:
                raise ConfigurationError(
                    f"ExaBGP speaker {name}: neighbors {earlier.peer} and "
                    f"{neighbor.peer} share local-address {neighbor.local_address}; "
                    f"a passive speaker listens on one local-address per neighbor"
                )
            seen[neighbor.local_address] = neighbor
