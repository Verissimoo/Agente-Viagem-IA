"""Fixtures comuns aos contract tests."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def future_date() -> str:
    """Data 15 dias à frente — distante o suficiente pra todos os provedores
    terem inventário."""
    return (dt.date.today() + dt.timedelta(days=15)).isoformat()


@pytest.fixture
def return_date() -> str:
    return (dt.date.today() + dt.timedelta(days=22)).isoformat()
