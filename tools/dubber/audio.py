"""Measured phrase fitting and M&E mixing; never overlay the original dialogue."""

import csv
import numpy as np
import soundfile as sf
from .core import need, read, run, write
from .editorial import duration
from .voices import require_takes

RATE = 48000
MAX_TEMPO = 1.12


def trim_edges(audio, rate):
    need(
        audio.ndim == 1 and len(audio) > 0 and np.isfinite(audio).all(),
        "Invalid voice waveform",
    )
    frame = max(1, round(rate * 0.01))
    levels = np.array(
        [
            np.sqrt(np.mean(audio[i : i + frame] ** 2))
            for i in range(0, len(audio), frame)
        ]
    )
    active = np.flatnonzero(levels > max(0.0008, float(levels.max()) * 0.025))
    need(len(active) > 0, "Generated take is silent")
    start = max(0, int(active[0] * frame - rate * 0.035))
    end = min(len(audio), int((active[-1] + 1) * frame + rate * 0.055))
    return audio[start:end].copy(), start / rate, (len(audio) - end) / rate


def active_rms(audio):
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    levels = np.array(
        [np.sqrt(np.mean(audio[i : i + 960] ** 2)) for i in range(0, len(audio), 960)]
    )
    if not len(levels):
        return 0.001
    active = levels[levels > max(0.0002, float(np.percentile(levels, 95)) * 0.10)]
    return float(np.sqrt(np.mean(active**2))) if len(active) else 0.001


def fit(project):
    takes = require_takes(project)
    project.require("separate")
    script = read(project.work / "script.json")
    records = []
    problems = []
    combined = []
    timeline = []
    offset = 0
    inputs = [project.work / "script.json", project.work / "voice-takes.json"] + [
        t["path"] for t in takes.values()
    ]
    folder = project.work / "fitted"
    folder.mkdir(exist_ok=True)
    for scene in project.scenes:
        directory = project.folder(scene)
        count = round(duration(scene) * RATE)
        if scene["mode"] == "passthrough":
            original, rate = sf.read(
                directory / "original.wav", dtype="float32", always_2d=True
            )
            need(
                rate == RATE and len(original) >= count - 1, "Invalid passthrough audio"
            )
            combined.append(original[:count])
            inputs.append(directory / "original.wav")
            offset += duration(scene)
            continue
        bed, rate = sf.read(
            directory / "stems/background.wav", dtype="float32", always_2d=True
        )
        speech, speech_rate = sf.read(
            directory / "stems/speech.wav", dtype="float32", always_2d=True
        )
        need(
            rate == speech_rate == RATE
            and bed.shape[1] == speech.shape[1] == 2
            and len(bed) >= count - 1,
            "Stem format/duration mismatch",
        )
        inputs += [directory / "stems/background.wav", directory / "stems/speech.wav"]
        selected = [l for l in script["lines"] if l["scene"] == scene["id"]]
        dialog = np.zeros((count, 2), dtype="float32")
        for index, line in enumerate(selected):
            take = takes[line["id"]]
            audio, rate = sf.read(take["path"], dtype="float32")
            audio, left, right = trim_edges(audio, rate)
            start = max(0, line["start"] - 0.025)
            next_start = (
                selected[index + 1]["start"]
                if index + 1 < len(selected)
                else duration(scene)
            )
            max_end = min(
                duration(scene),
                line["end"] + line.get("approved_end_extension", 0.22),
                next_start - 0.045,
            )
            slot = max_end - start
            need(slot > 0, "Overlapping performance slots")
            tempo = max(1.0, len(audio) / rate / max(0.05, slot - 0.04))
            record = {
                "id": line["id"],
                "scene": scene["id"],
                "tempo": tempo,
                "trimmed_seconds": len(audio) / rate,
                "available_seconds": slot,
                "text": line["text"],
            }
            if tempo > MAX_TEMPO:
                problems.append(record)
                records.append(record)
                continue
            raw = folder / (line["id"] + ".trimmed.wav")
            fitted = folder / (line["id"] + ".wav")
            sf.write(raw, audio, rate, subtype="PCM_16")
            filters = [f"atempo={tempo:.8f}"]
            if line.get("effect") == "radio":
                filters += ["highpass=f=200", "lowpass=f=5500"]
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    raw,
                    "-af",
                    ",".join(filters),
                    "-ar",
                    RATE,
                    "-ac",
                    "1",
                    fitted,
                ]
            )
            audio, rate = sf.read(fitted, dtype="float32")
            gain = float(
                np.clip(
                    active_rms(
                        speech[round(line["start"] * RATE) : round(line["end"] * RATE)]
                    )
                    * 1.12
                    / max(active_rms(audio), 0.0001),
                    0.05,
                    2.5,
                )
            )
            audio *= gain
            fade = min(round(0.006 * RATE), len(audio) // 2)
            if fade:
                audio[:fade] *= np.linspace(0, 1, fade)
                audio[-fade:] *= np.linspace(1, 0, fade)
            begin = round(start * RATE)
            end = begin + len(audio)
            need(
                end <= count and end / RATE <= max_end + 0.012,
                "Fitted take overran its bounded slot; shorten or retake",
            )
            dialog[begin:end] += audio[:, None]
            record.update(
                {"placement_start": start, "placement_end": end / RATE, "gain": gain}
            )
            records.append(record)
            timeline.append(
                {**line, "start": offset + start, "end": offset + end / RATE}
            )
        mixed = bed[:count] + dialog
        need(np.isfinite(mixed).all(), "Non-finite mixed audio")
        sf.write(directory / "dialogue.id.wav", dialog, RATE, subtype="FLOAT")
        combined.append(mixed)
        offset += duration(scene)
    write(
        project.work / "timing-analysis.json",
        {
            "passed": not problems,
            "max_tempo": MAX_TEMPO,
            "takes": records,
            "problems": problems,
        },
    )
    need(
        not problems,
        "Some performances need shorter wording or a retake; inspect timing-analysis.json. No export authorized by this failed fit.",
    )
    raw = project.work / "mix.id.raw.wav"
    master = project.work / "mix.id.wav"
    sf.write(raw, np.concatenate(combined), RATE, subtype="FLOAT")
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            raw,
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            RATE,
            "-c:a",
            "pcm_s24le",
            master,
        ]
    )
    write(project.work / "timeline.json", timeline)
    project.record(
        "mix",
        inputs,
        [master, project.work / "timeline.json", project.work / "timing-analysis.json"],
        {"max_tempo": MAX_TEMPO},
    )
    print(f"Fitted {len(records)} performances; Indonesian-only master ready.")


def require_mix(project):
    require_takes(project)
    project.require("separate")
    project.require("mix", {"max_tempo": MAX_TEMPO})
    need(
        read(project.work / "timing-analysis.json")["passed"],
        "Unresolved timing problems",
    )


def stamp(t):
    millis = round(t * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def captions(project):
    import textwrap

    rows = read(project.work / "timeline.json")
    output = project.output
    subs = [
        f"{i}\n{stamp(l['start'])} --> {stamp(l['end'])}\n"
        + "\n".join(
            textwrap.wrap(l["text"], 42, break_long_words=False, break_on_hyphens=False)
        )
        for i, l in enumerate(rows, 1)
    ]
    output.with_suffix(".srt").write_text("\n\n".join(subs) + "\n", encoding="utf-8")
    with output.with_suffix(".review.tsv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(
            ["id", "start", "end", "character", "Japanese", "Indonesian", "uncertainty"]
        )
        for l in rows:
            writer.writerow(
                [
                    l["id"],
                    stamp(l["start"]),
                    stamp(l["end"]),
                    l["character_id"],
                    l["ja"],
                    l["text"],
                    l["uncertainty"],
                ]
            )
    script = read(project.work / "script.json")
    write(output.with_suffix(".script.json"), script)
    write(output.with_suffix(".cast.json"), script["characters"])
