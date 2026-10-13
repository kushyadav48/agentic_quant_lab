"""Real production assets in the build job; source-only suite uses focused fixtures."""
import json
from pathlib import Path
import re
import pytest
from fastapi.testclient import TestClient
from quantlab.api import create_app
from quantlab.api.static import validate_dashboard
from tests.api.helpers import READ, ROOT, settings


def test_real_production_build():
    directory = Path(__file__).resolve().parents[2] / "frontend/dist"
    if not directory.exists():
        pytest.skip("Production assets are built and checked by the dedicated frontend CI job")
    app = create_app(settings(frontend_path=directory))
    _, files = validate_dashboard(directory, app.openapi())
    with TestClient(app, base_url="http://localhost") as c:
        page = c.get("/")
        assert page.status_code == 200 and 'id="root"' in page.text
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
        assert assets
        for asset in assets:
            response = c.get(asset)
            assert response.status_code == 200 and response.headers["x-content-type-options"] == "nosniff"
        assert c.get(ROOT + "/ready", headers=READ).status_code == 200
        assert c.get(ROOT + "/capabilities").status_code == 401
        assert c.get("/openapi.json", headers=READ).json() == json.loads(
            (directory.parent / "contracts/openapi.json").read_text())
    for name in files:
        data = (directory / name).read_bytes()
        assert b"GEMINI_API_KEY=" not in data and b"QUANTLAB_API_OPERATOR_TOKEN=" not in data
