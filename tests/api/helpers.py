from contextlib import contextmanager

from fastapi.testclient import TestClient
from pydantic import SecretStr
from quantlab.api import APISettings, create_app
from tests.mcp.test_operations import submission

OPERATOR = "operator-fixture-credential-" + "a" * 32
READER = "reader-fixture-credential-" + "b" * 32
AUTH = {"Authorization": f"Bearer {OPERATOR}"}
READ = {"Authorization": f"Bearer {READER}"}
ROOT = "/api/v1"
OPS = ROOT + "/research/operations"


def settings(**changes):
    return APISettings(operator_token=SecretStr(OPERATOR), reader_token=SecretStr(READER), **changes)


@contextmanager
def client(*, factory=None, config=None):
    app = create_app(config or settings(), services_factory=factory)
    with TestClient(app, base_url="http://localhost") as connection:
        yield connection


def admit(connection, **kwargs):
    response = connection.post(OPS, json=submission(**kwargs).model_dump(mode="json"), headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()["operation_id"]
