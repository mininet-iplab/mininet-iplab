"""Centralized logging configuration for mnIPLab (Mininet-IPLab).

This module provides structured logging with hierarchical loggers, configurable
log levels, and consistent formatting across all mnIPLab components.

Usage:
    # In your module
    from mniplab.logging import get_logger
    
    logger = get_logger(__name__)
    logger.info('Network started')
    logger.debug('Router r1 configuration: %s', config)
    logger.error('Failed to start FRR: %s', error)

Environment Variables:
    MNIPLAB_LOG_LEVEL: Default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    MNIPLAB_LOG_FILE: Optional log file path
    MNIPLAB_LOG_FORMAT: Custom log format string
"""

import logging
import sys
import os
from typing import Optional, Union
from logging.handlers import RotatingFileHandler

# Default log format
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# Color codes for terminal output
COLORS = {
    'DEBUG': '\033[0;36m',      # Cyan
    'INFO': '\033[0;32m',       # Green
    'WARNING': '\033[1;33m',    # Yellow
    'ERROR': '\033[0;31m',      # Red
    'CRITICAL': '\033[1;31m',   # Bold Red
    'RESET': '\033[0m',         # Reset
}


class ColoredFormatter(logging.Formatter):
    """Formatter that adds color to terminal output based on log level."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with color codes for terminal output.
        
        Args:
            record: Log record to format
            
        Returns:
            Formatted log message with color codes
        """
        # Add color to levelname
        if record.levelname in COLORS:
            colored_levelname = f"{COLORS[record.levelname]}{record.levelname}{COLORS['RESET']}"
            record.levelname = colored_levelname
        
        return super().format(record)


class MnIPLabLogger:
    """Centralized logger manager for mnIPLab.
    
    This class manages all loggers in the mnIPLab hierarchy and provides
    methods to configure logging behavior globally.
    
    Attributes:
        root_logger: The root logger for mnIPLab (mniplab.*)
        _initialized: Whether logging has been initialized
        _handlers: List of handlers attached to root logger
    """
    
    def __init__(self):
        """Initialize the logger manager."""
        self.root_logger = logging.getLogger('mniplab')
        self._initialized = False
        self._handlers = []
    
    def setup(
        self,
        level: Optional[Union[str, int]] = None,
        log_file: Optional[str] = None,
        log_format: Optional[str] = None,
        use_colors: bool = True,
        log_to_stdout: bool = True,
        propagate_to_root: bool = False,
        force: bool = False,
        root_logger: bool = False
    ) -> None:
        """Configure mnIPLab logging system.
        
        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) or logging constant.
                   Defaults to MNIPLAB_LOG_LEVEL env var or INFO.
            log_file: Optional file path for logging. If provided, logs are written to file.
                      Defaults to MNIPLAB_LOG_FILE env var.
            log_format: Custom log format string. Defaults to MNIPLAB_LOG_FORMAT env var
                       or DEFAULT_LOG_FORMAT.
            use_colors: Enable colored output for console (default: True)
            log_to_stdout: Enable output to stdout/console (default: True)
            propagate_to_root: Propagate logs to root logger (default: False)
            force: Force re-initialization even if already setup (default: False)
            root_logger: Attach handlers to the global root logger instead of 'mniplab' (default: False)
        """
        # Select target logger
        target_logger = logging.getLogger() if root_logger else self.root_logger
        
        # Prevent double initialization unless forced
        if self._initialized and not force:
            return
        
        # Clear existing handlers from potential targets if re-initializing
        if force:
            # More aggressive cleanup across known loggers
            # Note: We skip 'mininet' here if we want to preserve its own console handling
            for log_name in ['', 'mniplab', 'uvicorn', 'uvicorn.error']:
                l = logging.getLogger(log_name)
                for h in l.handlers[:]:
                    l.removeHandler(h)
                    try:
                        h.close()
                    except (OSError, ValueError, RuntimeError):
                        pass
            self._handlers.clear()
        
        # Determine log level
        if level is None:
            level = os.getenv('MNIPLAB_LOG_LEVEL', 'INFO').upper()
        
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        
        # Determine log file
        if log_file is None:
            log_file = os.getenv('MNIPLAB_LOG_FILE')
        
        # Determine log format
        if log_format is None:
            log_format = os.getenv('MNIPLAB_LOG_FORMAT', DEFAULT_LOG_FORMAT)
        
        # Configure target logger
        target_logger.setLevel(level)
        if root_logger:
            # When using root logger, ensure mniplab logger propagates to it
            self.root_logger.propagate = True
            # Also ensure mininet and uvicorn propagate to the root to reach the file handler
            logging.getLogger('mininet').propagate = True
            logging.getLogger('uvicorn').propagate = True
        else:
            self.root_logger.propagate = propagate_to_root
        
        # Console handler with colors
        if log_to_stdout:
            # Check if we already have a StreamHandler to stdout to avoid duplicates
            has_stdout = any(
                isinstance(h, logging.StreamHandler) and h.stream == sys.stdout 
                for h in target_logger.handlers
            )
            if not has_stdout:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(level)
                
                if use_colors and sys.stdout.isatty():
                    console_formatter: logging.Formatter = ColoredFormatter(log_format, datefmt=DEFAULT_DATE_FORMAT)
                else:
                    console_formatter = logging.Formatter(log_format, datefmt=DEFAULT_DATE_FORMAT)
                
                console_handler.setFormatter(console_formatter)
                target_logger.addHandler(console_handler)
                self._handlers.append(console_handler)
        
        # File handler (if specified)
        if log_file:
            # Check if we already have this file handler to avoid duplicates
            has_file = any(
                isinstance(h, RotatingFileHandler) and h.baseFilename == os.path.abspath(log_file)
                for h in target_logger.handlers
            )
            if not has_file:
                # Create rotating file handler (10MB max, 5 backups)
                file_handler = RotatingFileHandler(
                    log_file,
                    maxBytes=10 * 1024 * 1024,  # 10MB
                    backupCount=5
                )
                file_handler.setLevel(level)
                file_formatter = logging.Formatter(log_format, datefmt=DEFAULT_DATE_FORMAT)
                file_handler.setFormatter(file_formatter)
                target_logger.addHandler(file_handler)
                self._handlers.append(file_handler)
        
        self._initialized = True
        
        # Apply the level to all child loggers that might already exist
        self.set_level(level)
    
    def set_level(self, level: Union[str, int]) -> None:
        """Change log level for all mnIPLab loggers.
        
        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) or logging constant
        """
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        
        self.root_logger.setLevel(level)
        for handler in self._handlers:
            handler.setLevel(level)
            
        # Also update any already created child loggers
        # Since MnIPLabLogger.get_logger() sets explicit levels, we must update them
        import logging as log_module
        mniplab_root_name = self.root_logger.name
        for name in log_module.root.manager.loggerDict:  # pylint: disable=no-member
            if name.startswith(f"{mniplab_root_name}."):
                logger = log_module.getLogger(name)
                logger.setLevel(level)
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get a logger for a specific module.
        
        Creates a child logger under the mniplab hierarchy. If the name doesn't
        start with 'mniplab.', it will be prefixed automatically.
        
        Args:
            name: Logger name (e.g., 'mniplab.net', 'mniplab.webserver.api')
            
        Returns:
            Configured logger instance
            
        Example:
            >>> logger = get_logger('mniplab.net')
            >>> logger.info('Router r1 started')
        """
        # Initialize if not already done
        if not self._initialized:
            self.setup()
        
        if not name.startswith('mniplab.') and name != 'mniplab':
            name = f'mniplab.{name}'
        
        logger = logging.getLogger(name)
        # Ensure the logger uses the mnIPLab root level to stay consistent
        # and avoid interference from Mininet's default levels (e.g., INFO=25)
        logger.setLevel(self.root_logger.level)
        return logger
    
    def disable_mininet_logging(self) -> None:
        """Reduce verbosity of Mininet's own logging.
        
        Mininet uses its own logging with info(), error(), etc. which can be
        noisy. This method reduces Mininet logging to WARNING level only.
        """
        mininet_logger = logging.getLogger('mininet')
        mininet_logger.setLevel(logging.WARNING)


# Global logger manager instance
_logger_manager = MnIPLabLogger()


def setup_logging(
    level: Optional[Union[str, int]] = None,
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
    use_colors: bool = True,
    log_to_stdout: bool = True,
    force: bool = False,
    root_logger: bool = False
) -> None:
    """Configure mnIPLab logging system (convenience function).
    
    This is a convenience wrapper around MnIPLabLogger.setup().
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging
        log_format: Custom log format string
        use_colors: Enable colored output for console
        log_to_stdout: Enable output to stdout/console
        force: Force re-initialization even if already setup
        root_logger: Attach handlers to the global root logger instead of 'mniplab'
        
    Example:
        >>> from mniplab.logging import setup_logging
        >>> setup_logging(level='DEBUG', log_file='/tmp/mniplab.log')
    """
    _logger_manager.setup(
        level=level,
        log_file=log_file,
        log_format=log_format,
        use_colors=use_colors,
        log_to_stdout=log_to_stdout,
        force=force,
        root_logger=root_logger
    )


def get_logger(name: str = __name__) -> logging.Logger:
    """Get a logger for the specified module (convenience function).
    
    This is a convenience wrapper around MnIPLabLogger.get_logger().
    
    Args:
        name: Logger name (module name recommended, e.g., __name__)
        
    Returns:
        Configured logger instance
        
    Example:
        >>> from mniplab.logging import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info('Starting network')
    """
    return _logger_manager.get_logger(name)


def set_log_level(level: Union[str, int]) -> None:
    """Change log level for all mnIPLab loggers (convenience function).
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
    Example:
        >>> from mniplab.logging import set_log_level
        >>> set_log_level('DEBUG')
    """
    _logger_manager.set_level(level)


def disable_mininet_logging() -> None:
    """Reduce verbosity of Mininet's logging (convenience function).
    
    Example:
        >>> from mniplab.logging import disable_mininet_logging
        >>> disable_mininet_logging()
    """
    _logger_manager.disable_mininet_logging()


# Initialize with default settings on import
_logger_manager.setup()
