"""Resolve cuts once on the audited source frame grid for picture and audio."""

from fractions import Fraction
import json
import math
import subprocess
from .core import need, read, write

VERSION = 1
RATE = 48000


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
    total = float(info["format"]["duration"])
    coverage(project, total)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    fps = Fraction(video["r_frame_rate"])
    need(fps > 0, "Source has no usable frame rate")
    path = project.work / "frame-grid.json"
    grid = read(path) if path.exists() else {}
    identity = {"source_sha256": source_hash, "fps": str(fps), "version": VERSION}
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
                    "frame=best_effort_timestamp_time",
                    "-of",
                    "json",
                    str(project.source),
                ],
                encoding="utf-8",
            )
        )["frames"]
        origin = float(info["format"].get("start_time", 0))
        times = [float(f["best_effort_timestamp_time"]) - origin for f in frames]
        need(
            bool(times)
            and all(abs(t - i / float(fps)) < 1e-5 for i, t in enumerate(times)),
            "Irregular frame timing or offset video origin is unsupported; deliberately normalize a separate source copy before using this frame-grid workflow",
        )
        grid = {
            "identity": identity,
            "frame_count": len(times),
            "uniform_frame_grid_verified": True,
        }
        write(path, grid)
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
    need(
        project.config.get("scope") != "full" or full,
        "Resolved full-episode timeline does not cover every source frame",
    )
    timeline = {
        "version": VERSION,
        "source_sha256": source_hash,
        "fps": str(fps),
        "source_frame_count": grid["frame_count"],
        "source_duration": total,
        "frame_count": offset,
        "sample_count": edits[-1]["output_end_sample"],
        "duration": float(offset / fps),
        "continuous_picture": continuous,
        "full_source_picture": full,
        "scenes": edits,
    }
    write(project.work / "edit-timeline.json", timeline)
    return timeline
