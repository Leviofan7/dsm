import asyncio
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)
response = client.get("/analytics/metrics")
print(response.status_code)
print(response.json())
