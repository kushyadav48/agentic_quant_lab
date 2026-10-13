import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest
from scripts.package_release import build_inputs, prepare, verify_bundle
from scripts.build_dashboard import stamp


def repository(tmp_path):
    root = tmp_path / "release"
    (root / "src/quantlab").mkdir(parents=True)
    (root / "src/quantlab/__init__.py").write_text('"""Fixture package."""\n')
    (root / "pyproject.toml").write_text('[project]\nversion="0.1.0"\n')
    (root / ".gitignore").write_text('dist/\nfrontend/dist/\n')
    (root / "docs").mkdir()
    (root / "docs/deployment.md").write_text("Fixture install instructions")
    (root / "frontend/contracts").mkdir(parents=True)
    (root / "frontend/contracts/openapi.json").write_text('{"openapi":"fixture"}')
    directory = root / "frontend/dist"
    directory.mkdir()
    (directory / "index.html").write_text("fixture")
    stamp(directory, {"openapi": "fixture"})
    (root / "dist").mkdir()
    wheel = root / "dist/fixture-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("quantlab/__init__.py", (root / "src/quantlab/__init__.py").read_bytes())
        archive.writestr("fixture-0.1.0.dist-info/METADATA", "Name: fixture\nVersion: 0.1.0\n")
    for args in (["init"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root, wheel


def test_clean_candidate_contents_hashes_and_unapproved_status(tmp_path):
    root, wheel = repository(tmp_path)
    target = prepare(root, "https://example.invalid/test-ci-record")
    manifest = verify_bundle(target)
    assert manifest["version"] == "0.1.0" and manifest["release_approved"] is False
    assert "wheels/" + wheel.name in manifest["files"]
    assert "dashboard/quantlab-build.json" in manifest["files"]
    assert target.with_suffix(".zip.sha256").read_text().startswith(hashlib.sha256(target.read_bytes()).hexdigest())
    with zipfile.ZipFile(target) as archive:
        with zipfile.ZipFile(io.BytesIO(archive.read("source.zip"))) as source:
            assert "src/quantlab/__init__.py" in source.namelist()
            assert not any(n.startswith("dist/") for n in source.namelist())


@pytest.mark.parametrize("fault", ["dirty", "untracked", "asset", "wheel", "secret"])
def test_release_rejects_unreviewable_or_corrupt_inputs(tmp_path, fault):
    root, wheel = repository(tmp_path)
    if fault == "dirty": (root / "pyproject.toml").write_text("changed")
    if fault == "untracked": (root / "private.txt").write_text("unreviewed")
    if fault == "asset": (root / "frontend/dist/index.html").write_text("changed")
    if fault == "wheel": wheel.write_bytes(b"corrupt")
    if fault == "secret":
        (root / ".env").write_text("GEMINI_API_KEY=private")
        subprocess.run(["git", "add", ".env"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "secret-fixture"], cwd=root, check=True, capture_output=True)
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        prepare(root, "")


def test_candidate_verifier_rejects_altered_payload(tmp_path):
    target = tmp_path / "broken.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("source.zip", b"tampered")
        archive.writestr("manifest.json", json.dumps({"files": {"source.zip": "0" * 64}}))
    with pytest.raises(ValueError, match="integrity"):
        verify_bundle(target)


def test_release_rejects_unexpected_wheel_payload(tmp_path):
    root, wheel = repository(tmp_path)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("credentials.json", b"not part of the Python project")
    with pytest.raises(ValueError):
        prepare(root, "")


@pytest.mark.parametrize("location", ["dashboard", "wheel_metadata"])
def test_release_rejects_known_credential_in_generated_payload(tmp_path, location):
    root, wheel = repository(tmp_path)
    # Synthetic format canary; never a real provider credential.
    credential = b"AIza" + b"a" * 35
    if location == "dashboard":
        directory = root / "frontend/dist"
        (directory / "index.html").write_bytes(b"<script>const key='" + credential + b"';</script>")
        # Bypass the local stamping guard to test packaging independently.
        path = directory / "quantlab-build.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["index.html"] = hashlib.sha256((directory / "index.html").read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest))
    else:
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("fixture-0.1.0.dist-info/description.txt", credential)
    with pytest.raises(ValueError):
        prepare(root, "")
