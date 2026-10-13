"""Conservative tracked-file guard. Report filenames/rules, never matched values.

Not a replacement for GitHub secret scanning or operator credential rotation.
Scan tracked and proposed nonignored files, not ignored private files/history.
"""
import re
import subprocess
from pathlib import Path

RULES = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud credential": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[0-9A-Za-z]{30,}"),
    "credential assignment": re.compile(rb'''(?im)^[ \t]*(?:QUANTLAB_API_(?:OPERATOR|READER)_TOKEN|GEMINI_API_KEY|AWS_SECRET_ACCESS_KEY)[ \t]*=[ \t]*["']?([^\s#"'][^\r\n]*)'''),
    "browser secret configuration": re.compile(rb"(?im)^[ \t]*VITE_[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY)[A-Z0-9_]*[ \t]*=[ \t]*\S+"),
}


def inspect(name, data):
    parts = Path(name).parts
    if (any(p in (".aws", "secrets", ".runtime", "node_modules", ".venv") for p in parts)
            or (Path(name).name.startswith(".env") and Path(name).name != ".env.example")
            or Path(name).suffix.lower() in (".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3")):
        return ["private/generated file"]
    return [rule for rule, expression in RULES.items() if expression.search(data)]


def main():
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    failures = [(name, inspect(name, Path(name).read_bytes())) for name in names if name and Path(name).is_file()]
    for name, rules in failures:
        for rule in rules:
            print(f"Secret guard: {name}: {rule}")
    if any(rules for _, rules in failures):
        raise SystemExit(1)
    print("Tracked-file secret guard passed (no credential values printed).")


if __name__ == "__main__":
    main()
