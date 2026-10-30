"""Local configuration, content identity and dependency seals. No provider calls."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def safe_id(value):
    need(
        isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value),
        "Use a short lowercase slug/id with digits and hyphens",
    )
    need(
        value.upper().split(".")[0]
        not in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *[f"COM{i}" for i in range(1, 10)],
            *[f"LPT{i}" for i in range(1, 10)],
        },
        "Reserved Windows identifier",
    )
    return value


def run(args, log=None):
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with Path(log).open("w", encoding="utf-8") as stream:
            subprocess.run(
                [str(a) for a in args],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
            )
    else:
        subprocess.run([str(a) for a in args], check=True)


def probe(path):
    return json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            encoding="utf-8",
        )
    )


class Project:
    def __init__(self, config, root=ROOT):
        self.root = Path(root).resolve()
        self.config_path = self.path(config)
        self.config = read(self.config_path)
        self.slug = safe_id(self.config["slug"])
        self.source = self.path(self.config["source"])
        self.work = self.root / "work" / self.slug
        self.output = self.root / "dubbed" / (self.slug + ".id.480p.mp4")
        self.scenes = self.config["scenes"]
        need(
            self.config.get("scope", "pilot") in {"pilot", "full"},
            "scope must be pilot or full",
        )
        need(
            bool(self.scenes),
            "Select at least one bounded scene before submitting jobs",
        )
        names = set()
        previous = -1
        for scene in self.scenes:
            safe_id(scene["id"])
            need(scene["id"] not in names, "Duplicate scene id")
            names.add(scene["id"])
            start, end = scene["start"], scene["end"]
            need(
                all(
                    isinstance(x, (int, float)) and math.isfinite(x)
                    for x in [start, end]
                )
                and 0 <= start < end,
                "Invalid scene range",
            )
            need(start >= previous, "Scene ranges must be ordered and must not overlap")
            previous = end
            need(
                scene.get("mode") in {"dub", "passthrough"},
                "Scene mode must be dub or passthrough",
            )
            if scene.get("contains_song"):
                need(
                    scene["mode"] == "passthrough",
                    "Songs must pass through unchanged; split dialogue from singing",
                )
        need(
            1 <= self.config.get("voice_workers", 3) <= 3,
            "Use one to three concurrent voice jobs",
        )

    def path(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def folder(self, scene):
        return self.work / "scenes" / scene["id"]

    def model(self, name):
        return self.config["models"][name]

    def record(self, stage, inputs, outputs, settings=None):
        record = {
            "inputs": {str(Path(p).resolve()): sha(p) for p in inputs},
            "outputs": {str(Path(p).resolve()): sha(p) for p in outputs},
            "settings": settings,
            "completed_at": time.time(),
        }
        write(self.work / "stages" / (stage + ".json"), record)
        return record

    def require(self, stage, settings=None):
        path = self.work / "stages" / (stage + ".json")
        need(path.exists(), f"Run {stage} first")
        state = read(path)
        if settings is not None:
            need(
                state["settings"] == settings,
                f"{stage} settings changed; rerun that stage",
            )
        for section in ["inputs", "outputs"]:
            for name, expected in state[section].items():
                need(
                    Path(name).is_file() and sha(name) == expected,
                    f"{stage} is stale: {Path(name).name}; rerun the smallest affected stage",
                )
        return state

    def prepared(self):
        need(
            self.source == self.path(self.config["source"]),
            "Source selection changed; reload the project and prepare again",
        )
        return self.require("prepare", self.preparation_settings())

    def preparation_settings(self):
        from .edits import AUDIO_CLOCK_VERSION, VERSION

        return {
            "source": str(self.path(self.config["source"])),
            "scope": self.config.get("scope", "pilot"),
            "scenes": self.scenes,
            "edit_timeline_version": VERSION,
            "audio_clock_version": AUDIO_CLOCK_VERSION,
        }

    def edits(self):
        self.prepared()
        return read(self.work / "edit-timeline.json")["scenes"]

    def approved(self):
        self.prepared()
        state = self.require(
            "review",
            self.review_settings(),
        )
        overrides = self.work / "source-overrides.json"
        need(
            state.get("overrides_sha256")
            == (sha(overrides) if overrides.exists() else None),
            "Source overrides changed; review again",
        )
        return state

    def review_settings(self):
        from .performance import DEFAULT_REGISTER

        return {
            "scenes": self.scenes,
            "context": self.config.get("series_context", ""),
            "glossary": self.config.get("glossary", {}),
            "style": self.config.get("style", ""),
            "register_defaults": self.config.get("register_defaults", DEFAULT_REGISTER),
            "pronunciations": self.config.get("pronunciations", []),
            "cast": self.config.get("cast", []),
            "performance_policy_version": 2,
        }


def env_key(name):
    value = os.environ.get(name)
    need(bool(value), f"Missing {name}; no request submitted")
    return value
