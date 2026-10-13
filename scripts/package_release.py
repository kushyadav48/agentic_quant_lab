"""Validate build inputs and prepare a clean-source candidate. Never tag/publish."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_secrets import inspect


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build_inputs(root):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheels = list((root / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Require exactly one built project wheel in dist.")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Require exactly one wheel metadata directory.")
        metadata_prefix = metadata[0].rsplit("/", 1)[0] + "/"
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel paths.")
        for name in names:
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name
                    or str(path) != name.rstrip("/")
                    or not name.startswith(("quantlab/", metadata_prefix))):
                raise ValueError("Unexpected wheel payload.")
            if not name.endswith("/") and inspect(name, archive.read(name)):
                raise ValueError("Wheel payload rejected by secret/file guard.")
        expected = {p.relative_to(root / "src").as_posix(): p.read_bytes()
            for p in (root / "src/quantlab").rglob("*.py")}
        actual = {n: archive.read(n) for n in archive.namelist() if n.startswith("quantlab/")}
        if actual != expected:
            raise ValueError("Wheel sources differ from current project.")
        if len(metadata) != 1 or f"Version: {version}" not in archive.read(metadata[0]).decode().splitlines():
            raise ValueError("Wheel version differs from project.")
    directory = root / "frontend/dist"
    manifest = json.loads((directory / "quantlab-build.json").read_text())
    contract = json.loads((root / "frontend/contracts/openapi.json").read_text())
    contract_hash = sha(json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    if manifest.get("schema") != 1 or manifest.get("contract_sha256") != contract_hash:
        raise ValueError("Dashboard contract differs from checked schema.")
    files = manifest["files"]
    if "index.html" not in files:
        raise ValueError("Dashboard index missing.")
    actual_files = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if actual_files != set(files) | {"quantlab-build.json"}:
        raise ValueError("Unexpected dashboard files.")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (str(path) != name or path.is_absolute() or ".." in path.parts
                or "\\" in name or (name != "index.html" and not name.startswith("assets/"))):
            raise ValueError("Unsafe asset name.")
        file = directory / name
        if file.is_symlink() or not file.resolve().is_relative_to(directory.resolve()) or sha(file.read_bytes()) != digest:
            raise ValueError("Dashboard asset integrity failed.")
        if inspect(name, file.read_bytes()):
            raise ValueError("Dashboard payload rejected by secret/file guard.")
    if inspect("quantlab-build.json", (directory / "quantlab-build.json").read_bytes()):
        raise ValueError("Dashboard manifest rejected by secret guard.")
    return version, wheel, directory


def verify_bundle(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Invalid candidate archive.")
        manifest = json.loads(archive.read("manifest.json"))
        if set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise ValueError("Candidate contents differ from manifest.")
        for name, digest in manifest["files"].items():
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or "\\" in name or sha(archive.read(name)) != digest:
                raise ValueError("Candidate integrity failed.")
        return manifest


def prepare(root, ci_run_url):
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise ValueError("Release preparation requires a clean Git tree, including untracked files.")
    version, wheel, directory = build_inputs(root)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    source = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=root)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        for name in archive.namelist():
            if not name.endswith("/") and inspect(name, archive.read(name)):
                raise ValueError("Release source rejected by secret/file guard.")
    contents = {"source.zip": source, "wheels/" + wheel.name: wheel.read_bytes(),
        "INSTALL.md": (root / "docs/deployment.md").read_bytes()}
    contents.update({"dashboard/" + p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*") if p.is_file()})
    manifest = {"schema": 1, "version": version, "commit": commit,
        "ci_run_url": ci_run_url, "release_approved": False,
        "files": {name: sha(data) for name, data in contents.items()}}
    target = root / "dist" / f"quantlab-candidate-{version}-{commit[:12]}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    verify_bundle(target)
    target.with_suffix(".zip.sha256").write_text(sha(target.read_bytes()) + "  " + target.name + "\n")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-build", action="store_true")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--ci-run-url", default="")
    args = parser.parse_args()
    try:
        if args.verify:
            verify_bundle(args.verify)
            print("Candidate manifest and all payload hashes verified.")
        elif args.check_build:
            build_inputs(ROOT)
            print("Wheel version/source contents and production dashboard integrity verified.")
        else:
            target = prepare(ROOT, args.ci_run_url)
            print(f"Prepared unapproved local candidate: {target.name}")
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, subprocess.CalledProcessError):
        raise SystemExit("Release preparation/verification failed: check clean Git tree, wheel, assets and secret guard.") from None


if __name__ == "__main__":
    main()
