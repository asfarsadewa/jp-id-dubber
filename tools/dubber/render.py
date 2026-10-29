"""480p delivery, exactly one Indonesian track, verified before atomic replacement."""

from fractions import Fraction
import json
import subprocess
import time
import soundfile as sf
from .core import need, probe, read, run, sha, write
from .edits import audio_is_covered, coverage, nearest_tick, selected_audio_bounds
from .audio import captions, require_mix


def boxes(path):
    result = []
    size = path.stat().st_size
    offset = 0
    with path.open("rb") as stream:
        while offset < size:
            stream.seek(offset)
            header = stream.read(8)
            need(len(header) == 8, "Truncated MP4 box")
            length = int.from_bytes(header[:4], "big")
            result.append(header[4:8].decode("ascii"))
            if length == 1:
                length = int.from_bytes(stream.read(8), "big")
            if length == 0:
                length = size - offset
            need(length >= 8 and offset + length <= size, "Invalid MP4 box")
            offset += length
    return result


def verify(project, path=None):
    require_mix(project)
    if path is None:
        project.require("render", {"height": 480, "audio": "ind-only"})
    path = path or project.output
    info = probe(path)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    source = next(
        s
        for s in read(project.work / "probe.json")["streams"]
        if s["codec_type"] == "video"
    )
    need(
        len(videos) == 1 and len(audio) == 1 and len(info["streams"]) == 2,
        "Delivery must contain exactly one video and one Indonesian audio stream",
    )
    video = videos[0]
    a = audio[0]
    timeline = read(project.work / "edit-timeline.json")
    expected = timeline["duration"]
    coverage(project, timeline["source_duration"])
    selected_audio_covered = audio_is_covered(
        timeline["scenes"],
        Fraction(timeline["fps"]),
        timeline["selected_streams"]["audio"],
    )
    source_ranges = timeline["scenes"]
    full_picture_covered = (
        all(
            a["source_end_frame"] == b["source_start_frame"]
            for a, b in zip(source_ranges, source_ranges[1:])
        )
        and timeline["scenes"][0]["source_start_frame"] == 0
        and timeline["scenes"][-1]["source_end_frame"] == timeline["source_frame_count"]
    )
    delivered_audio = selected_audio_bounds(
        path, a, Fraction(info["format"].get("start_time", "0"))
    )
    audio_tick = Fraction(a["time_base"])
    delivered_audio_complete = nearest_tick(
        Fraction(delivered_audio["start"]) / audio_tick
    ) == 0 and nearest_tick(
        Fraction(delivered_audio["end"]) / audio_tick
    ) >= nearest_tick(Fraction(timeline["sample_count"], 48000) / audio_tick)
    sar = Fraction(source.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    dar = source["width"] * float(sar) / source["height"]
    boxnames = boxes(path)
    checks = {
        "480p": video["height"] == 480,
        "display_aspect_preserved": abs(video["width"] / video["height"] - dar)
        <= 2 / 480,
        "square_pixels": video.get("sample_aspect_ratio") == "1:1",
        "h264_8bit": video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p",
        "frame_rate_preserved": Fraction(video["r_frame_rate"])
        == Fraction(source["r_frame_rate"]),
        "exact_picture_frame_count": int(video.get("nb_frames", -1))
        == timeline["frame_count"],
        "full_scope_coverage": project.config.get("scope") != "full"
        or full_picture_covered
        and selected_audio_covered,
        "full_selected_audio_coverage": project.config.get("scope") != "full"
        or selected_audio_covered,
        "delivered_audio_coverage": delivered_audio_complete,
        "master_audio_sample_count": sf.info(project.work / "mix.id.wav").frames
        == timeline["sample_count"],
        "indonesian_only": a.get("tags", {}).get("language") == "ind"
        and a["disposition"]["default"] == 1,
        "aac_stereo": a["codec_name"] == "aac"
        and a["channels"] == 2
        and a["sample_rate"] == "48000",
        "duration": abs(float(info["format"]["duration"]) - expected)
        < max(0.12, 2 / float(Fraction(source["r_frame_rate"]))),
        "faststart": boxnames.index("moov") < boxnames.index("mdat"),
        "original_source_unchanged": sha(project.source)
        == read(project.work / "source-identity.json")["sha256"],
    }
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    checks["full_decode"] = decoded.returncode == 0 and not decoded.stderr.strip()
    (project.work / "decode.log").write_text(decoded.stderr, encoding="utf-8")
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "output_probe": info,
        "duration_seconds": float(info["format"]["duration"]),
        "size_bytes": path.stat().st_size,
        "sha256": sha(path),
        "scope": project.config.get("scope", "pilot"),
        "edit_timeline": timeline,
        "delivered_audio": delivered_audio,
        "audio_policy": "single Bahasa Indonesia track; songs in passthrough intervals retain original singing",
    }
    write(project.work / "verification.json", report)
    need(report["passed"], "Export verification failed; inspect verification.json")
    return report


def render(project, overwrite=False):
    require_mix(project)
    output = project.output
    need(
        overwrite or not output.exists(),
        "Existing export requires --overwrite; replacement happens only after candidate validation",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partial.mp4")
    source = next(
        s
        for s in read(project.work / "probe.json")["streams"]
        if s["codec_type"] == "video"
    )
    sar = Fraction(source.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    width = round(source["width"] * float(sar) / source["height"] * 480 / 2) * 2
    timeline = read(project.work / "edit-timeline.json")
    scenes = timeline["scenes"]
    fps = Fraction(timeline["fps"])
    args = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        project.source,
        "-i",
        project.work / "mix.id.wav",
    ]
    if timeline["continuous_picture"]:
        # Continuous full episodes (and adjacent excerpts) retain one continuous
        # picture stream. No separately rounded video segments exist.
        filters = [
            f"[0:v:0]trim=start_frame={scenes[0]['source_start_frame']}:end_frame={scenes[-1]['source_end_frame']}[joined]"
        ]
    else:
        filters = [
            f"[0:v:0]split={len(scenes)}"
            + "".join(f"[s{i}]" for i in range(len(scenes)))
        ]
        filters += [
            f"[s{i}]trim=start_frame={s['source_start_frame']}:end_frame={s['source_end_frame']},setpts=PTS-STARTPTS[v{i}]"
            for i, s in enumerate(scenes)
        ]
        filters += [
            "".join(f"[v{i}]" for i in range(len(scenes)))
            + f"concat=n={len(scenes)}:v=1:a=0[joined]"
        ]
    # Assign output times from the global frame count, rather than carrying
    # concat's per-segment duration estimates into the delivery timeline.
    filters += [
        f"[joined]setpts=N*{fps.denominator}/({fps.numerator}*TB),scale={width}:480:flags=lanczos,setsar=1,format=yuv420p[v]"
    ]
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "25",
        "-r",
        source["r_frame_rate"],
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-frames:v",
        timeline["frame_count"],
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-metadata:s:a:0",
        "language=ind",
        "-metadata:s:a:0",
        "title=Bahasa Indonesia",
        "-disposition:a:0",
        "default",
        partial,
    ]
    start = time.time()
    run(args, project.work / "encode.log")
    report = verify(project, partial)
    partial.replace(output)
    report["output_probe"]["format"]["filename"] = str(output)
    report["encode_verify_seconds"] = time.time() - start
    write(project.work / "verification.json", report)
    captions(project)
    project.record(
        "render",
        [
            project.work / "mix.id.wav",
            project.work / "timeline.json",
            project.work / "edit-timeline.json",
        ],
        [output],
        {"height": 480, "audio": "ind-only"},
    )
    print(
        json.dumps(
            {
                "video": str(output),
                "size_MB": round(output.stat().st_size / 1e6, 1),
                "duration_seconds": report["duration_seconds"],
                "checks": report["checks"],
            },
            indent=2,
        )
    )
