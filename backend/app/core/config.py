"""
Application configuration using Pydantic Settings.
Loads environment variables for CORS, debug mode, and other settings.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """

    # Application
    APP_NAME: str = "FlowSight"
    DEBUG: bool = True

    # CORS Configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Weather (Phase 2A) - live meteorological ingestion via Open-Meteo.
    # Non-secret configuration only: Open-Meteo's free forecast endpoint
    # requires no API key for this non-commercial prototype. IMD will be
    # added as the production India-specific source once API access is
    # approved; these settings will gain an IMD_* counterpart at that time
    # rather than replacing this block.
    WEATHER_PROVIDER: str = "open_meteo"
    WEATHER_API_BASE_URL: str = "https://api.open-meteo.com/v1/forecast"
    WEATHER_DEFAULT_LATITUDE: float = 19.0760
    WEATHER_DEFAULT_LONGITUDE: float = 72.8777
    WEATHER_DEFAULT_TIMEZONE: str = "Asia/Kolkata"
    WEATHER_REQUEST_TIMEOUT_SECONDS: float = 10.0

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()