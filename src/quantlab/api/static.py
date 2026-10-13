"""Explicit production assets only. No SPA fallback over API or private files."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from starlette.responses import FileResponse


def contract_digest(schema):
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def validate_dashboard(directory: Path, schema):
    try:
        if directory.is_symlink():
            raise ValueError()
        directory = directory.resolve(strict=True)
        manifest = json.loads((directory / "quantlab-build.json").read_text(encoding="utf-8"))
        if set(manifest) != {"schema", "contract_sha256", "files"} or manifest["schema"] != 1:
            raise ValueError()
        if manifest["contract_sha256"] != contract_digest(schema):
            raise ValueError()
        files = manifest["files"]
        if not isinstance(files, dict) or "index.html" not in files or not files:
            raise ValueError()
        for name, digest in files.items():
            parts = PurePosixPath(name).parts
            if str(PurePosixPath(name)) != name:
                raise ValueError()
            if (name != "index.html" and (not name.startswith("assets/")
                    or not name.endswith((".js", ".css", ".svg", ".png", ".woff2")))):
                raise ValueError()
            if "\\" in name or any(p in (".", "..") or p.startswith(".") for p in parts):
                raise ValueError()
            file = directory / name
            if any((directory.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
                raise ValueError()
            if not file.resolve(strict=True).is_relative_to(directory):
                raise ValueError()
            if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise ValueError()
        actual = {f.relative_to(directory).as_posix() for f in directory.rglob("*") if f.is_file()}
        if actual != set(files) | {"quantlab-build.json"}:
            raise ValueError()
        return directory, files
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError("Invalid dashboard build; rebuild assets and verify API contracts.") from None


def mount_dashboard(app, directory):
    directory, files = validate_dashboard(directory, app.openapi())

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(directory / "index.html")

    @app.get("/assets/{name:path}", include_in_schema=False)
    async def dashboard_asset(name: str):
        path = "assets/" + name
        if path not in files:
            raise HTTPException(404)
        return FileResponse(directory / path)
