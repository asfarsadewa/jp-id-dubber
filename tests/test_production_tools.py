"""Production refinements tested with generated pictures/tones, no paid jobs."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

from test_contracts import make_project, Response
from test_stream_boundaries import source_project, finish_passthrough
from dubber.core import Project, read, run, sha, write
from dubber.audio import fit, require_mix
from dubber.editorial import prepare
from dubber.media import compact, condition, require_compact
from dubber import postproduction as post
from dubber.providers import Jobs


class CompletedCacheReuse(unittest.TestCase):
    def test_exact_completed_result_reuses_without_network_and_keeps_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Jobs(Path(tmp) / "old"), Jobs(Path(tmp) / "new")
            spec = {"voice": "Orus", "text": "Siap.", "take": 1}
            old.request("voice", spec, lambda: Response({"fixture": True}))
            self.assertTrue(new.import_completed(old, "voice", spec))
            self.assertEqual(
                new.request("voice", spec, lambda: self.fail("Unexpected POST")),
                {"fixture": True},
            )
            self.assertTrue(new.import_completed(old, "voice", spec))
            self.assertFalse(new.import_completed(old, "voice", {**spec, "take": 2}))

    def test_uncertain_destination_cannot_be_overwritten_by_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Jobs(Path(tmp) / "old"), Jobs(Path(tmp) / "new")
            spec = {"text": "Fixture"}
            old.request("voice", spec, lambda: Response({"fixture": True}))
            receipt, _ = new.paths("voice", spec)
            write(receipt, {"status": "unknown", "metadata": spec})
            before = receipt.read_bytes()
            with self.assertRaisesRegex(ValueError, "uncertain submission"):
                new.import_completed(old, "voice", spec)
            self.assertEqual(receipt.read_bytes(), before)

    def test_corrupt_or_uncertain_source_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Jobs(Path(tmp) / "old"), Jobs(Path(tmp) / "new")
            spec = {"text": "Fixture"}
            old.request("voice", spec, lambda: Response({"fixture": True}))
            receipt, result = old.paths("voice", spec)
            write(result, {"changed": True})
            with self.assertRaises(ValueError):
                new.import_completed(old, "voice", spec)
            self.assertFalse(new.paths("voice", spec)[0].exists())
            result.unlink()
            write(receipt, {"status": "unknown", "metadata": spec})
            with self.assertRaises(ValueError):
                new.import_completed(old, "voice", spec)


def raw_stems(project, weak_speech=False):
    folder = project.folder(project.scenes[0]) / "stems"
    count = 3 * 48000
    t = np.arange(count) / 48000
    music = np.repeat(
        (0.025 * np.sin(2 * np.pi * 880 * t) + 0.04 * np.sin(2 * np.pi * 440 * t))[
            :, None
        ],
        2,
        axis=1,
    ).astype("float32")
    effects = np.repeat(
        (0.003 * np.sin(2 * np.pi * 1500 * t))[:, None], 2, axis=1
    ).astype("float32")
    for name, data in [
        ("music", music),
        ("sfx", effects),
        ("background", music + effects),
    ]:
        sf.write(folder / (name + ".wav"), data, 48000, subtype="FLOAT")
    if weak_speech:
        sf.write(folder / "speech.wav", np.zeros_like(music), 48000, subtype="FLOAT")
    project.record(
        "separate",
        [project.folder(project.scenes[0]) / "original.wav"],
        [folder / (n + ".wav") for n in ("speech", "music", "sfx", "background")],
        {},
    )
    return music, effects


class StemRefinement(unittest.TestCase):
    def test_cleanup_cache_locality_level_reference_and_mix_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            music, effects = raw_stems(p, weak_speech=True)
            folder = p.folder(p.scenes[0]) / "stems"
            raw_hashes = {
                name: sha(folder / (name + ".wav"))
                for name in ("speech", "music", "sfx", "background")
            }
            p.config["postprocess"] = {
                "device": "cpu",
                "scenes": [
                    {
                        "id": "dialogue",
                        "cleanup": [
                            {
                                "source_ids": [1],
                                "reason": "Synthetic leaked tone",
                                "evidence": "Known 440 Hz fixture",
                            }
                        ],
                    }
                ],
            }
            weights = post.checkpoint(p)
            weights.parent.mkdir(parents=True)
            weights.write_bytes(b"Synthetic placeholder, never deserialized")
            t = np.arange(len(music)) / 48000
            vocals = np.repeat(
                (0.04 * np.sin(2 * np.pi * 440 * t))[:, None], 2, axis=1
            ).astype("float32")
            with (
                patch.object(post, "CHECKPOINT_SHA", sha(weights)),
                patch.object(post, "estimates", return_value=vocals) as estimate,
            ):
                post.refine(p)
                post.refine(p)
                self.assertEqual(estimate.call_count, 1)
                bed_path, reference_path = post.stems(p, p.scenes[0])
                bed, _ = sf.read(bed_path, dtype="float32")
                reference, _ = sf.read(reference_path, dtype="float32")
                speech, _ = sf.read(folder / "speech.wav", dtype="float32")
                mask = post.mask_for(
                    p.config["postprocess"]["scenes"][0]["cleanup"],
                    read(p.folder(p.scenes[0]) / "units.json"),
                    len(music),
                )
                np.testing.assert_array_equal(
                    bed[mask == 0], (music + effects)[mask == 0]
                )
                np.testing.assert_allclose(
                    bed, music - vocals * mask[:, None] + effects, atol=1e-7
                )
                np.testing.assert_allclose(
                    reference, speech + vocals * mask[:, None], atol=1e-7
                )
                np.testing.assert_allclose(
                    reference + bed, speech + music + effects, atol=2e-6
                )
                self.assertEqual(
                    raw_hashes,
                    {name: sha(folder / (name + ".wav")) for name in raw_hashes},
                )
                fit(p)
                require_mix(p)
                self.assertGreater(
                    read(p.work / "timing-analysis.json")["takes"][0]["gain"], 0.05
                )
                mixed, _ = sf.read(p.work / "mix.id.raw.wav", dtype="float32")
                np.testing.assert_array_equal(mixed[57600:72000], bed[57600:72000])
                p.config["postprocess"]["scenes"][0]["cleanup"][0]["evidence"] = (
                    "Changed source review"
                )
                with self.assertRaises(ValueError):
                    require_mix(p)
                p.config.pop("postprocess")
                with self.assertRaises(ValueError):
                    require_mix(p)

    def test_music_patch_preserves_sfx_and_every_sample_outside_its_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            music, effects = raw_stems(p)
            repair = {
                "start": 0.8,
                "end": 1.2,
                "donor_start": 2.0,
                "fade": 0.1,
                "reason": "Synthetic residual",
                "evidence": "Known instrumental donor in fixture",
            }
            p.config["postprocess"] = {
                "scenes": [{"id": "dialogue", "repairs": [repair]}]
            }
            with patch.object(
                post, "estimates", side_effect=AssertionError("Patch needs no model")
            ):
                post.refine(p)
            clean, changed, _ = post.patch_music(music, [repair])
            np.testing.assert_array_equal(clean[~changed], music[~changed])
            bed_path, reference_path = post.stems(p, p.scenes[0])
            bed, _ = sf.read(bed_path, dtype="float32")
            np.testing.assert_allclose(bed, clean + effects, atol=1e-7)
            fit(p)
            raw, rate = sf.read(p.work / "mix.id.raw.wav", dtype="float32")
            song, _ = sf.read(p.folder(p.scenes[1]) / "original.wav", dtype="float32")
            np.testing.assert_array_equal(raw[3 * rate :], song)

    def test_invalid_repairs_and_missing_source_ids_are_rejected(self):
        music = np.ones((3 * 48000, 2), dtype="float32") * 0.02
        base = {
            "start": 0.5,
            "end": 1.5,
            "donor_start": 2.5,
            "reason": "Fixture",
            "evidence": "Fixture",
        }
        for extra in [
            {},
            {"donor_start": 0.75},
            {"start": float("nan")},
            {"fade": 1},
            {"evidence": ""},
        ]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                post.patch_music(music, [{**base, **extra}])
        with self.assertRaises(ValueError):
            post.mask_for(
                [{"source_ids": [99], "reason": "Fixture", "evidence": "Fixture"}],
                [],
                len(music),
            )


class SourceAndCompactExports(unittest.TestCase):
    def test_conditioning_holds_only_final_picture_and_preserves_audible_audio_tail(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp), video=5.5, audio=6)
            original_sha = sha(p.source)
            output = p.source.with_name("conditioned.mp4")
            report = condition(p.source, output)
            self.assertTrue(report["passed"])
            self.assertEqual(report["held_frames"], 12)
            self.assertEqual(sha(p.source), original_sha)
            config = read(p.config_path)
            config["source"] = str(output)
            config["scenes"][0]["end"] = report["duration_seconds"]
            write(p.config_path, config)
            p = Project(p.config_path, root=p.root)
            prepare(p)
            self.assertTrue(finish_passthrough(p)["passed"])
            with self.assertRaises(ValueError):
                condition(p.source, output)

    def test_compact_preserves_audio_canonical_records_and_all_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp))
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=s=320x240:r=24:d=6",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=6",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    p.source,
                ]
            )
            prepare(p)
            finish_passthrough(p)
            original_sha = sha(p.output)
            canonical = (p.work / "verification.json").read_bytes()
            report = compact(p)
            self.assertTrue(report["passed"])
            self.assertGreater(report["reduction_percent"], 0)
            self.assertTrue(report["checks"]["audio_packets_unchanged"])
            self.assertTrue(report["checks"]["decoded_audio_unchanged"])
            self.assertEqual(sha(p.output), original_sha)
            self.assertEqual((p.work / "verification.json").read_bytes(), canonical)
            require_compact(p)
            with self.assertRaises(ValueError):
                compact(p)
            with self.assertRaises(ValueError):
                compact(p, output=p.output, overwrite=True)
            small = Path(report["output"])
            small_sha = sha(small)

            def wrong_audio(args, log):
                # A complete, mechanically valid export can still contain the
                # wrong track. Substitute the unnormalized source audio.
                args[args.index(p.output)] = p.source
                run(args, log)

            with (
                patch("dubber.media.run", side_effect=wrong_audio),
                self.assertRaises(ValueError),
            ):
                compact(p, overwrite=True)
            self.assertEqual(sha(small), small_sha)
            self.assertEqual((p.work / "verification.json").read_bytes(), canonical)
            require_compact(p)
            small.write_bytes(small.read_bytes() + b"changed")
            with self.assertRaises(ValueError):
                require_compact(p)

    def test_fractional_rate_conditioning_uses_whole_frames_without_truncating_audio(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp), video=5.5, audio=6, fps="24000/1001")
            report = condition(p.source, p.source.with_name("conditioned.mp4"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["fps"], "24000/1001")
            self.assertGreaterEqual(report["duration_seconds"], 6)

    def test_sources_without_an_audio_tail_are_not_reencoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = source_project(Path(tmp))
            output = p.source.with_name("unnecessary.mp4")
            before = sha(p.source)
            with self.assertRaisesRegex(ValueError, "no tail"):
                condition(p.source, output)
            self.assertEqual(sha(p.source), before)
            self.assertFalse(output.exists())
