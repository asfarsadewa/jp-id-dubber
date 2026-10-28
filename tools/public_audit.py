"""Audit exactly the staged public include set, with redacted findings only."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md",
    "AGENTS.md",
    "LICENSE",
    ".gitignore",
    ".gitattributes",
    ".env.example",
    "project.example.json",
    "requirements.txt",
    "requirements.lock.txt",
    "requirements-separator.txt",
}
DIRECTORIES = {"tools", "tests", "prompts", "docs", ".agents", ".github"}
EXTENSIONS = {".py", ".md", ".json", ".txt", ".yaml", ".yml"}


def main():
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode:
        raise ValueError(
            "Tracked working files differ from the index; stage the reviewed revision"
        )
    names = [
        p
        for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
        if p
    ]
    if not names:
        raise ValueError("Stage the intended public files before auditing")
    findings = []
    manifest = {}
    secrets = {
        name: value
        for name, value in os.environ.items()
        if re.search("KEY|TOKEN|SECRET|PASSWORD", name, re.I) and len(value) >= 16
    }
    patterns = {
        "provider credential": r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "personal home path": r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)[A-Za-z0-9_.-]+[\\/]",
    }
    for name in names:
        path = Path(name)
        if (
            not (
                name in ROOT_FILES
                or len(path.parts) > 1
                and path.parts[0] in DIRECTORIES
                and path.suffix in EXTENSIONS
            )
            or path.name in {"project.json", "source-overrides.json", "script.json"}
            or "__pycache__" in path.parts
        ):
            findings.append({"file": name, "reason": "outside public include set"})
            continue
        raw = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        if len(raw) > 300000 or b"\0" in raw:
            findings.append({"file": name, "reason": "unexpected large/binary content"})
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            findings.append({"file": name, "reason": "not UTF-8 text"})
            continue
        for label, pattern in patterns.items():
            if re.search(pattern, text):
                findings.append({"file": name, "reason": label})
        for key, value in secrets.items():
            if value.encode() in raw:
                findings.append(
                    {"file": name, "reason": "matches environment credential " + key}
                )
        manifest[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    report = {
        "passed": not findings,
        "tracked_files": len(names),
        "total_bytes": sum(p["bytes"] for p in manifest.values()),
        "findings": findings,
        "files": manifest,
    }
    folder = ROOT / "work/publication"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked-audit.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ["passed", "tracked_files", "total_bytes", "findings"]
            },
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
