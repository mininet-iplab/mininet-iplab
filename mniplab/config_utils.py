"""Base configuration builder for template-based config generation.

This module provides a reusable base class for building configuration files
from Mako templates. Used by both FRRConfigBuilder and ExaBGPConfigBuilder
to eliminate template rendering duplication.
"""

import os
from abc import ABC, abstractmethod
from mako.template import Template
from mako.exceptions import MakoException
from typing import Dict, Any, Optional
from mniplab.errors import ConfigurationError
from mniplab.logging import get_logger
from mniplab.utils import ensure_directory

logger = get_logger(__name__)


class ConfigBuilder(ABC):
    """Base class for configuration builders using Mako templates.
    
    Provides common template rendering, validation, and file handling logic
    for all configuration builders (FRR, ExaBGP, etc.).
    
    Subclasses must define:
    - REQUIRED_VARS: Dict of required template variable names and types
    - _get_template_dir(): Path to template directory if not provided
    """
    
    # Required template variables with their types
    # Subclasses MUST override this
    REQUIRED_VARS: Dict[str, Any] = {}
    
    def __init__(self, template_dir: Optional[str] = None):
        """Initialize ConfigBuilder.
        
        Args:
            template_dir: Path to template directory. If None, uses subclass default.
        """
        if template_dir is None:
            template_dir = self._get_template_dir()
        self.template_dir = template_dir
    
    @abstractmethod
    def _get_template_dir(self) -> str:
        """Get default template directory path.
        
        Subclasses must implement this to provide their own default template path.
        
        Returns:
            Path to the template directory
        """
        pass
    
    def validate_vars(self, variables: Dict[str, Any]) -> None:
        """Validate that required template variables are present and correct type.
        
        Args:
            variables: Dictionary of template variables
            
        Raises:
            ConfigurationError: If required variables are missing or have incorrect type
        """
        for var_name, expected_type in self.REQUIRED_VARS.items():
            if var_name not in variables:
                raise ConfigurationError(
                    f"Required template variable '{var_name}' is missing\n"
                    f"Required variables: {', '.join(self.REQUIRED_VARS.keys())}\n"
                    f"Provided variables: {', '.join(variables.keys())}"
                )
            
            if not isinstance(variables[var_name], expected_type):
                raise ConfigurationError(
                    f"Template variable '{var_name}' must be of type {expected_type}, "
                    f"got {type(variables[var_name])}\n"
                    f"Value: {variables[var_name]}"
                )
    
    def render_template(self, template_path: str, variables: Dict[str, Any]) -> str:
        """Render a Mako template with the given variables.
        
        Args:
            template_path: Path to the template file
            variables: Dictionary of template variables
            
        Returns:
            Rendered template content as string
            
        Raises:
            ConfigurationError: If template file doesn't exist, variables invalid, or rendering fails
        """
        if not os.path.exists(template_path):
            raise ConfigurationError(
                f"Template not found: {template_path}\n"
                f"Ensure template directory exists: {self.template_dir}\n"
                f"Template directory contents: {os.listdir(self.template_dir) if os.path.isdir(self.template_dir) else 'NOT FOUND'}"
            )
        
        # Validate required variables
        try:
            self.validate_vars(variables)
        except ConfigurationError:
            raise  # Re-raise ConfigurationError as-is
        
        # Read template file
        try:
            with open(template_path, 'r') as f:
                template_content = f.read()
        except IOError as e:
            raise ConfigurationError(
                f"Failed to read template file: {template_path}\n"
                f"Error: {e}\n"
                f"Check file permissions and disk space."
            )
        
        # Render template
        try:
            template = Template(template_content)
            return template.render(**variables)
        except MakoException as e:
            # Extract useful error info from Mako exception
            raise ConfigurationError(
                f"Template rendering failed: {template_path}\n"
                f"Error: {e}\n"
                f"Variables provided: {list(variables.keys())}\n"
                f"Check template syntax and variable usage."
            )
        except (TypeError, KeyError, NameError, ValueError) as e:
            raise ConfigurationError(
                f"Unexpected error rendering template: {template_path}\n"
                f"Error: {e}"
            ) from e
    
    def _ensure_config_dir(self, config_dir: str, mode: int = 0o755) -> None:
        """Ensure configuration directory exists with proper permissions.
        
        Args:
            config_dir: Path to the configuration directory
            mode: File mode permissions (default: 0o755)
        """
        ensure_directory(config_dir, mode=mode)
