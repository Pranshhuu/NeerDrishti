"""
FlowSight Backend - Main Application
Urban Flood Nowcasting System
Phase 1 - Geospatial Foundation
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import router as v1_router


# Initialize FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description="Urban flood nowcasting and decision-support platform",
    version="0.1.0",
    debug=settings.DEBUG,
)


# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include v1 API router
app.include_router(v1_router)


@app.get("/")
async def root():
    """
    Root endpoint.
    Confirms that the FlowSight API is running.
    """
    return {"message": "FlowSight API is running"}


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Used for monitoring and container orchestration.
    Returns service status and name.
    """
    return {
        "status": "healthy",
        "service": "flowsight-backend",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=settings.DEBUG,
    )