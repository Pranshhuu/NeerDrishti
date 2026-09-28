"""
API v1 router for FlowSight (Phase 1 + Phase 2A)

Aggregates the endpoint routers under the versioned API prefix. This module
wires routers together and holds no endpoint logic of its own.

Prefix composition:
    This router carries API_VERSION_PREFIX ("/api/v1"). Each endpoint module
    declares its own section prefix ("/system", "/terrain", "/processing",
    "/weather"), so prefixes are never repeated here and each path is defined
    in exactly one place.

    main.py must therefore include this router with no additional prefix.
    Passing one would produce "/api/v1/api/v1/...".

Resulting routes:
    GET  /api/v1/system/health
    GET  /api/v1/system/status
    GET  /api/v1/terrain
    GET  /api/v1/terrain/info
    POST /api/v1/terrain/verify
    POST /api/v1/processing/terrain
    GET  /api/v1/weather
    GET  /api/v1/weather/current
    GET  /api/v1/weather/forecast
"""

from fastapi import APIRouter

from app.core.constants import API_VERSION_PREFIX
from app.api.v1.endpoints import flood_risk, processing, runoff, system, terrain, weather

router = APIRouter(prefix=API_VERSION_PREFIX)

# Tags are declared on each endpoint router, so none are added here.
router.include_router(system.router)
router.include_router(terrain.router)
router.include_router(processing.router)
router.include_router(runoff.router)
router.include_router(weather.router)
router.include_router(flood_risk.router)