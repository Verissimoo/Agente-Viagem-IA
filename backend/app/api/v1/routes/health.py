"""GET /api/v1/health — heartbeat para load balancer / monitoring."""
from fastapi import APIRouter

from backend.app.api.v1.schemas.search_response import HealthResponseDTO
from backend.app.services.search_orchestrator import _ADAPTER_MAP

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponseDTO)
def health() -> HealthResponseDTO:
    return HealthResponseDTO(
        status="ok",
        version="1.0.0",
        adapters=sorted(_ADAPTER_MAP.keys()),
    )
