#!/usr/bin/env python3
"""Stage-by-stage Japanese → Bahasa Indonesia dubbing. No implicit paid 'all'."""

from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from dubber.core import ROOT, Project, need, read, safe_id, write
from dubber.providers import Pending


def status(project):
    checks = {}
    for stage in [
        "prepare",
        "transcribe",
        "review",
        "separate",
        "voices",
        "mix",
        "render",
    ]:
        if not (project.work / "stages" / (stage + ".json")).exists():
            checks[stage] = "missing"
            continue
        try:
            if stage == "prepare":
                project.prepared()
            elif stage == "review":
                project.approved()
            elif stage == "voices":
                from dubber.voices import require_takes

                require_takes(project)
            elif stage in {"mix", "render"}:
                from dubber.audio import require_mix

                require_mix(project)
                if stage == "render":
                    project.require("render", {"height": 480, "audio": "ind-only"})
            else:
                project.require(stage)
            checks[stage] = "current"
        except (ValueError, KeyError, FileNotFoundError) as error:
            checks[stage] = "stale: " + str(error)
    receipts = []
    for p in sorted((project.work / "api").glob("*.receipt.json")):
        record = read(p)
        receipts.append(
            {
                "file": p.name,
                "status": record.get("status"),
                "id": record.get("id"),
                "http_status": record.get("http_status"),
            }
        )
    return {
        "slug": project.slug,
        "source": str(project.source),
        "work": str(project.work),
        "output": str(project.output),
        "stages": checks,
        "jobs": receipts,
    }


def doctor(project=None):
    report = {
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "executables": {
            name: bool(shutil.which(name)) for name in ["ffmpeg", "ffprobe", "git"]
        },
        "base_modules": {
            name: bool(importlib.util.find_spec(name))
            for name in ["requests", "PIL", "numpy", "scipy", "soundfile"]
        },
    }
    import os

    report["key_presence"] = {
        name: bool(os.environ.get(name))
        for name in ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY"]
    }
    if project:
        from dubber.separation import paths

        checkout, checkpoint = paths(project)
        report.update(
            source_exists=project.source.exists(),
            separator_checkout_exists=checkout.exists(),
            checkpoint_exists=checkpoint.exists(),
            torch_installed=bool(importlib.util.find_spec("torch")),
        )
    print(json.dumps(report, indent=2))
    return (
        0
        if report["python_3_12_or_newer"]
        and all(report["executables"].values())
        and all(report["base_modules"].values())
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        help="Local project JSON; paths inside it resolve from the repo root",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--source", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--start", type=float, required=True)
    init.add_argument("--end", type=float, required=True)
    commands.add_parser("doctor")
    commands.add_parser("status")
    commands.add_parser("prepare")
    commands.add_parser("transcribe").add_argument(
        "--submit", action="store_true", required=True
    )
    adapt = commands.add_parser("adapt")
    adapt.add_argument("--submit", action="store_true", required=True)
    adapt.add_argument("--scene")
    commands.add_parser("assemble")
    commands.add_parser("review").add_argument("--note", required=True)
    voices = commands.add_parser("voices")
    voices.add_argument("--submit", action="store_true")
    setup = commands.add_parser("setup-separator")
    setup.add_argument("--no-download", action="store_true")
    commands.add_parser("separate")
    commands.add_parser("fit")
    commands.add_parser("render").add_argument("--overwrite", action="store_true")
    commands.add_parser("verify")
    qa = commands.add_parser("qa")
    qa.add_argument("--start", type=float, default=0)
    qa.add_argument("--seconds", type=float, default=40)
    qa.add_argument("--submit", action="store_true")
    qa.add_argument("--background-scene")
    args = parser.parse_args(argv)
    if args.command == "init":
        safe_id(args.slug)
        path = (
            Path(args.config) if args.config else ROOT / "local" / (args.slug + ".json")
        )
        if not path.is_absolute():
            path = ROOT / path
        need(
            not path.exists(),
            "Local config already exists; preserve its episode settings",
        )
        config = read(ROOT / "project.example.json")
        config.update(
            slug=args.slug,
            source=args.source,
            scenes=[
                {
                    "id": "dialogue-01",
                    "start": args.start,
                    "end": args.end,
                    "mode": "dub",
                    "description": "Review this bounded dialogue scene; exclude songs.",
                }
            ],
        )
        # Validate before leaving a config behind.
        need(args.start >= 0 and args.end > args.start, "Invalid initial scene range")
        write(path, config)
        Project(path)
        print(path)
        return 0
    project = Project(args.config) if args.config else None
    if args.command == "doctor":
        return doctor(project)
    need(project is not None, "Pass --config local/<episode>.json before the command")
    if args.command == "status":
        print(json.dumps(status(project), indent=2))
        return 0
    if args.command in {"prepare", "transcribe", "adapt", "assemble", "review"}:
        from dubber import editorial

        if args.command == "prepare":
            editorial.prepare(project)
        elif args.command == "transcribe":
            editorial.transcribe(project)
        elif args.command == "adapt":
            editorial.adaptation(project, args.scene)
        elif args.command == "assemble":
            editorial.assemble(project)
        else:
            editorial.review(project, args.note)
    elif args.command == "voices":
        from dubber.voices import generate

        generate(project, submit=args.submit)
    elif args.command in {"setup-separator", "separate"}:
        from dubber import separation

        if args.command == "setup-separator":
            separation.setup(project, download=not args.no_download)
        else:
            separation.separate(project)
    elif args.command == "fit":
        from dubber.audio import fit

        fit(project)
    elif args.command in {"render", "verify"}:
        from dubber import render

        if args.command == "render":
            render.render(project, overwrite=args.overwrite)
        else:
            print(json.dumps(render.verify(project), indent=2))
    elif args.command == "qa":
        from dubber.qa import check

        check(project, args.start, args.seconds, args.submit, args.background_scene)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Pending as error:
        print(str(error), file=sys.stderr)
        sys.exit(3)
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
