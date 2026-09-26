"""
System service for FlowSight (Phase 1)

Provides application-level health and status information for the FlowSight
backend.

Boundaries:
- Environment detection is delegated entirely to app.core.environment. This
  service does not check for GDAL, WhiteboxTools, rasterio, pyproj, or numpy
  itself; it reuses EnvironmentChecker's result and maps it to an application
  status.
- Terrain access belongs to app.services.terrain_service; terrain processing
  belongs to app.services.processing_service. Nothing here reads rasters or
  invokes geospatial tools.
- HTTP concerns belong to the API layer. Every public method returns plain
  JSON-serializable structures, never live checker objects.

Status mapping reflects a real operating condition of this system: the API can
serve requests while the geospatial toolchain is missing. That case is reported
as degraded rather than healthy, so an operator can see that terrain processing
is blocked before attempting a run.

Usage:
    from app.services.system_service import SystemService

    service = SystemService()
    health = service.get_health()
    status = service.get_status()
"""

from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.constants import API_VERSION, PROJECT_FULL_NAME, PROJECT_NAME
from app.core.environment import EnvironmentStatus, get_environment_report
from app.core.logging import get_logger

logger = get_logger(__name__)

# Application-level status values reported by this service.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"


class SystemServiceError(Exception):
    """
    Raised when system status could not be determined.

    Environment inspection is expected to report missing tools rather than
    fail, so this signals that the check itself broke down.
    """

    pass


class SystemService:
    """
    Application-level facade for system health and status.

    Construction is cheap: no environment inspection happens until a method is
    called, so instantiating the service in a request path costs nothing.
    Each call re-reads the environment, since a dependency can be installed or
    removed while the process is running.
    """

    def __init__(self, service_name: str | None = None) -> None:
        """
        Create a system service.

        Args:
            service_name: Optional name to report as the service identity.
                Defaults to the project name from constants.
        """
        self.service_name: str = service_name or PROJECT_NAME

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    def get_environment_report(self) -> dict[str, Any]:
        """
        Return the current environment report as a plain dictionary.

        The underlying EnvironmentStatus dataclass is converted so callers
        receive only serializable values.

        Returns:
            Dictionary of every environment field reported by
            app.core.environment.

        Raises:
            SystemServiceError: If the environment check could not run.
        """
        status = self._check_environment()
        return self._environment_to_dict(status)

    # ------------------------------------------------------------------
    # Health and status
    # ------------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """
        Return a compact health summary suitable for a liveness endpoint.

        Kept deliberately small: a health check is polled frequently, so it
        reports the verdict without the full component breakdown.

        Returns:
            Dictionary with 'status', 'service', 'version', and
            'environment_available'.

        Raises:
            SystemServiceError: If the environment check could not run.
        """
        status = self._check_environment()

        return {
            "status": self._map_status(status),
            "service": self.service_name,
            "version": API_VERSION,
            "environment_available": status.all_available,
        }

    def get_status(self) -> dict[str, Any]:
        """
        Return a detailed application status report.

        Unlike get_health, this includes the per-component breakdown and the
        list of missing tools, so an operator can see exactly what is blocking
        terrain processing.

        Returns:
            Dictionary describing the service, its status, and the environment
            components it depends on.

        Raises:
            SystemServiceError: If the environment check could not run.
        """
        status = self._check_environment()
        overall = self._map_status(status)

        report = {
            "status": overall,
            "service": self.service_name,
            "service_full_name": PROJECT_FULL_NAME,
            "version": API_VERSION,
            "environment": {
                "available": status.all_available,
                "python_version": status.python_version_str,
                "missing_tools": list(status.missing_tools),
            },
            "components": self._component_summary(status),
        }

        if not status.all_available:
            report["environment"]["message"] = (
                "Terrain processing is unavailable until the missing tools are "
                "installed. The API remains operational."
            )

        logger.debug(
            "System status: %s (environment_available=%s)",
            overall,
            status.all_available,
        )
        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_environment(self) -> EnvironmentStatus:
        """
        Run the environment check, translating unexpected failures.

        Args:
            None.

        Returns:
            The EnvironmentStatus reported by app.core.environment.

        Raises:
            SystemServiceError: If the check raised instead of reporting.
        """
        try:
            return get_environment_report()
        except Exception as exc:
            logger.exception("Environment check failed")
            raise SystemServiceError(
                f"Cannot determine environment status: {exc}"
            ) from exc

    @staticmethod
    def _map_status(status: EnvironmentStatus) -> str:
        """
        Map an environment report onto an application status value.

        The distinction that matters here is between a backend that can serve
        requests with reduced capability and one that cannot function at all.
        Missing geospatial tools block processing but leave the API working,
        so they yield 'degraded'. Only a missing core dependency, without
        which the data layer cannot operate at all, yields 'unavailable'.

        Args:
            status: The environment report.

        Returns:
            One of 'ok', 'degraded', or 'unavailable'.
        """
        if status.all_available:
            return STATUS_OK

        # numpy underpins every raster read and validation path, so its
        # absence is not a partial capability loss.
        if not status.numpy_available:
            return STATUS_UNAVAILABLE

        return STATUS_DEGRADED

    @staticmethod
    def _component_summary(status: EnvironmentStatus) -> dict[str, Any]:
        """
        Summarize individual environment components.

        Args:
            status: The environment report.

        Returns:
            Mapping of component name to its availability and reported version.
        """
        return {
            "gdal_python": {
                "available": status.gdal_python_available,
                "version": status.gdal_python_version,
            },
            "gdal_cli": {
                "available": status.gdal_cli_available,
                "version": status.gdal_cli_version,
            },
            "whitebox": {
                "available": status.whitebox_available,
                "version": status.whitebox_version,
            },
            "rasterio": {
                "available": status.rasterio_available,
                "version": status.rasterio_version,
            },
            "pyproj": {
                "available": status.pyproj_available,
                "version": status.pyproj_version,
            },
            "numpy": {
                "available": status.numpy_available,
                "version": status.numpy_version,
            },
        }

    @staticmethod
    def _environment_to_dict(status: EnvironmentStatus) -> dict[str, Any]:
        """
        Convert an environment report into a plain dictionary.

        Args:
            status: The environment report.

        Returns:
            Dictionary of the report's fields, with sequences copied so callers
            cannot mutate the original.
        """
        if is_dataclass(status):
            data = asdict(status)
        else:
            data = dict(vars(status))

        missing = data.get("missing_tools")
        if missing is not None:
            data["missing_tools"] = list(missing)

        return data