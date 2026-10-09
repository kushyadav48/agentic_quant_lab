import json
from pathlib import Path

from quantlab.api import create_app
from .helpers import AUTH, ROOT, OPS, client, settings


def test_openapi_routes_auth_errors_discriminators_and_resolved_refs():
    with client() as c:
        response = c.get("/openapi.json", headers=AUTH)
        assert response.status_code == 200
        schema = response.json()
    assert schema["openapi"].startswith("3.1") and len(schema["paths"]) == 14
    paths, definitions = schema["paths"], schema["components"]["schemas"]
    assert paths[OPS]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/OperationSubmission")
    assert definitions["OperationSubmission"]["properties"]["operation"]["discriminator"]["propertyName"] == "kind"
    assert len(definitions["OperationSubmission"]["properties"]["operation"]["oneOf"]) == 9
    result_schema = paths[OPS+"/{operation_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert len(result_schema["oneOf"]) == 9 and result_schema["discriminator"]["propertyName"] == "kind"
    operation_ids = []
    for path, methods in paths.items():
        for method, route in methods.items():
            operation_ids.append(route["operationId"])
            if path != ROOT+"/health":
                assert route["security"] == [{"LocalBearer": []}]
            for code in ("401", "403", "413", "422", "500", "503"):
                assert route["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert len(operation_ids) == len(set(operation_ids))
    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/components/schemas/")
                assert value["$ref"].split("/")[-1] in definitions
            if "pattern" in value:
                assert r"\z" not in value["pattern"]
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(schema)
    assert "Decimal" not in schema["components"].get("securitySchemes", {})
    assert "operator-fixture-credential" not in json.dumps(schema)


def test_core_has_no_fastapi_dependency_and_app_factory_has_no_resource_side_effects():
    package = Path(__file__).parents[2]/"src"/"quantlab"
    for source in package.rglob("*.py"):
        if "api" not in source.relative_to(package).parts:
            assert "fastapi" not in source.read_text(encoding="utf-8").lower()
    app = create_app(settings())
    assert not app.debug and not app.state.ready
    assert not hasattr(app.state, "lane")
