"""FRR configuration builder for mnIPLab routers."""

import glob
import os
from typing import Dict, List, Optional, Any
from mniplab.config_utils import ConfigBuilder
from mniplab.errors import ConfigurationError
from mniplab.logging import get_logger
from mniplab.utils import remove_stale_directory

# Module logger
logger = get_logger(__name__)


def rpki_module_installed(lib_root: str = '/usr/lib') -> bool:
    """Return whether FRR's RPKI module for ``bgpd`` is installed.

    The module ships as an optional FRR package. Its ``bgpd_rpki.so`` sits
    in ``frr/modules`` under the library root, or in a Debian multiarch
    directory beneath it (``/usr/lib/<triplet>/frr/modules``), so both are
    searched.

    Args:
        lib_root: Library root to search. The system's ``/usr/lib`` by
            default; tests point it at a temporary tree.

    Returns:
        True when ``bgpd_rpki.so`` exists in any FRR module directory.
    """
    patterns = (
        os.path.join(lib_root, 'frr', 'modules', 'bgpd_rpki.so'),
        os.path.join(lib_root, '*', 'frr', 'modules', 'bgpd_rpki.so'),
    )
    return any(glob.glob(pattern) for pattern in patterns)


class FRRConfigBuilder(ConfigBuilder):
    """Builder class for FRR router configurations.

    Inherits template rendering from ConfigBuilder.

    Attributes:
        REQUIRED_VARS: Template variables required for rendering:
            name (str): Router name.
            daemons (list): Enabled FRR daemon names.
            frr_config (str): Raw FRR configuration block.
    """
    
    # Required template variables with their types
    REQUIRED_VARS = {
        'name': str,
        'daemons': (list, tuple),
        'frr_config': str,
    }
    
    def _get_template_dir(self) -> str:
        """Get default FRR template directory path.
        
        Returns:
            Path to the FRR templates directory
        """
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'frrouter',
            'templates'
        )
    
    #: Files rendered on every start from the template and the Router's
    #: Protocols, over any copy present: no hand edit can drift from them.
    OWNED_FILES = ('daemons', 'vtysh.conf', 'support_bundle_commands.conf')

    #: The one file an Instructor or Learner writes; rendered only when missing.
    AUTHORED_FILE = 'frr.conf'

    def setup_config_dir(self, router_name: str, config_dir: str,
                         reset: bool = False, **kwargs) -> None:
        """Render a Router's FRR files into its configuration directory.

        The tool owns every file but ``frr.conf``: ``daemons``, ``vtysh.conf``
        and ``support_bundle_commands.conf`` are rendered from the template
        and the Router's Protocols each time, overwriting any copy, so a Seed
        holding only ``frr.conf`` starts and a hand-edited ``daemons`` lasts
        until the next start. ``frr.conf`` is rendered only when missing:
        in a caller-supplied ``frr_dir`` it is the Instructor's
        version-controlled configuration, or a Learner's ``write memory``.
        An ephemeral directory is documented as reset on each run, so its
        caller passes ``reset`` and a previous Lab's extra files go too.

        Args:
            router_name: Name of the router
            config_dir: Path to the configuration directory
            reset: Discard anything already at ``config_dir`` and generate
                fresh config files. For ephemeral directories only.
            **kwargs: Additional template variables (daemons, frr_config, etc.)

        Raises:
            ConfigurationError: If directory creation fails or template rendering fails
        """
        if reset:
            self._reset_config_dir(config_dir)

        # Create directory if it doesn't exist
        self._ensure_config_dir(config_dir, mode=0o755)

        variables = self._variables(router_name, kwargs)
        for dst_name in self.OWNED_FILES:
            try:
                self._render_config_file(dst_name, config_dir, variables)
            except ConfigurationError as e:
                # The Router cannot start without daemons; the others are optional.
                if dst_name == 'daemons':
                    raise
                logger.warning(f"Failed to render optional config file {dst_name}: {e}")

        if not os.path.exists(os.path.join(config_dir, self.AUTHORED_FILE)):
            self.write_frr_conf(router_name, config_dir, **kwargs)

    def _reset_config_dir(self, config_dir: str) -> None:
        """Remove an ephemeral config directory left by a previous Lab.

        Removing the whole directory rather than the files this builder
        writes is deliberate: a previous Lab's extra files (an included
        snippet, say) would otherwise still be read by FRR.

        Args:
            config_dir: Ephemeral directory to clear; a missing one is fine.

        Raises:
            OSError: If the existing path cannot be removed.
        """
        if not remove_stale_directory(config_dir) and os.path.lexists(config_dir):
            os.unlink(config_dir)
        logger.debug(f"Reset ephemeral FRR config directory: {config_dir}")

    def _render_config_file(
        self,
        dst_name: str,
        config_dir: str,
        variables: Dict[str, Any]
    ) -> None:
        """Render one config file from its template, replacing any present.

        Silently skips if no template is found.

        Args:
            dst_name: Destination filename
            config_dir: Destination directory
            variables: Template variables

        Raises:
            ConfigurationError: On render or write failure
        """
        template_path = self._find_template([f'{dst_name}.mako', dst_name])
        if template_path is None:
            logger.debug(f"No template found for {dst_name}")
            return

        rendered_content = self.render_template(template_path, variables)
        self._write_config_file(os.path.join(config_dir, dst_name), rendered_content)

    def _find_template(self, candidates: List[str]) -> Optional[str]:
        """Find the first existing template from a list of candidates.
        
        Args:
            candidates: List of template filenames to try in order
        
        Returns:
            Path to the first existing template, or None if none found
        """
        for candidate in candidates:
            candidate_path = os.path.join(self.template_dir, candidate)
            if os.path.exists(candidate_path):
                return candidate_path
        return None

    def _write_config_file(self, dst_path: str, content: str) -> None:
        """Write rendered content to a configuration file.
        
        Args:
            dst_path: Destination file path
            content: Rendered content to write
        
        Raises:
            ConfigurationError: If file writing fails
        """
        try:
            with open(dst_path, 'w') as f:
                f.write(content)
            logger.debug(f"Created config file: {dst_path}")
        except IOError as e:
            raise ConfigurationError(
                f"Failed to write config file: {dst_path}\n"
                f"Error: {e}\n"
                f"Check directory permissions and disk space."
            )

    def write_frr_conf(self, router_name: str, config_dir: str, **kwargs: Any) -> None:
        """Render a Router's ``frr.conf`` alone, replacing any present.

        For a Seed, which carries only this file; :meth:`setup_config_dir`
        renders the rest when the Router starts.

        Args:
            router_name: Name of the router.
            config_dir: Existing directory to write into.
            **kwargs: Template variables (``frr_config`` above all).

        Raises:
            ConfigurationError: If rendering or writing fails.
        """
        self._render_config_file(self.AUTHORED_FILE, config_dir,
                                 self._variables(router_name, kwargs))
        frr_conf_path = os.path.join(config_dir, self.AUTHORED_FILE)
        try:
            os.chmod(frr_conf_path, 0o640)
        except OSError as e:
            logger.warning(f"Failed to set permissions on {frr_conf_path}: {e}")

    @staticmethod
    def _variables(router_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Template variables with their defaults filled in."""
        variables = {
            'name': router_name,
            'daemons': [],
            'frr_config': '',
            'rpki': rpki_module_installed(),
        }
        variables.update(kwargs)
        return variables
