"""Stamp already-built Vite assets with checked API contract and file hashes."""
import hashlib
import json
import sys
from pathlib import Path
from pydantic import SecretStr
from quantlab.api import APISettings, create_app
from quantlab.api.static import contract_digest, validate_dashboard

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_secrets import inspect


def stamp(directory, schema):
    files = {}
    for file in directory.rglob("*"):
        if not file.is_file() or file.name == "quantlab-build.json":
            continue
        name, data = file.relative_to(directory).as_posix(), file.read_bytes()
        if inspect(name, data):
            raise ValueError("Dashboard payload rejected by secret/file guard.")
        files[name] = hashlib.sha256(data).hexdigest()
    manifest = {"schema": 1, "contract_sha256": contract_digest(schema), "files": files}
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    if inspect("quantlab-build.json", manifest_text.encode("utf-8")):
        raise ValueError("Dashboard manifest rejected by secret guard.")
    (directory / "quantlab-build.json").write_text(manifest_text, encoding="utf-8")
    validate_dashboard(directory, schema)


def main():
    schema = create_app(APISettings(operator_token=SecretStr("build-only-" + "x" * 32))).openapi()
    checked = json.loads((ROOT / "frontend/contracts/openapi.json").read_text(encoding="utf-8"))
    if schema != checked:
        raise SystemExit("OpenAPI drift; review and regenerate frontend contracts before building.")
    stamp(ROOT / "frontend/dist", schema)
    print("Dashboard file integrity and API contract verified.")


if __name__ == "__main__":
    main()
