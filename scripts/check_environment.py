"""Offline installed-package/import/constraint consistency check."""
import importlib
import importlib.metadata
import pkgutil
import sys
from pathlib import Path
from packaging.requirements import Requirement


def main():
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Phase 24 supported deployment/CI profile requires Python 3.11.x.")
    for line in (Path(__file__).resolve().parents[1] / "requirements/constraints.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            continue  # Optional cloud/dev dependencies need not be installed.
        if installed not in requirement.specifier:
            raise SystemExit(f"Installed version differs from supported constraint: {requirement.name}")
    import quantlab
    for module in pkgutil.walk_packages(quantlab.__path__, quantlab.__name__ + "."):
        importlib.import_module(module.name)
    print("Supported Python profile, installed constraints and all quantlab imports verified offline.")


if __name__ == "__main__":
    main()
