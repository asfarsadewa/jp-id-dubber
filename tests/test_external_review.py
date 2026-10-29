"""Observable regressions for the external offline review; no live providers."""

import copy
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import soundfile as sf

from test_contracts import Response, make_project
from dubber.core import ROOT, Project, digest, read, run, sha, write
from dubber.editorial import adaptation, prepare, review, source_units, validate_script
from dubber.providers import Jobs
from dubber.voices import generate, payload, specs, voice_spec
from dubber.performance import DEFAULT_REGISTER
from dubber.audio import fit
from dubber.render import render, verify


def seal_transcript(project, units):
    folder = project.folder(project.scenes[0])
    write(folder / "units.json", units)
    project.record(
        "transcribe",
        [folder / "source.mp3"],
        [folder / name for name in ["units.json", "scribe.json", "independent.json"]],
        {key: project.model(key) for key in ["scribe", "transcribe"]},
    )


class CacheRegressions(unittest.TestCase):
    def test_missing_key_check_never_claims_an_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)

            def missing():
                raise ValueError("Missing fixture credential")

            with self.assertRaises(ValueError):
                jobs.request(
                    "clip",
                    {},
                    lambda: self.fail("Unexpected POST"),
                    before_send=missing,
                )
            self.assertFalse(jobs.paths("clip", {})[0].exists())

    def test_exact_completed_legacy_decimal_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            meta = {"audio": "original"}
            label = "qa-0.5-40.25"
            base = Path(tmp) / (label + "-" + digest(meta)[:24])
            result = base.with_suffix(".result.json")
            write(result, {"text": "original"})
            write(
                base.with_suffix(".receipt.json"),
                {"status": "completed", "metadata": meta, "result_sha256": sha(result)},
            )
            actual = jobs.request(
                label,
                meta,
                lambda: self.fail("Unexpected POST"),
                before_send=lambda: self.fail(
                    "Cached result should not need credentials"
                ),
            )
            self.assertEqual(actual["text"], "original")
            self.assertTrue(jobs.paths(label, meta)[1].exists())

    def test_fractional_labels_and_audio_changes_get_distinct_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            sent = []
            for label, audio in [
                ("rendered-render-0.5-40.25", "a"),
                ("rendered-render-0.8-40.25", "b"),
                ("rendered-render-0.8-40.25", "c"),
            ]:
                result = jobs.request(
                    label,
                    {"audio": audio},
                    lambda: (sent.append(audio), Response({"text": audio}))[1],
                )
                self.assertEqual(result["text"], audio)
            self.assertEqual(sent, ["a", "b", "c"])

    def test_matching_result_hash_cannot_hide_receipt_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            jobs.request("clip", {"audio": "a"}, lambda: Response({"text": "a"}))
            receipt, _ = jobs.paths("clip", {"audio": "a"})
            state = read(receipt)
            state["metadata"] = {"audio": "b"}
            write(receipt, state)
            with self.assertRaises(ValueError):
                jobs.request(
                    "clip", {"audio": "a"}, lambda: self.fail("Unexpected POST")
                )


class SourceRegressions(unittest.TestCase):
    def test_correction_can_split_a_merged_source_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            raw = p.folder(p.scenes[0]) / "units.json"
            before = sha(raw)
            write(
                p.work / "source-overrides.json",
                [
                    {
                        "op": "split",
                        "scene": "dialogue",
                        "source_id": 1,
                        "units": [
                            {
                                "id": "split-first",
                                "start": 0.5,
                                "end": 0.9,
                                "ja": "はい。",
                            },
                            {
                                "id": "split-second",
                                "start": 1.0,
                                "end": 1.6,
                                "ja": "行け。",
                            },
                        ],
                        "reason": "Merged utterances have different speakers",
                        "evidence": "Bounded source +0.5 to +1.6 s",
                    }
                ],
            )
            units = source_units(p)["dialogue"]
            self.assertEqual([u["id"] for u in units], ["split-first", "split-second"])
            self.assertEqual(sha(raw), before)

    def test_insert_requires_evidence_valid_bounds_and_unique_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            base = {
                "op": "insert",
                "scene": "dialogue",
                "unit": {"id": "manual-line", "start": 0.1, "end": 0.3, "ja": "はい。"},
                "reason": "Confirmed missed utterance",
                "evidence": "Bounded listening",
            }
            bad = []
            entry = copy.deepcopy(base)
            entry["evidence"] = ""
            bad.append([entry])
            entry = copy.deepcopy(base)
            entry["unit"]["end"] = 99
            bad.append([entry])
            bad.append([base, base])
            for corrections in bad:
                with self.subTest(corrections=corrections):
                    write(p.work / "source-overrides.json", corrections)
                    with self.assertRaises(ValueError):
                        source_units(p)

    def test_switching_source_path_rejects_all_resumed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            other = p.source.with_name("other.mp4")
            shutil.copyfile(p.source, other)
            config = read(p.config_path)
            config["source"] = str(other)
            write(p.config_path, config)
            changed = Project(p.config_path, root=p.root)
            for check in [
                changed.prepared,
                changed.approved,
                lambda: source_units(changed),
                lambda: specs(changed),
            ]:
                with self.subTest(check=check), self.assertRaises(ValueError):
                    check()

    def test_partial_pilot_cannot_be_relabelled_full_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            config = read(p.config_path)
            config["scenes"] = config["scenes"][:1]
            write(p.config_path, config)
            p = Project(p.config_path, root=p.root)
            prepare(p)
            config["scope"] = "full"
            write(p.config_path, config)
            changed = Project(p.config_path, root=p.root)
            with self.assertRaises(ValueError):
                changed.prepared()

    def test_confirmed_missing_utterance_can_be_inserted_without_editing_raw_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            seal_transcript(p, [])
            raw = p.folder(p.scenes[0]) / "units.json"
            before = sha(raw)
            write(
                p.work / "source-overrides.json",
                [
                    {
                        "op": "insert",
                        "scene": "dialogue",
                        "unit": {
                            "id": "manual-ack",
                            "start": 0.5,
                            "end": 1.6,
                            "ja": "はい。",
                            "speaker": "manual",
                        },
                        "reason": "Primary ASR omitted a confirmed acknowledgment",
                        "evidence": "Independent ASR and bounded source listening at +0.5 to +1.6 s",
                    }
                ],
            )
            units = source_units(p)["dialogue"]
            self.assertEqual(units[0]["id"], "manual-ack")
            self.assertEqual(units[0]["ja"], "はい。")
            self.assertEqual(sha(raw), before)

    def test_moved_scene_uses_current_source_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            source = p.source.with_name("red-blue.mp4")
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=320x240:r=24:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=320x240:r=24:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=d=6:r=48000",
                    "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "2:a",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    source,
                ]
            )
            config = read(p.config_path)
            config.update(
                slug="frame-review", source=str(source), scenes=config["scenes"][:1]
            )
            write(p.config_path, config)
            (p.root / "prompts").mkdir()
            shutil.copyfile(ROOT / "prompts/adapt.md", p.root / "prompts/adapt.md")
            script = read(p.work / "script.json")
            units = read(p.folder(p.scenes[0]) / "units.json")
            seen = []
            for start in [0, 3]:
                config["scenes"][0].update(start=start, end=start + 3)
                write(p.config_path, config)
                current = Project(p.config_path, root=p.root)
                prepare(current)
                folder = current.folder(current.scenes[0])
                write(folder / "scribe.json", {})
                write(folder / "independent.json", {"text": "Synthetic"})
                seal_transcript(current, units)

                def fake_adapt(project, payload, schema, images, instructions):
                    with Image.open(images[0]) as image:
                        seen.append(np.array(image)[100, 100].astype(int))
                    return script

                with patch("dubber.providers.adapt", side_effect=fake_adapt):
                    adaptation(current)
            self.assertGreater(seen[0][0] - seen[0][2], 100)
            self.assertGreater(
                seen[1][2] - seen[1][0],
                100,
                "Moved scene reused an earlier character-reference frame",
            )


class VoiceRegressions(unittest.TestCase):
    def test_identical_occurrences_share_one_inflight_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            line, spec = specs(p)[0]
            jobs = Jobs(p.work / "api")
            receipt, result = jobs.paths("voice", spec)
            response = read(result)
            # Remove only a fake completed fixture, never a real paid attempt.
            receipt.unlink()
            result.unlink()
            units = read(p.folder(p.scenes[0]) / "units.json")
            units.append({**units[0], "id": 2, "start": 2.0, "end": 2.9})
            seal_transcript(p, units)
            script = read(p.work / "script.json")
            script["lines"].append(
                {
                    **script["lines"][0],
                    "id": "line-02",
                    "source_ids": [2],
                    "start": 2.0,
                    "end": 2.9,
                }
            )
            write(p.work / "script.json", script)
            review(p, "Two distinct source occurrences with identical spoken response")

            def send(*args, **kwargs):
                time.sleep(0.15)
                return Response(response)

            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "fixture"}),
                patch("requests.post", side_effect=send) as post,
            ):
                generate(p, submit=True)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(len(read(p.work / "voice-takes.json")), 2)


class PerformanceRegressions(unittest.TestCase):
    def test_significant_shift_needs_evidence_and_is_recorded_before_voices(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            script["lines"][0].update(start=0.8, end=1.9)
            with self.assertRaises(ValueError):
                validate_script(p, script)
            script["lines"][0]["timing_adjustment"] = {
                "reason": "Measured held-shot gap permits delayed delivery",
                "evidence": "Source +0.5 to +1.9 s inspected; no following utterance displaced",
            }
            write(p.work / "script.json", script)
            review(p, "Explicitly measured delivery shift")
            report = read(p.work / "script-timing.json")["performances"][0]
            self.assertTrue(report["significant_shift"])
            self.assertEqual(report["derived_start"], 0.5)

    def test_repeated_source_id_requires_complete_declared_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            first = script["lines"][0]
            first["end"] = 0.95
            second = {**first, "id": "line-02", "start": 1.05, "end": 1.6}
            script["lines"].append(second)
            with self.assertRaises(ValueError):
                validate_script(p, script)
            for index, line in enumerate(script["lines"], 1):
                line["source_splits"] = [
                    {
                        "source_id": 1,
                        "part": index,
                        "parts": 2,
                        "start": line["start"],
                        "end": line["end"],
                        "reason": "One source breath phrase intentionally split",
                        "evidence": "Bounded source contains a pause between the two parts",
                    }
                ]
            validate_script(p, script)
            second["source_splits"][0]["part"] = 1
            with self.assertRaises(ValueError):
                validate_script(p, script)

    def test_relationship_registers_are_explicit_and_pronunciation_is_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            character = script["characters"][0]
            character["register"] = {
                "default": copy.deepcopy(DEFAULT_REGISTER),
                "relationships": [
                    {
                        "addressee_id": "commander",
                        "self": "saya",
                        "address": "Anda",
                        "forms_of_address": ["Komandan"],
                        "honorifics": [],
                        "notes": "Respectful report to commanding officer",
                    }
                ],
            }
            script["lines"][0]["addressee_id"] = "commander"
            validate_script(p, script)
            line = {**script["lines"][0], "text": "Mesin A, maju!"}
            p.config["pronunciations"] = [
                {"term": "Mesin A", "spoken_form": "Me-sin A"}
            ]
            spec = voice_spec(p, line, character)
            body = payload(spec)
            content = body["input"][0]["content"][0]
            self.assertEqual(content["text"], "Mesin A, maju!")
            self.assertIn(
                "Pronounce Mesin A as Me-sin A", content["annotations"][0]["style"]
            )
            # Unused pronunciation entries do not trigger fresh takes.
            p.config["pronunciations"].append(
                {"term": "Different machine", "spoken_form": "Unused"}
            )
            self.assertEqual(voice_spec(p, line, character), spec)
            p.config["pronunciations"].append({"term": "maju", "spoken_form": "ma-ju"})
            unrelated = {**line, "text": "Kemajuan sudah jelas."}
            with_entry = voice_spec(p, unrelated, character)
            p.config["pronunciations"].pop()
            self.assertEqual(voice_spec(p, unrelated, character), with_entry)

    def test_vocal_effort_requires_a_treatment_before_final_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            script = read(p.work / "script.json")
            event = {
                "id": "shouted-order",
                "scene": "dialogue",
                "start": 0.5,
                "end": 1.6,
                "character_id": "lead",
                "kind": "shout",
                "decision": "unresolved",
                "line_ids": [],
                "reason": "Observed emphatic delivery",
                "evidence": "Bounded source listening",
            }
            script["performance_events"] = [event]
            validate_script(p, script)
            write(p.work / "script.json", script)
            with self.assertRaises(ValueError):
                review(p, "Unresolved event cannot disappear")
            event.update(decision="handled_as_line", line_ids=["line-01"])
            write(p.work / "script.json", script)
            review(p, "Shout is represented by the directed line")
            self.assertFalse(
                read(p.work / "performance-plan.json")["overlap_mixer_supported"]
            )

    def test_true_overlap_remains_an_explicit_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            units = read(p.folder(p.scenes[0]) / "units.json")
            units.append({**units[0], "id": 2, "start": 1.0, "end": 2.0})
            seal_transcript(p, units)
            script = read(p.work / "script.json")
            script["lines"].append(
                {
                    **script["lines"][0],
                    "id": "interruption",
                    "source_ids": [2],
                    "start": 1.0,
                    "end": 2.0,
                }
            )
            with self.assertRaisesRegex(
                ValueError, "Simultaneous lanes are not implemented"
            ):
                validate_script(p, script)


class TimelineRegressions(unittest.TestCase):
    def test_disjoint_fractional_cuts_match_every_selected_frame_at_2997_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input/source.mp4"
            source.parent.mkdir()
            count = 360
            colors = np.array(
                [
                    [40 + (i * 17) % 160, 40 + (i * 37) % 160, 40 + (i * 71) % 160]
                    for i in range(count)
                ],
                dtype=np.uint8,
            )
            raw = root / "source.rgb"
            raw.write_bytes(
                np.repeat(colors[:, None, None, :], 48, axis=1)
                .repeat(64, axis=2)
                .tobytes()
            )
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "rgb24",
                    "-video_size",
                    "64x48",
                    "-framerate",
                    "30000/1001",
                    "-i",
                    raw,
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=12.012",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    source,
                ]
            )
            config = read(ROOT / "project.example.json")
            config.update(
                slug="disjoint",
                source=str(source),
                scenes=[
                    {
                        "id": f"clip-{i}",
                        "start": start,
                        "end": start + 1.251,
                        "mode": "passthrough",
                    }
                    for i, start in enumerate([0.53, 4.807, 9.311])
                ],
            )
            path = root / "local/project.json"
            write(path, config)
            p = Project(path, root=root)
            prepare(p)
            p.record(
                "transcribe", [], [], {k: p.model(k) for k in ["scribe", "transcribe"]}
            )
            write(
                p.work / "script.json",
                {
                    "source_notes": "Synthetic disjoint markers",
                    "characters": [],
                    "lines": [],
                    "omissions": [],
                },
            )
            review(p, "Synthetic passthrough edit")
            generate(p, submit=True)
            p.record("separate", [], [], {})
            fit(p)
            render(p)

            def decode(path):
                data = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-vf",
                        "scale=64:48",
                        "-map",
                        "0:v:0",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-",
                    ]
                )
                return (
                    np.frombuffer(data, dtype=np.uint8)
                    .reshape(-1, 48, 64, 3)
                    .mean(axis=(1, 2))
                )

            original = decode(source)
            actual = decode(p.output)
            expected = np.concatenate(
                [
                    original[s["source_start_frame"] : s["source_end_frame"]]
                    for s in p.edits()
                ]
            )
            self.assertEqual(len(actual), len(expected))
            np.testing.assert_allclose(actual, expected, atol=12)
            timeline = read(p.work / "edit-timeline.json")
            self.assertFalse(timeline["continuous_picture"])
            self.assertEqual(
                sf.info(p.work / "mix.id.wav").frames, timeline["sample_count"]
            )
            raw_mix, rate = sf.read(p.work / "mix.id.raw.wav", always_2d=True)
            for scene in p.edits():
                audio, _ = sf.read(p.folder(scene) / "original.wav", always_2d=True)
                np.testing.assert_allclose(
                    raw_mix[scene["output_start_sample"] : scene["output_end_sample"]],
                    audio,
                    atol=1e-7,
                )

    def test_many_fractional_cuts_preserve_observable_picture_and_audio_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input/markers.mp4"
            source.parent.mkdir()
            fps = 24
            step = 1.251
            seconds = 26
            count = fps * seconds
            colors = np.array(
                [
                    [40 + (i * 73) % 160, 40 + (i * 101) % 160, 40 + (i * 47) % 160]
                    for i in range(21)
                ],
                dtype=np.uint8,
            )
            frames = np.stack(
                [
                    np.broadcast_to(colors[min(int((i / fps) / step), 20)], (48, 64, 3))
                    for i in range(count)
                ]
            )
            raw = root / "frames.rgb"
            raw.write_bytes(frames.tobytes())
            t = np.arange(seconds * 48000) / 48000
            ids = np.minimum((t / step).astype(int), 20)
            audio = 0.12 * np.sin(2 * np.pi * (300 + ids * 80) * t)
            wav = root / "markers.wav"
            sf.write(wav, audio, 48000)
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "rgb24",
                    "-video_size",
                    "64x48",
                    "-framerate",
                    fps,
                    "-i",
                    raw,
                    "-i",
                    wav,
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    source,
                ]
            )
            config = read(ROOT / "project.example.json")
            config.update(
                slug="markers",
                source=str(source),
                scenes=[
                    {
                        "id": f"scene-{i:02}",
                        "start": i * step,
                        "end": (i + 1) * step,
                        "mode": "passthrough",
                    }
                    for i in range(20)
                ],
            )
            path = root / "local/project.json"
            write(path, config)
            p = Project(path, root=root)
            prepare(p)
            p.record(
                "transcribe", [], [], {k: p.model(k) for k in ["scribe", "transcribe"]}
            )
            write(
                p.work / "script.json",
                {
                    "source_notes": "Synthetic markers",
                    "characters": [],
                    "lines": [],
                    "omissions": [],
                },
            )
            review(p, "No translated dialogue; all synthetic source is passthrough")
            generate(p, submit=True)
            p.record("separate", [], [], {})
            fit(p)
            render(p)
            verify(p)

            # Decode actual source/export frames at the same observable marker positions.
            def rgb(path):
                raw = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-map",
                        "0:v:0",
                        "-vf",
                        "scale=64:48",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-",
                    ]
                )
                return (
                    np.frombuffer(raw, dtype=np.uint8)
                    .reshape(-1, 48, 64, 3)
                    .mean(axis=(1, 2))
                )

            expected = rgb(source)
            actual = rgb(p.output)
            for segment in [10, 15, 19]:
                index = math.ceil(segment * step * fps - 1e-8)
                np.testing.assert_allclose(
                    actual[index],
                    expected[index],
                    atol=10,
                    err_msg=f"Picture delayed at segment {segment + 1}",
                )
            # Final-scene picture must accompany the final-scene audio.
            index = round(23.919 * fps)
            np.testing.assert_allclose(actual[index], expected[index], atol=10)
            samples = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(p.output),
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
            samples = np.frombuffer(samples, dtype="<f4")[
                int(23.90 * 48000) : int(24.0 * 48000)
            ]
            frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
            dominant = frequencies[np.argmax(abs(np.fft.rfft(samples)))]
            self.assertAlmostEqual(dominant, 300 + 19 * 80, delta=20)


if __name__ == "__main__":
    unittest.main()
