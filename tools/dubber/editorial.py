"""Bounded source preparation, direct adaptation and explicit editorial review."""

import math
from fractions import Fraction
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw
from .core import digest, need, probe, read, run, safe_id, sha, write
from . import providers
from .performance import (
    DEFAULT_REGISTER,
    check_character_register,
    check_profile,
    event_plan,
    pronunciation_notes,
    timing_report,
)

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
    "Kore",
    "Zephyr",
    "Algieba",
    "Umbriel",
]


def duration(scene):
    return (
        scene["sample_count"] / 48000
        if "sample_count" in scene
        else scene["end"] - scene["start"]
    )


def source_audio_clock(project, timeline):
    """Decode once without input seeking; slice scenes on an exact PCM clock."""
    from .edits import AUDIO_CLOCK_VERSION

    fps = Fraction(timeline["fps"])
    audio_end = round(Fraction(timeline["selected_streams"]["audio"]["end"]) * 48000)
    count = max(
        audio_end, round(Fraction(timeline["source_frame_count"] * 48000, 1) / fps)
    )
    path, marker = project.work / "source.clock.wav", project.work / "source.clock.json"
    spec = {
        "source_sha256": timeline["source_sha256"],
        "audio_end_sample": audio_end,
        "sample_count": count,
        "audio_clock_version": AUDIO_CLOCK_VERSION,
    }
    valid = marker.exists() and read(marker).get("spec") == spec and path.exists()
    valid = valid and read(marker).get("sha256") == sha(path)
    if not valid:
        # Input -ss can discard an AAC frame in FFmpeg 9, even at zero. A
        # timestamped source decode also preserves positive audio start offsets.
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                project.source,
                "-map",
                "0:a:0",
                "-af",
                f"aresample=48000:first_pts=0,atrim=end_sample={audio_end},apad=whole_len={count},atrim=end_sample={count},asetpts=N/SR/TB",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                path,
            ]
        )
        info = sf.info(path)
        need(
            info.samplerate == 48000 and info.channels == 2 and info.frames == count,
            "Source PCM clock does not match selected stream coverage",
        )
        write(marker, {"spec": spec, "sha256": sha(path)})
    return path, marker


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
    need(any(s["codec_type"] == "video" for s in info["streams"]), "No video stream")
    need(any(s["codec_type"] == "audio" for s in info["streams"]), "No audio stream")
    from .edits import build

    timeline = build(project, info, source_hash)
    write(
        identity,
        {
            "source": str(project.source),
            "sha256": source_hash,
            "bytes": project.source.stat().st_size,
        },
    )
    write(project.work / "probe.json", info)
    outputs = [
        identity,
        project.work / "probe.json",
        project.work / "frame-grid.json",
        project.work / "edit-timeline.json",
    ]
    clock, clock_marker = source_audio_clock(project, timeline)
    outputs += [clock, clock_marker]
    from .edits import AUDIO_CLOCK_VERSION

    for scene in timeline["scenes"]:
        folder = project.folder(scene)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = folder / "extraction.json"
        spec = {
            "source_sha256": source_hash,
            "scene": scene,
            "audio_clock_version": AUDIO_CLOCK_VERSION,
        }
        wav, mp3 = folder / "original.wav", folder / "source.mp3"
        valid = stamp.exists() and read(stamp).get("spec") == spec
        if valid:
            valid = all(
                p.exists() and sha(p) == read(stamp)["files"].get(p.name)
                for p in [wav, mp3]
            )
        if not valid:
            fps = Fraction(timeline["fps"])
            start_sample = round(Fraction(scene["source_start_frame"] * 48000, 1) / fps)
            end_sample = round(Fraction(scene["source_end_frame"] * 48000, 1) / fps)
            count = scene["sample_count"]
            samples = np.zeros((count, 2), dtype="int16")
            with sf.SoundFile(clock) as original:
                original.seek(min(start_sample, original.frames))
                selected = original.read(
                    min(count, end_sample - start_sample), dtype="int16", always_2d=True
                )
            samples[: len(selected)] = selected
            sf.write(wav, samples, 48000, subtype="PCM_16")
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
    project.record("prepare", [project.source], outputs, project.preparation_settings())
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
    for scene in project.edits():
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
    result = {}
    for scene in project.edits():
        if scene["mode"] != "dub":
            continue
        units = read(project.folder(scene) / "units.json")
        by_id = {u["id"]: u for u in units}
        need(len(by_id) == len(units), "Duplicate raw source unit id")
        touched = set()
        for edit in [o for o in overrides if o["scene"] == scene["id"]]:
            op = edit.get("op", "replace")
            need(
                op in {"replace", "insert", "split"},
                "Unknown source correction operation",
            )
            need(bool(edit.get("reason", "").strip()), "Correction needs a reason")
            if op != "replace":
                need(
                    bool(edit.get("evidence", "").strip()),
                    "Insert/split corrections need bounded source evidence",
                )
            provenance = {
                "operation": op,
                "reason": edit["reason"],
                "evidence": edit.get("evidence", ""),
            }
            if op == "insert":
                children = [edit["unit"]]
            else:
                parent_id = edit["source_id"]
                need(parent_id in by_id, "Source override points to an unknown unit")
                need(parent_id not in touched, "Duplicate source correction target")
                touched.add(parent_id)
                if op == "replace":
                    by_id[parent_id].update(
                        {
                            k: edit[k]
                            for k in ["ja", "start", "end", "speaker"]
                            if k in edit
                        }
                    )
                    by_id[parent_id]["correction"] = provenance
                    continue
                parent = by_id.pop(parent_id)
                children = edit["units"]
                need(
                    len(children) >= 2,
                    "Splitting a source unit needs at least two children",
                )
                need(
                    all(
                        parent["start"] <= c["start"] < c["end"] <= parent["end"]
                        for c in children
                    ),
                    "Split children must remain inside their referenced source unit",
                )
                provenance = {**provenance, "derived_from": parent_id}
            for child in children:
                safe_id(child["id"])
                need(
                    child["id"] not in by_id, "Inserted/split source id already exists"
                )
                by_id[child["id"]] = {
                    **child,
                    "speaker": child.get("speaker", "manual"),
                    "correction": provenance,
                }
        units = sorted(
            by_id.values(), key=lambda u: (u["start"], u["end"], str(u["id"]))
        )
        for unit in units:
            need(
                all(
                    isinstance(t, (int, float)) and math.isfinite(t)
                    for t in [unit["start"], unit["end"]]
                )
                and 0 <= unit["start"] < unit["end"] <= duration(scene) + 0.01
                and isinstance(unit.get("ja"), str)
                and unit["ja"].strip(),
                "Corrected source unit is out of bounds",
            )
        result[scene["id"]] = units
    need(
        all(o["scene"] in result for o in overrides),
        "Source override points to an unknown/passthrough scene",
    )
    return result


def schema(project, selected=None):
    s = {"type": "string"}
    n = {"type": "number"}
    i = {"type": "integer"}
    unit_id = {"anyOf": [i, s]}

    def obj(fields):
        return {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        }

    def arr(item):
        return {"type": "array", "items": item}

    profile = {
        "self": s,
        "address": s,
        "forms_of_address": arr(s),
        "honorifics": arr(s),
    }

    scene = {
        "type": "string",
        "enum": [
            s["id"]
            for s in project.edits()
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
                        "register": obj(
                            {
                                "default": obj(profile),
                                "relationships": arr(
                                    obj({"addressee_id": s, **profile, "notes": s})
                                ),
                            }
                        ),
                    }
                )
            ),
            "lines": arr(
                obj(
                    {
                        "id": s,
                        "scene": scene,
                        "source_ids": arr(unit_id),
                        "start": n,
                        "end": n,
                        "character_id": s,
                        "ja": s,
                        "text": s,
                        "direction": s,
                        "uncertainty": s,
                        "addressee_id": s,
                        "timing_adjustment": obj({"reason": s, "evidence": s}),
                        "source_splits": arr(
                            obj(
                                {
                                    "source_id": unit_id,
                                    "part": i,
                                    "parts": i,
                                    "start": n,
                                    "end": n,
                                    "reason": s,
                                    "evidence": s,
                                }
                            )
                        ),
                    }
                )
            ),
            "omissions": arr(obj({"scene": scene, "source_id": unit_id, "reason": s})),
            "performance_events": arr(
                obj(
                    {
                        "id": s,
                        "scene": scene,
                        "start": n,
                        "end": n,
                        "character_id": s,
                        "character_ids": arr(s),
                        "kind": {
                            "type": "string",
                            "enum": [
                                "overlap",
                                "interruption",
                                "shout",
                                "laughter",
                                "gasp",
                                "exertion",
                                "other",
                            ],
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["unresolved", "handled_as_line", "omit"],
                        },
                        "line_ids": arr(s),
                        "reason": s,
                        "evidence": s,
                    }
                )
            ),
        }
    )


def adaptation(project, scene_id=None):
    check_profile(project.config.get("register_defaults", DEFAULT_REGISTER))
    pronunciation_notes(project, "")
    units = source_units(project)
    payload = []
    images = []
    selected = [
        s
        for s in project.edits()
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
                "asr_audio_events": [
                    w
                    for w in read(folder / "scribe.json").get("words", [])
                    if w.get("type") not in {"word", "spacing"}
                ],
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
            extraction = {
                "source_sha256": read(project.work / "source-identity.json")["sha256"],
                "absolute_time": scene["start"] + t,
                "filter": "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2",
                "format": "jpeg-v1",
            }
            frame = folder / ("dialogue-" + digest(extraction)[:24] + ".jpg")
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
                        extraction["filter"],
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
        "register_defaults": project.config.get("register_defaults", DEFAULT_REGISTER),
        "pronunciations": project.config.get("pronunciations", []),
        "style": project.config.get("style", ""),
        "scenes": payload,
    }
    prompt = (project.root / "prompts/adapt.md").read_text(encoding="utf-8")
    result = providers.adapt(
        project, payload, schema(project, set(selected_units)), images, prompt
    )
    target = project.work / (
        "scripts/" + scene_id + ".draft.json" if scene_id else "script.draft.json"
    )
    write(target, result)
    validate_script(project, result, selected_units)
    print(
        "Saved script.draft.json. Review source, complete phrases, character evidence and timing; then copy to script.json and run review."
    )


def validate_script(project, script, units=None, final=False):
    units = source_units(project) if units is None else units
    scenes = {
        s["id"]: s for s in project.edits() if s["mode"] == "dub" and s["id"] in units
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
        check_character_register(
            c, project.config.get("register_defaults", DEFAULT_REGISTER)
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
        if line.get("addressee_id"):
            safe_id(line["addressee_id"])
        start, end = line["start"], line["end"]
        need(
            all(isinstance(t, (int, float)) and math.isfinite(t) for t in [start, end])
            and 0 <= start < end <= duration(scenes[sid]) + 0.01,
            "Line timing out of bounds",
        )
        need(
            start >= last.get(sid, -1),
            f"{line['id']}: overlapping/out-of-order performance in {sid}; record the source event and resolve treatment. Simultaneous lanes are not implemented.",
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
    timing_report(script, units)
    event_plan(script, scenes, final=final)
    return script


def assemble(project):
    characters = {}
    lines = []
    omissions = []
    notes = []
    events = []
    for scene in project.edits():
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
        events += [
            {
                **e,
                "id": scene["id"] + "-" + e["id"],
                "line_ids": [scene["id"] + "-" + ref for ref in e.get("line_ids", [])],
            }
            for e in script.get("performance_events", [])
        ]
        notes.append(script.get("source_notes", ""))
    result = {
        "source_notes": "\n".join(notes),
        "characters": list(characters.values()),
        "lines": lines,
        "omissions": omissions,
        "performance_events": events,
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
    script = read(path)
    validate_script(project, script, final=True)
    timing_path = project.work / "script-timing.json"
    write(timing_path, timing_report(script, source_units(project)))
    plan_path = project.work / "performance-plan.json"
    write(
        plan_path, event_plan(script, {s["id"]: s for s in project.edits()}, final=True)
    )
    inputs = [path]
    for scene in project.edits():
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
        [timing_path, plan_path],
        project.review_settings(),
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
