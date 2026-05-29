"""Router HTTP do produto chat. Isolado de /search e demais rotas do gerencial."""
from backend.app.api.v1.chat.routes import router

__all__ = ["router"]
