"""
Static application constants for FlowSight Phase 1

This module contains genuinely static constants used throughout the application.
These values are fixed application-level identifiers that do not change through
configuration or deployment.

IMPORTANT: This module does NOT contain:
- CRS definitions (see data/CRS_DEFINITIONS.yaml)
- Processing parameters (see data/PROCESSING_CONFIG.yaml)
- File paths or directories (see configuration files)
- Environment-specific values (see backend/app/core/config.py)
- API port or CORS settings (see backend/app/core/config.py)
- Raster specifications or thresholds (see data/PROCESSING_CONFIG.yaml)

Single source of truth for application identification and basic routing constants.
"""

# Application identification
PROJECT_NAME = "FlowSight"
PROJECT_FULL_NAME = "FlowSight - Urban Flood Nowcasting and Decision Support System"

# API routing
API_VERSION = "1"
API_VERSION_PREFIX = "/api/v1"

# Supported terrain data sources
# Used to identify and describe terrain product types in API responses and validation
TERRAIN_SOURCE_COPERNICUS_GLO30 = "copernicus_glo30"

SUPPORTED_TERRAIN_SOURCES = {
    TERRAIN_SOURCE_COPERNICUS_GLO30: "Copernicus Digital Surface Model GLO-30",
}