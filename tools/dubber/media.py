"""Offline source-tail preservation and smaller copies of verified deliveries."""

from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import uuid
from .core import need, probe, read, run, sha, write
from .edits import audit_frame_ticks, selected_audio_bounds
from .render import picture_filters, verify
from .audio import require_mix

COMPACT_SETTINGS = {
    "height": 480,
    "audio": "copy-ind-only",
    "preset": "veryslow",
    "crf": 29,
}


def audio_hash(path, codec="copy"):
    result = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            codec,
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        encoding="utf-8",
    ).strip()
    need(result.startswith("SHA256="), "Could not hash the complete selected audio")
    return result


def frame_hashes(path, pix_fmt):
    text = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-pix_fmt",
            pix_fmt,
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ],
        encoding="utf-8",
    )
    return [
        line.rsplit(",", 1)[1].strip()
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]


def condition(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    need(
        source.is_file() and source != output and not output.exists(),
        "Conditioning needs an existing source and a new, separate output",
    )
    need(
        output.suffix.lower() == ".mp4",
        "Use an MP4 destination for the conditioned source",
    )
    info = probe(source)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    need(
        video is not None and audio is not None,
        "Conditioning requires selected picture and audio streams",
    )
    origin = Fraction(info["format"].get("start_time", "0"))
    need(
        origin == 0,
        "Conditioning requires a zero media origin; review offset sources separately",
    )
    fps = Fraction(video["r_frame_rate"])
    need(fps > 0, "Conditioning requires a usable constant frame rate")
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
                str(source),
            ],
            encoding="utf-8",
        )
    )["frames"]
    audit_frame_ticks(frames, fps, Fraction(video["time_base"]), origin)
    bounds = selected_audio_bounds(source, audio, origin)
    hold = max(0, math.ceil(Fraction(bounds["end"]) * fps) - len(frames))
    need(
        hold > 0,
        "Selected audio has no tail beyond the picture; no conditioning needed",
    )
    source_sha = sha(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(
        output.stem + "." + uuid.uuid4().hex[:8] + ".partial.mp4"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-n",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            f"tpad=stop_mode=clone:stop={hold}",
            "-c:v",
            "libx264",
            "-qp",
            "0",
            "-preset",
            "fast",
            "-pix_fmt",
            video["pix_fmt"],
            "-r",
            str(fps),
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(fps.numerator),
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            partial,
        ],
        output.with_suffix(".conditioning.log"),
    )
    old_frames, new_frames = (
        frame_hashes(source, video["pix_fmt"]),
        frame_hashes(partial, video["pix_fmt"]),
    )
    after = probe(partial)
    after_audio = next(s for s in after["streams"] if s["codec_type"] == "audio")
    after_bounds = selected_audio_bounds(
        partial, after_audio, Fraction(after["format"].get("start_time", "0"))
    )
    checks = {
        "original_unchanged": sha(source) == source_sha,
        "decoded_picture_preserved": len(old_frames) == len(frames)
        and new_frames[: len(frames)] == old_frames,
        "held_frames_only": new_frames[len(frames) :] == [old_frames[-1]] * hold,
        "audio_packets_preserved": audio_hash(source) == audio_hash(partial),
        "decoded_audio_preserved": audio_hash(source, "pcm_s16le")
        == audio_hash(partial, "pcm_s16le"),
        "audio_bounds_preserved": all(
            Fraction(bounds[k]) == Fraction(after_bounds[k]) for k in ("start", "end")
        ),
        "picture_covers_audio": Fraction(len(new_frames), 1) / fps
        >= Fraction(bounds["end"]),
        "two_selected_streams": len(after["streams"]) == 2,
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "source": str(source),
        "source_sha256": source_sha,
        "output": str(output),
        "sha256": sha(partial),
        "held_frames": hold,
        "fps": str(fps),
        "duration_seconds": float(after["format"]["duration"]),
    }
    write(output.with_suffix(".conditioning.json"), report)
    need(
        report["passed"],
        "Source conditioning failed preservation checks; candidate retained for inspection",
    )
    need(
        not output.exists(),
        "Conditioning destination appeared during processing; refused replacement",
    )
    partial.rename(output)
    print(json.dumps(report, indent=2))
    return report


def require_compact(project):
    require_mix(project)
    project.require("render", {"height": 480, "audio": "ind-only"})
    return project.require("compact", COMPACT_SETTINGS)


def compact(project, output=None, overwrite=False):
    require_mix(project)
    project.require("render", {"height": 480, "audio": "ind-only"})
    original = project.output
    output = (
        project.path(output)
        if output
        else original.with_name(original.stem + ".small.mp4")
    )
    need(
        output.suffix.lower() == ".mp4" and output not in {original, project.source},
        "Compact output must be a separate MP4; original delivery/source cannot be replaced",
    )
    need(
        overwrite or not output.exists(), "Existing compact output requires --overwrite"
    )
    original_sha = sha(original)
    compact_report = project.work / "verification.small.json"
    canonical_path = project.work / "verification.json"
    canonical = canonical_path.read_bytes() if canonical_path.exists() else None
    source = next(
        s
        for s in read(project.work / "probe.json")["streams"]
        if s["codec_type"] == "video"
    )
    timeline = read(project.work / "edit-timeline.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(
        output.stem + "." + uuid.uuid4().hex[:8] + ".partial.mp4"
    )
    report_path = project.work / (partial.stem + ".verification.json")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-n",
            "-i",
            project.source,
            "-i",
            original,
            "-filter_complex",
            ";".join(picture_filters(source, timeline)),
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryslow",
            "-crf",
            "29",
            "-r",
            source["r_frame_rate"],
            "-c:a",
            "copy",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-metadata:s:a:0",
            "language=ind",
            "-disposition:a:0",
            "default",
            "-movflags",
            "+faststart",
            partial,
        ],
        project.work / "encode.small.log",
    )
    report = verify(project, partial, report_path=report_path)
    report["checks"].update(
        {
            "audio_packets_unchanged": audio_hash(original) == audio_hash(partial),
            "decoded_audio_unchanged": audio_hash(original, "pcm_s16le")
            == audio_hash(partial, "pcm_s16le"),
            "original_delivery_unchanged": sha(original) == original_sha,
            "smaller": partial.stat().st_size < original.stat().st_size,
        }
    )
    report.update(
        passed=all(report["checks"].values()),
        original_bytes=original.stat().st_size,
        reduction_percent=100 * (1 - partial.stat().st_size / original.stat().st_size),
        output=str(output),
        encoding=COMPACT_SETTINGS,
    )
    write(report_path, report)
    need(
        report["passed"],
        "Compact verification failed or file did not shrink; original retained",
    )
    need(
        (canonical_path.read_bytes() if canonical_path.exists() else None) == canonical,
        "Canonical delivery verification was unexpectedly modified",
    )
    need(
        overwrite or not output.exists(),
        "Compact destination appeared during encoding; refused replacement",
    )
    partial.replace(output)
    report["output_probe"]["format"]["filename"] = str(output)
    write(compact_report, report)
    project.record(
        "compact",
        [original, project.work / "mix.id.wav", project.work / "edit-timeline.json"],
        [output, compact_report],
        COMPACT_SETTINGS,
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("passed", "output", "size_bytes", "reduction_percent")
            },
            indent=2,
        )
    )
    return report
