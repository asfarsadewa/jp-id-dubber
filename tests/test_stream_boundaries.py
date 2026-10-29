"""Selected-stream clocks and event compatibility, with synthetic media only."""

from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from test_contracts import make_project
from test_external_review import seal_transcript
from dubber.core import ROOT, Project, probe, read, run, write
from dubber.editorial import prepare, review
from dubber.voices import generate
from dubber.audio import fit
from dubber.render import render, verify
from dubber.edits import audit_frame_ticks


def source_project(root, video=6, audio=6, suffix="mp4", scope="full", fps="24"):
    source = root / "input" / f"source.{suffix}"
    source.parent.mkdir(parents=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=navy:s=160x120:r={fps}:d={video}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={audio}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "flac" if suffix == "mkv" else "aac",
            "-ac",
            "2",
            source,
        ]
    )
    config = read(ROOT / "project.example.json")
    config.update(
        slug="stream-boundaries",
        source=str(source),
        scope=scope,
        scenes=[
            {
                "id": "selected",
                "start": 0,
                "end": max(video, audio),
                "mode": "passthrough",
            }
        ],
    )
    path = root / "local/project.json"
    write(path, config)
    return Project(path, root=root)


def finish_passthrough(project):
    project.record(
        "transcribe", [], [], {k: project.model(k) for k in ["scribe", "transcribe"]}
    )
    write(
        project.work / "script.json",
        {
            "source_notes": "Synthetic passthrough fixture",
            "characters": [],
            "lines": [],
            "omissions": [],
        },
    )
    review(project, "No generated performances; synthetic selected source only")
    generate(project, submit=True)
    project.record("separate", [], [], {})
    fit(project)
    render(project)
    return verify(project)


class StreamBoundaryRegressions(unittest.TestCase):
    def test_pilot_before_the_audio_tail_remains_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp), video=5.5, audio=6, scope="pilot")
            config = read(p.config_path)
            config["scenes"][0]["end"] = 3
            write(p.config_path, config)
            p = Project(p.config_path, root=p.root)
            prepare(p)
            self.assertTrue(finish_passthrough(p)["passed"])

    def test_shorter_audio_stream_does_not_discard_any_source_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp), video=6, audio=5.5)
            prepare(p)
            report = finish_passthrough(p)
            self.assertTrue(report["checks"]["full_selected_audio_coverage"])
            self.assertTrue(report["checks"]["delivered_audio_coverage"])

    def test_full_coverage_uses_selected_audio_not_a_longer_unselected_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp))
            source = p.source.with_name("extra-track.mp4")
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    p.source,
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=660:sample_rate=48000:duration=7",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a:0",
                    "copy",
                    "-c:a:1",
                    "aac",
                    source,
                ]
            )
            config = read(p.config_path)
            config["source"] = str(source)
            write(p.config_path, config)
            p = Project(p.config_path, root=p.root)
            self.assertGreater(float(probe(source)["format"]["duration"]), 6.5)
            prepare(p)
            report = finish_passthrough(p)
            self.assertAlmostEqual(report["duration_seconds"], 6.0, places=3)
            self.assertEqual(
                report["edit_timeline"]["selected_streams"]["audio"]["index"], 1
            )

    def test_final_verification_rejects_shortened_encoded_audio_even_with_full_picture(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp))
            prepare(p)
            finish_passthrough(p)
            candidate = p.output.with_name("missing-audio-tail.mp4")
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    p.output,
                    "-filter_complex",
                    "[0:a:0]atrim=end=5.5[a]",
                    "-map",
                    "0:v:0",
                    "-map",
                    "[a]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-metadata:s:a:0",
                    "language=ind",
                    "-disposition:a:0",
                    "default",
                    "-movflags",
                    "+faststart",
                    candidate,
                ]
            )
            with self.assertRaises(ValueError):
                verify(p, candidate)
            report = read(p.work / "verification.json")
            self.assertTrue(report["checks"]["exact_picture_frame_count"])
            self.assertFalse(report["checks"]["delivered_audio_coverage"])

    def test_tick_quantization_still_rejects_irregular_missing_and_offset_frames(self):
        valid = [{"best_effort_timestamp": t} for t in [0, 42, 83, 125, 167]]
        audit_frame_ticks(valid, Fraction(24), Fraction(1, 1000), Fraction(0))
        bad = [{"best_effort_timestamp": t} for t in [0, 43, 83, 125, 167]]
        missing = valid[:1] + valid[2:]
        offset = [
            {"best_effort_timestamp": f["best_effort_timestamp"] + 100} for f in valid
        ]
        for frames in [bad, missing, offset]:
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                audit_frame_ticks(frames, Fraction(24), Fraction(1, 1000), Fraction(0))

    def test_audible_audio_tail_is_rejected_before_preparation_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp), video=5.5, audio=6)
            tail = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "5.5",
                    "-i",
                    str(p.source),
                    "-map",
                    "0:a:0",
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-f",
                    "f32le",
                    "-",
                ]
            )
            samples = np.frombuffer(tail, dtype="<f4")
            self.assertGreaterEqual(len(samples), 24000)
            self.assertGreater(float(np.sqrt(np.mean(samples**2))), 0.01)
            with self.assertRaisesRegex(
                ValueError, "[Aa]udio.*tail|[Aa]udio.*beyond|selected audio"
            ):
                prepare(p)
            self.assertFalse((p.work / "stages/prepare.json").exists())

    def test_millisecond_quantized_cfr_mkv_can_prepare_and_export(self):
        for fps, seconds in [("24", 6), ("24000/1001", 6.006)]:
            with self.subTest(fps=fps), tempfile.TemporaryDirectory() as tmp:
                p = source_project(
                    Path(tmp), video=seconds, audio=seconds, suffix="mkv", fps=fps
                )
                info = probe(p.source)
                video = next(s for s in info["streams"] if s["codec_type"] == "video")
                self.assertEqual(Fraction(video["time_base"]), Fraction(1, 1000))
                self.assertEqual(Fraction(video["r_frame_rate"]), Fraction(fps))
                prepare(p)
                report = finish_passthrough(p)
                self.assertTrue(report["passed"])
                self.assertEqual(
                    int(report["output_probe"]["streams"][0]["nb_frames"]), 144
                )


class EventCompatibilityRegressions(unittest.TestCase):
    def test_sequential_group_needs_explicit_complete_performer_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            script["characters"].append(
                {
                    **script["characters"][0],
                    "id": "second",
                    "name": "Second",
                    "voice": "Leda",
                }
            )
            units = read(p.folder(p.scenes[0]) / "units.json")
            units.append({**units[0], "id": 2, "start": 2.0, "end": 2.9})
            seal_transcript(p, units)
            script["lines"].append(
                {
                    **script["lines"][0],
                    "id": "line-02",
                    "source_ids": [2],
                    "start": 2.0,
                    "end": 2.9,
                    "character_id": "second",
                }
            )
            event = {
                "id": "group-call",
                "scene": "dialogue",
                "start": 0.5,
                "end": 2.9,
                "character_id": "",
                "character_ids": ["lead", "second"],
                "kind": "shout",
                "decision": "handled_as_line",
                "line_ids": ["line-01", "line-02"],
                "reason": "Two sequential calls",
                "evidence": "Bounded source listening confirms both performers",
            }
            script["performance_events"] = [event]
            write(p.work / "script.json", script)
            review(p, "Both source characters have matching serial performances")
            p.approved()
            event["line_ids"] = ["line-01"]
            write(p.work / "script.json", script)
            with self.assertRaisesRegex(ValueError, "performer identity"):
                review(p, "A missing group member cannot count as handled")
            event["character_id"] = "second"
            write(p.work / "script.json", script)
            with self.assertRaises(ValueError):
                review(p, "Group metadata cannot override a known single identity")

    def test_declared_overlap_cannot_be_claimed_handled_after_source_bounds_are_serialized(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            script["performance_events"] = [
                {
                    "id": "known-overlap",
                    "scene": "dialogue",
                    "start": 0.5,
                    "end": 1.6,
                    "character_id": "lead",
                    "kind": "overlap",
                    "decision": "handled_as_line",
                    "line_ids": ["line-01"],
                    "reason": "The source event is known to be simultaneous",
                    "evidence": "Bounded source listening",
                }
            ]
            write(p.work / "script.json", script)
            with self.assertRaisesRegex(ValueError, "overlap event"):
                review(p, "An overlap cannot be handled by serial lines")

    def test_wrong_character_cannot_handle_known_character_gasp(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            script["characters"].append(
                {
                    **script["characters"][0],
                    "id": "second",
                    "name": "Second",
                    "voice": "Leda",
                }
            )
            script["performance_events"] = [
                {
                    "id": "gasp",
                    "scene": "dialogue",
                    "start": 0.5,
                    "end": 1.6,
                    "character_id": "second",
                    "kind": "gasp",
                    "decision": "handled_as_line",
                    "line_ids": ["line-01"],
                    "reason": "Observed reaction",
                    "evidence": "Bounded source listening",
                }
            ]
            write(p.work / "script.json", script)
            with self.assertRaisesRegex(ValueError, "character|identity|performer"):
                review(p, "Wrong actor must not seal as handled")
                p.approved()

    def test_declared_source_overlap_cannot_be_handled_by_serialized_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            script["characters"].append(
                {
                    **script["characters"][0],
                    "id": "second",
                    "name": "Second",
                    "voice": "Leda",
                }
            )
            units = read(p.folder(p.scenes[0]) / "units.json")
            units[0].update(start=0.5, end=1.0)
            units.append({**units[0], "id": 2})
            seal_transcript(p, units)
            script["lines"][0].update(start=0.5, end=0.75)
            script["lines"].append(
                {
                    **script["lines"][0],
                    "id": "line-02",
                    "source_ids": [2],
                    "start": 0.75,
                    "end": 1.0,
                    "character_id": "second",
                }
            )
            script["performance_events"] = [
                {
                    "id": "simultaneous",
                    "scene": "dialogue",
                    "start": 0.5,
                    "end": 1.0,
                    "character_id": "",
                    "kind": "overlap",
                    "decision": "handled_as_line",
                    "line_ids": ["line-01", "line-02"],
                    "reason": "Source contains two simultaneous voices",
                    "evidence": "Bounded source listening",
                }
            ]
            write(p.work / "script.json", script)
            for kind in ["overlap", "shout"]:
                script["performance_events"][0]["kind"] = kind
                write(p.work / "script.json", script)
                with (
                    self.subTest(kind=kind),
                    self.assertRaisesRegex(ValueError, "overlap|simultaneous"),
                ):
                    review(p, "Sequential rewrites do not handle source simultaneity")
                    p.approved()


if __name__ == "__main__":
    unittest.main()
