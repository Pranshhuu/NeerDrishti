"""
Centralized logging configuration for FlowSight Phase 1

Provides utilities for configuring logging for:
- FastAPI application startup and requests
- CLI terrain processing pipeline
- GDAL/WhiteboxTools command execution
- Validation and diagnostics

This module provides logging infrastructure only. It does not:
- Auto-configure on import
- Create log files automatically
- Handle application-specific logging logic
- Swallow exceptions

Usage:
    from app.core.logging import setup_logging, get_logger
    
    # In main application (main.py)
    logger = setup_logging(level="INFO")
    
    # In modules
    logger = get_logger(__name__)
    logger.info("Processing started")

The setup_logging() function is safe to call multiple times and will not
create duplicate handlers. Handlers created by this module are identified
by a private marker attribute.
"""

import logging
from typing import Optional, Set

# Valid logging levels - used for explicit validation
_VALID_LOG_LEVELS: Set[str] = {
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
}

# Marker attribute name used to identify handlers created by this module
_FLOWSIGHT_HANDLER_MARKER = "_flowsight_console_handler"


def setup_logging(
    level: str = "INFO",
    name: Optional[str] = None,
) -> logging.Logger:
    """
    Configure logging with console output.
    
    Sets up a logger with stream handler and standard formatter.
    Safe to call multiple times - does not create duplicate handlers.
    Handlers created by this module are identified by a private marker
    to distinguish them from handlers added by other code.
    
    Args:
        level: Logging level as string (case-insensitive).
            Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL
            Default: INFO
        name: Logger name. If None, configures root logger.
            Typically None for application root, or __name__ for module loggers.
    
    Returns:
        Configured logger instance
    
    Raises:
        ValueError: If level is not a valid logging level string
    
    Example:
        >>> logger = setup_logging(level="INFO")
        >>> logger.info("Application started")
        2026-09-05 23:34:56 - root - INFO - Application started
        
        >>> logger = setup_logging(level="DEBUG")  # Safe to call again
        >>> logger.debug("Debug output")
    """
    # Validate log level explicitly
    level_upper = level.upper()
    if level_upper not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid log level '{level}'. "
            f"Valid levels: {', '.join(sorted(_VALID_LOG_LEVELS))}"
        )
    
    # Convert validated level string to logging level constant
    log_level = getattr(logging, level_upper)
    
    # Get or create logger
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # Check if FlowSight console handler already exists for this logger
    # This avoids duplicate handlers when setup_logging() is called multiple times
    for handler in logger.handlers:
        if hasattr(handler, _FLOWSIGHT_HANDLER_MARKER):
            # Handler already exists and was created by this module
            # Just update the level and return
            handler.setLevel(log_level)
            logger.setLevel(log_level)
            return logger
    
    # No FlowSight handler found, safe to create one
    
    # Create console handler (streams to stderr by default)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    
    # Mark this handler as created by FlowSight to identify it later
    setattr(console_handler, _FLOWSIGHT_HANDLER_MARKER, True)
    
    # Create formatter with timestamp, logger name, level, and message
    # Format is readable for both interactive development and CLI processing
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger by name.
    
    Returns an existing logger instance or creates a new one with the given name.
    Does not modify logging configuration - call setup_logging() first to
    configure the root logger and set logging level.
    
    Args:
        name: Logger name. Typically __name__ from the calling module.
    
    Returns:
        Logger instance
    
    Example:
        >>> from app.core.logging import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.debug("Debug information")
    """
    return logging.getLogger(name)