"""Bounded source preparation, direct adaptation and explicit editorial review."""

import math
from PIL import Image, ImageDraw
from .core import need, probe, read, run, safe_id, sha, write
from . import providers

VOICES = [
    "Orus",
    "Leda",
    "Rasalgethi",
    "Algenib",
    "Fenrir",
    "Charon",
    "Puck",
    "Iapetus",
    "Alnilam",
]


def duration(scene):
    return scene["end"] - scene["start"]


def prepare(project):
    need(project.source.is_file(), "Source file does not exist")
    identity = project.work / "source-identity.json"
    source_hash = sha(project.source)
    if identity.exists():
        need(
            read(identity)["sha256"] == source_hash,
            "This slug belongs to a different source; use a new slug",
        )
    info = probe(project.source)
    total = float(info["format"]["duration"])
    need(any(s["codec_type"] == "video" for s in info["streams"]), "No video stream")
    need(any(s["codec_type"] == "audio" for s in info["streams"]), "No audio stream")
    need(
        all(s["end"] <= total + 0.03 for s in project.scenes),
        "Scene extends beyond source",
    )
    if project.config.get("scope") == "full":
        need(
            abs(project.scenes[0]["start"]) < 0.02
            and abs(project.scenes[-1]["end"] - total) < 0.05,
            "Full scope must cover the whole episode",
        )
        need(
            all(
                abs(b["start"] - a["end"]) < 0.02
                for a, b in zip(project.scenes, project.scenes[1:])
            ),
            "Full scope has an unreviewed gap",
        )
    write(
        identity,
        {
            "source": str(project.source),
            "sha256": source_hash,
            "bytes": project.source.stat().st_size,
        },
    )
    write(project.work / "probe.json", info)
    outputs = [identity, project.work / "probe.json"]
    for scene in project.scenes:
        folder = project.folder(scene)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = folder / "extraction.json"
        spec = {"source_sha256": source_hash, "scene": scene}
        wav, mp3 = folder / "original.wav", folder / "source.mp3"
        valid = stamp.exists() and read(stamp).get("spec") == spec
        if valid:
            valid = all(
                p.exists() and sha(p) == read(stamp)["files"].get(p.name)
                for p in [wav, mp3]
            )
        if not valid:
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    scene["start"],
                    "-i",
                    project.source,
                    "-t",
                    duration(scene),
                    "-map",
                    "0:a:0",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    wav,
                ]
            )
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    wav,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    mp3,
                ]
            )
            write(stamp, {"spec": spec, "files": {p.name: sha(p) for p in [wav, mp3]}})
        outputs += [wav, mp3, stamp]
        # Keep aspect ratio in the contact sheet; never modify the source picture.
        sheet = Image.new("RGB", (960, 266), "#171717")
        draw = ImageDraw.Draw(sheet)
        for index, fraction in enumerate([0.15, 0.5, 0.85]):
            t = scene["start"] + duration(scene) * fraction
            frame = folder / f"overview-{index}.jpg"
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    t,
                    "-i",
                    project.source,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2",
                    frame,
                ]
            )
            with Image.open(frame) as im:
                sheet.paste(im, (index * 320, 0))
            draw.text(
                (index * 320 + 8, 244),
                f"{scene['id']} +{duration(scene) * fraction:.2f}s",
                fill="white",
            )
        contact = folder / "overview.jpg"
        sheet.save(contact, quality=91)
        outputs.append(contact)
    project.record("prepare", [project.source], outputs, project.scenes)
    print(
        "Prepared bounded scenes. Inspect contact sheets and source audio before adaptation."
    )


def group_words(raw, end):
    groups = []
    words = []

    def flush():
        if words:
            groups.append(
                {
                    "id": len(groups) + 1,
                    "start": words[0]["start"],
                    "end": min(words[-1]["end"], end),
                    "speaker": words[0].get("speaker_id", "unknown"),
                    "ja": "".join(w["text"] for w in words).strip(),
                }
            )
            words.clear()

    for word in raw.get("words", []):
        if word.get("type") == "spacing":
            continue
        if word.get("type") != "word" or word["start"] >= end:
            flush()
            continue
        if words and (
            word.get("speaker_id") != words[-1].get("speaker_id")
            or word["start"] - words[-1]["end"] > 0.65
            or word["end"] - words[0]["start"] > 9
        ):
            flush()
        words.append(word)
    flush()
    return groups


def transcribe(project):
    project.prepared()
    inputs = []
    outputs = []
    for scene in project.scenes:
        if scene["mode"] != "dub":
            continue
        folder = project.folder(scene)
        audio = folder / "source.mp3"
        inputs.append(audio)
        raw = providers.transcribe(project, audio, "scribe-" + scene["id"], scribe=True)
        independent = providers.transcribe(project, audio, "independent-" + scene["id"])
        write(folder / "scribe.json", raw)
        write(folder / "independent.json", independent)
        write(folder / "units.json", group_words(raw, duration(scene)))
        outputs += [
            folder / "scribe.json",
            folder / "independent.json",
            folder / "units.json",
        ]
    project.record(
        "transcribe",
        inputs,
        outputs,
        {k: project.model(k) for k in ["scribe", "transcribe"]},
    )
    print(
        "Saved raw ASRs and anonymous units; identities still require scene evidence."
    )


def source_units(project):
    project.prepared()
    project.require(
        "transcribe", {k: project.model(k) for k in ["scribe", "transcribe"]}
    )
    overrides_path = project.work / "source-overrides.json"
    overrides = read(overrides_path) if overrides_path.exists() else []
    need(isinstance(overrides, list), "source-overrides.json must be an array")
    used = set()
    result = {}
    for scene in project.scenes:
        if scene["mode"] != "dub":
            continue
        units = read(project.folder(scene) / "units.json")
        for unit in units:
            edits = [
                o
                for o in overrides
                if o["scene"] == scene["id"] and o["source_id"] == unit["id"]
            ]
            need(len(edits) <= 1, "Duplicate source correction")
            if edits:
                edit = edits[0]
                need(
                    bool(edit.get("reason", "").strip()),
                    "Correction needs evidence/reason",
                )
                unit.update({k: edit[k] for k in ["ja", "start", "end"] if k in edit})
                used.add((scene["id"], unit["id"]))
            need(
                0 <= unit["start"] < unit["end"] <= duration(scene) + 0.01,
                "Corrected source unit is out of bounds",
            )
        result[scene["id"]] = units
    need(
        used == {(o["scene"], o["source_id"]) for o in overrides},
        "Source override points to an unknown unit",
    )
    return result


def schema(project, selected=None):
    s = {"type": "string"}
    n = {"type": "number"}
    i = {"type": "integer"}

    def obj(fields):
        return {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        }

    def arr(item):
        return {"type": "array", "items": item}

    scene = {
        "type": "string",
        "enum": [
            s["id"]
            for s in project.scenes
            if s["mode"] == "dub" and (selected is None or s["id"] in selected)
        ],
    }
    return obj(
        {
            "source_notes": s,
            "characters": arr(
                obj(
                    {
                        "id": s,
                        "name": s,
                        "voice": {"type": "string", "enum": VOICES},
                        "style": s,
                        "evidence": s,
                    }
                )
            ),
            "lines": arr(
                obj(
                    {
                        "id": s,
                        "scene": scene,
                        "source_ids": arr(i),
                        "start": n,
                        "end": n,
                        "character_id": s,
                        "ja": s,
                        "text": s,
                        "direction": s,
                        "uncertainty": s,
                    }
                )
            ),
            "omissions": arr(obj({"scene": scene, "source_id": i, "reason": s})),
        }
    )


def adaptation(project, scene_id=None):
    units = source_units(project)
    payload = []
    images = []
    selected = [
        s
        for s in project.scenes
        if s["mode"] == "dub" and (scene_id is None or s["id"] == scene_id)
    ]
    need(bool(selected), "Choose a dub scene")
    need(
        sum(duration(s) for s in selected) <= 240,
        "Adapt long episodes one scene at a time with --scene, then assemble; do not submit an oversized job",
    )
    selected_units = {s["id"]: units[s["id"]] for s in selected}
    for scene in selected:
        folder = project.folder(scene)
        payload.append(
            {
                "scene": scene,
                "units": units[scene["id"]],
                "independent": read(folder / "independent.json"),
            }
        )
        snapshots = [(u["start"] + u["end"]) / 2 for u in units[scene["id"]]]
        need(
            bool(snapshots),
            "No source words; review scene selection/ASR before paying for adaptation",
        )
        sheet = Image.new("RGB", (1280, 266 * ((len(snapshots) + 3) // 4)), "#171717")
        draw = ImageDraw.Draw(sheet)
        for index, t in enumerate(snapshots):
            frame = folder / f"dialogue-{t:.3f}.jpg"
            if not frame.exists():
                run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-ss",
                        scene["start"] + t,
                        "-i",
                        project.source,
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2",
                        frame,
                    ]
                )
            x, y = index % 4 * 320, index // 4 * 266
            with Image.open(frame) as im:
                sheet.paste(im, (x, y))
            draw.text((x + 7, y + 244), f"{scene['id']} +{t:.2f}s", fill="white")
        contact = folder / "dialogue-shots.jpg"
        sheet.save(contact, quality=92)
        images.append(contact)
    payload = {
        "series": project.config.get("series", ""),
        "episode": project.config.get("episode", ""),
        "context": project.config.get("series_context", ""),
        "glossary": project.config.get("glossary", {}),
        "established_cast": project.config.get("cast", []),
        "style": project.config.get("style", ""),
        "scenes": payload,
    }
    prompt = (project.root / "prompts/adapt.md").read_text(encoding="utf-8")
    result = providers.adapt(
        project, payload, schema(project, set(selected_units)), images, prompt
    )
    validate_script(project, result, selected_units)
    target = project.work / (
        "scripts/" + scene_id + ".draft.json" if scene_id else "script.draft.json"
    )
    write(target, result)
    print(
        "Saved script.draft.json. Review source, complete phrases, character evidence and timing; then copy to script.json and run review."
    )


def validate_script(project, script, units=None):
    units = source_units(project) if units is None else units
    scenes = {
        s["id"]: s for s in project.scenes if s["mode"] == "dub" and s["id"] in units
    }
    characters = {c["id"]: c for c in script["characters"]}
    need(len(characters) == len(script["characters"]), "Duplicate character id")
    for c in characters.values():
        safe_id(c["id"])
        need(
            c["voice"] in VOICES
            and bool(c["style"].strip())
            and bool(c["evidence"].strip()),
            "Missing voice, character style or identity evidence",
        )
    names = set()
    last = {}
    used = {key: set() for key in scenes}
    omitted = {key: set() for key in scenes}
    for line in script["lines"]:
        safe_id(line["id"])
        need(line["id"] not in names, "Duplicate line id")
        names.add(line["id"])
        sid = line["scene"]
        need(sid in scenes, "Cannot voice a passthrough/song or unknown scene")
        need(line["character_id"] in characters, "Unknown character")
        start, end = line["start"], line["end"]
        need(
            all(isinstance(t, (int, float)) and math.isfinite(t) for t in [start, end])
            and 0 <= start < end <= duration(scenes[sid]) + 0.01,
            "Line timing out of bounds",
        )
        need(
            start >= last.get(sid, -1),
            "Performances must be ordered and must not overlap; deliberate overlap needs a separate implementation",
        )
        last[sid] = end
        need(
            isinstance(line["text"], str) and line["text"].strip(), "Empty spoken text"
        )
        need(bool(line["source_ids"]), "Each performance needs source coverage")
        need(
            0 <= line.get("approved_end_extension", 0.22) <= 0.4,
            "End extension must stay within 0.4 seconds",
        )
        need(
            line.get("effect", "clean") in {"clean", "radio"}, "Unknown dialogue effect"
        )
        used[sid].update(line["source_ids"])
    for omission in script.get("omissions", []):
        need(
            omission["scene"] in scenes and bool(omission["reason"].strip()),
            "Omission requires a scene and reason",
        )
        omitted[omission["scene"]].add(omission["source_id"])
    for sid in scenes:
        expected = {u["id"] for u in units[sid]}
        need(
            not (used[sid] & omitted[sid]) and used[sid] | omitted[sid] == expected,
            f"Uncovered, double-accounted or unknown Japanese source units in {sid}",
        )
    return script


def assemble(project):
    characters = {}
    lines = []
    omissions = []
    notes = []
    for scene in project.scenes:
        if scene["mode"] != "dub":
            continue
        path = project.work / "scripts" / (scene["id"] + ".json")
        if not path.exists():
            path = project.work / "scripts" / (scene["id"] + ".draft.json")
        need(path.exists(), "Missing per-scene draft for " + scene["id"])
        script = read(path)
        for c in script["characters"]:
            if c["id"] in characters:
                need(
                    characters[c["id"]]["voice"] == c["voice"],
                    "Resolve conflicting casting before assembly: " + c["id"],
                )
            else:
                characters[c["id"]] = c
        for line in script["lines"]:
            need(line["scene"] == scene["id"], "Per-scene file includes another scene")
            lines.append({**line, "id": scene["id"] + "-" + line["id"]})
        omissions += script.get("omissions", [])
        notes.append(script.get("source_notes", ""))
    result = {
        "source_notes": "\n".join(notes),
        "characters": list(characters.values()),
        "lines": lines,
        "omissions": omissions,
    }
    validate_script(project, result)
    write(project.work / "script.draft.json", result)
    print(
        "Assembled script.draft.json; review identities, merged styles, source coverage and scene transitions into script.json."
    )


def review(project, note):
    need(bool(note.strip()), "Record concrete source/acting review notes")
    path = project.work / "script.json"
    need(path.exists(), "Review the draft into script.json first")
    validate_script(project, read(path))
    inputs = [path]
    for scene in project.scenes:
        if scene["mode"] == "dub":
            inputs += [
                project.folder(scene) / n
                for n in ["units.json", "independent.json", "scribe.json"]
            ]
    overrides = project.work / "source-overrides.json"
    if overrides.exists():
        inputs.append(overrides)
    record = project.record(
        "review",
        inputs,
        [],
        {
            "scenes": project.scenes,
            "context": project.config.get("series_context", ""),
            "glossary": project.config.get("glossary", {}),
        },
    )
    # Bind absence too: adding an override invalidates review.
    record["overrides_sha256"] = sha(overrides) if overrides.exists() else None
    record["review_note"] = note
    record["review_kind"] = (
        "agent-assisted editorial review; not a native-speaker certification"
    )
    write(project.work / "stages/review.json", record)
    print(
        "Editorial review sealed. Text/cast/source changes require a fresh review before voices/mix/export."
    )
