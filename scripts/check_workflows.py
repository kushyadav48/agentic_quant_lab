"""Local YAML and minimal policy checks; actionlint remains the semantic linter."""
import re
from pathlib import Path
import yaml


def validate(path):
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert document["permissions"] == {"contents": "read"}, "workflow permissions must be read-only"
    assert set(document["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert document["on"]["push"]["branches"] == ["master"]
    assert document["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    for job in document["jobs"].values():
        assert "timeout-minutes" in job
        assert "permissions" not in job
        build_tools_seeded = False
        for step in job["steps"]:
            command = step.get("run", "")
            if "pip install -r requirements/build.txt" in command:
                build_tools_seeded = True
            if "pip install" in command and "-e '.[dev]'" in command:
                assert build_tools_seeded and "--no-build-isolation" in command, "application build backend must use seeded pins"
            if "uses" in step:
                assert re.fullmatch(r"actions/[\w-]+@[a-f0-9]{40}", step["uses"])
                if step["uses"].startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] == "false"
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\$\{\{\s*secrets\.", text) and "pull_request_target" not in text
    assert text.count("python -m pytest -q --junitxml") == 1
    print("CI YAML, read-only permissions, immutable action pins, triggers and full-suite policy verified.")


if __name__ == "__main__":
    validate(Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml")
