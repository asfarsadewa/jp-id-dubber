"""Resolve cuts once on the audited source frame grid for picture and audio."""

from fractions import Fraction
import json
import math
import subprocess
from .core import need, read, write

VERSION = 2
RATE = 48000


def nearest_tick(value):
    """Exact rational rescaling, nearest with halfway cases away from zero."""
    value = Fraction(value)
    if value < 0:
        return -nearest_tick(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def audit_frame_ticks(frames, fps, time_base, origin):
    need(
        bool(frames) and time_base > 0 and time_base <= 1 / fps,
        "Source has no usable timestamp grid",
    )
    need(
        all(isinstance(f.get("best_effort_timestamp"), int) for f in frames),
        "Missing source frame timestamps",
    )
    expected = [
        nearest_tick((origin + Fraction(i, 1) / fps) / time_base)
        for i in range(len(frames))
    ]
    need(
        [f["best_effort_timestamp"] for f in frames] == expected,
        "Irregular frame timing, missing frames or offset video origin is unsupported; normalize a separate source copy deliberately",
    )


def selected_audio_bounds(source, audio, origin):
    """Audit a:0 presentation packets, excluding signalled codec padding."""
    time_base = Fraction(audio["time_base"])
    rate = int(audio["sample_rate"])
    packets = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_packets",
                "-show_entries",
                "packet=pts,duration,side_data_list",
                "-of",
                "json",
                str(source),
            ],
            encoding="utf-8",
        )
    )["packets"]
    spans = []
    for packet in packets:
        need(
            isinstance(packet.get("pts"), int)
            and isinstance(packet.get("duration"), int)
            and packet["duration"] > 0,
            "Cannot establish selected audio coverage from packet timestamps/durations",
        )
        side = [
            s
            for s in packet.get("side_data_list", [])
            if s.get("side_data_type") == "Skip Samples"
        ]
        skip = sum(int(s.get("skip_samples", 0)) for s in side)
        discard = sum(int(s.get("discard_padding", 0)) for s in side)
        start = max(
            Fraction(0), packet["pts"] * time_base - origin + Fraction(skip, rate)
        )
        end = (
            (packet["pts"] + packet["duration"]) * time_base
            - origin
            - Fraction(discard, rate)
        )
        if end > start:
            spans.append((start, end))
    need(bool(spans), "Selected audio stream has no presentable samples")
    return {
        "index": audio["index"],
        "time_base": str(time_base),
        "start": str(min(s[0] for s in spans)),
        "end": str(max(s[1] for s in spans)),
        "sample_rate": rate,
        "padding_accounted": True,
    }


def audio_is_covered(scenes, fps, audio):
    time_base = Fraction(audio["time_base"])
    cursor = nearest_tick(Fraction(audio["start"]) / time_base)
    end = nearest_tick(Fraction(audio["end"]) / time_base)
    for scene in scenes:
        left = nearest_tick(Fraction(scene["source_start_frame"], 1) / fps / time_base)
        right = nearest_tick(Fraction(scene["source_end_frame"], 1) / fps / time_base)
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = right
        if cursor >= end:
            return True
    return cursor >= end


def reject_selected_audio_tail(project, fps, frame_count, audio):
    time_base = Fraction(audio["time_base"])
    picture_end = nearest_tick(Fraction(frame_count, 1) / fps / time_base)
    audio_start = nearest_tick(Fraction(audio["start"]) / time_base)
    audio_end = nearest_tick(Fraction(audio["end"]) / time_base)
    for scene in project.scenes:
        wanted_end = min(
            audio_end, nearest_tick(Fraction(str(scene["end"])) / time_base)
        )
        wanted_start = max(
            picture_end,
            audio_start,
            nearest_tick(Fraction(str(scene["start"])) / time_base),
        )
        need(
            wanted_end <= wanted_start,
            "Selected audio tail extends beyond the last picture frame; this exporter cannot preserve that range without an explicit picture-hold treatment. Condition a separate source copy or select a range within the picture; no tail will be silently discarded.",
        )


def coverage(project, total):
    scenes = project.scenes
    need(all(s["end"] <= total + 0.002 for s in scenes), "Scene extends beyond source")
    if project.config.get("scope", "pilot") == "full":
        need(
            abs(scenes[0]["start"]) < 1e-6 and abs(scenes[-1]["end"] - total) < 0.002,
            "Full scope must cover the actual whole source",
        )
        need(
            all(abs(b["start"] - a["end"]) < 1e-6 for a, b in zip(scenes, scenes[1:])),
            "Full scope has an unreviewed gap",
        )


def build(project, info, source_hash):
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    fps = Fraction(video["r_frame_rate"])
    need(fps > 0, "Source has no usable frame rate")
    time_base = Fraction(video["time_base"])
    origin = Fraction(info["format"].get("start_time", "0"))
    path = project.work / "frame-grid.json"
    grid = read(path) if path.exists() else {}
    identity = {
        "source_sha256": source_hash,
        "fps": str(fps),
        "time_base": str(time_base),
        "video_index": video["index"],
        "audio_index": audio["index"],
        "version": VERSION,
    }
    if grid.get("identity") != identity:
        frames = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_frames",
                    "-show_entries",
                    "frame=best_effort_timestamp",
                    "-of",
                    "json",
                    str(project.source),
                ],
                encoding="utf-8",
            )
        )["frames"]
        audit_frame_ticks(frames, fps, time_base, origin)
        grid = {
            "identity": identity,
            "frame_count": len(frames),
            "uniform_frame_grid_verified": True,
            "selected_audio": selected_audio_bounds(project.source, audio, origin),
        }
        write(path, grid)
    total = max(
        Fraction(grid["frame_count"], 1) / fps, Fraction(grid["selected_audio"]["end"])
    )
    coverage(project, float(total))
    reject_selected_audio_tail(
        project, fps, grid["frame_count"], grid["selected_audio"]
    )
    offset = 0
    edits = []
    for scene in project.scenes:
        # The same requested boundary always resolves to the same source frame.
        start = min(
            grid["frame_count"],
            math.ceil(Fraction(str(scene["start"])) * fps - Fraction(1, 100000)),
        )
        end = min(
            grid["frame_count"],
            math.ceil(Fraction(str(scene["end"])) * fps - Fraction(1, 100000)),
        )
        need(0 <= start < end, "Selected scene contains no source frames")
        output_end = offset + end - start
        sample_start = round(Fraction(offset * RATE, 1) / fps)
        sample_end = round(Fraction(output_end * RATE, 1) / fps)
        edits.append(
            {
                **scene,
                "requested_start": scene["start"],
                "requested_end": scene["end"],
                "start": float(start / fps),
                "end": float(end / fps),
                "source_start_frame": start,
                "source_end_frame": end,
                "output_start_frame": offset,
                "output_end_frame": output_end,
                "output_start_sample": sample_start,
                "output_end_sample": sample_end,
                "sample_count": sample_end - sample_start,
            }
        )
        offset = output_end
    continuous = all(
        a["source_end_frame"] == b["source_start_frame"]
        for a, b in zip(edits, edits[1:])
    )
    full = (
        edits[0]["source_start_frame"] == 0
        and edits[-1]["source_end_frame"] == grid["frame_count"]
        and continuous
    )
    full_audio = audio_is_covered(edits, fps, grid["selected_audio"])
    need(
        project.config.get("scope") != "full" or full,
        "Resolved full-episode timeline does not cover every source frame",
    )
    need(
        project.config.get("scope") != "full" or full_audio,
        "Full scope would omit selected audio; audio beyond picture needs an explicit supported treatment",
    )
    timeline = {
        "version": VERSION,
        "source_sha256": source_hash,
        "fps": str(fps),
        "source_frame_count": grid["frame_count"],
        "source_duration": float(total),
        "container_duration": float(info["format"]["duration"]),
        "selected_streams": {
            "video": {
                "index": video["index"],
                "time_base": str(time_base),
                "start": "0",
                "end": str(Fraction(grid["frame_count"], 1) / fps),
            },
            "audio": grid["selected_audio"],
        },
        "frame_count": offset,
        "sample_count": edits[-1]["output_end_sample"],
        "duration": float(offset / fps),
        "continuous_picture": continuous,
        "full_source_picture": full,
        "full_source_audio": full_audio,
        "scenes": edits,
    }
    write(project.work / "edit-timeline.json", timeline)
    return timeline
