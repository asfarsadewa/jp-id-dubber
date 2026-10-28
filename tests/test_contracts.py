"""No keys or copyrighted media: failure guards and a real synthetic A/V export."""

import base64
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import numpy as np
import requests
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from dubber.core import ROOT, Project, read, run, sha, write
from dubber.editorial import prepare, review
from dubber.providers import Jobs, Pending
from dubber.voices import audio_wav, generate, payload, specs
from dubber.audio import fit, trim_edges
from dubber.render import render, verify


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status
        self.headers = {}

    def json(self):
        return self.data


class JobContracts(unittest.TestCase):
    def test_cached_paid_result_never_resubmits(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            sent = []
            for _ in range(2):
                self.assertEqual(
                    jobs.request(
                        "test", {}, lambda: (sent.append(1), Response({"ok": True}))[1]
                    ),
                    {"ok": True},
                )
            self.assertEqual(sent, [1])

    def test_timeout_blocks_blind_resubmission(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            sent = []

            def send():
                sent.append(1)
                raise requests.Timeout()

            for _ in range(2):
                with self.assertRaises(RuntimeError if not sent else ValueError):
                    jobs.request("test", {}, send)
            self.assertEqual(sent, [1])
            self.assertEqual(read(jobs.paths("test", {})[0])["status"], "unknown")

    def test_background_resumes_known_id_after_failed_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            sent = []

            def send():
                sent.append(1)
                return Response({"id": "resp_fixture", "status": "in_progress"})

            with self.assertRaises(Pending):
                jobs.request("test", {}, send, True, lambda _: None)
            with self.assertRaises(RuntimeError):
                jobs.request("test", {}, send, True, lambda _: Response({}, 503))
            self.assertEqual(read(jobs.paths("test", {})[0])["id"], "resp_fixture")
            result = jobs.request(
                "test",
                {},
                send,
                True,
                lambda identifier: Response({"id": identifier, "status": "completed"}),
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(sent, [1])

    def test_atomic_claim_prevents_concurrent_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            sent = []

            def attempt():
                try:
                    jobs.request(
                        "test", {}, lambda: (sent.append(1), Response({"ok": True}))[1]
                    )
                except (ValueError, FileExistsError):
                    pass

            with ThreadPoolExecutor(2) as pool:
                list(pool.map(lambda _: attempt(), range(2)))
            self.assertEqual(sent, [1])

    def test_cache_corruption_stops_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            jobs.request("test", {}, lambda: Response({"ok": True}))
            write(jobs.paths("test", {})[1], {"changed": True})
            with self.assertRaises(ValueError):
                jobs.request("test", {}, lambda: self.fail("unexpected POST"))


def make_project(root):
    config = read(ROOT / "project.example.json")
    config.update(
        slug="synthetic-pilot",
        source="input/synthetic.mp4",
        scenes=[
            {"id": "dialogue", "start": 0, "end": 3, "mode": "dub"},
            {
                "id": "song",
                "start": 3,
                "end": 6,
                "mode": "passthrough",
                "contains_song": True,
            },
        ],
    )
    write(root / "local/project.json", config)
    p = Project(root / "local/project.json", root=root)
    p.source.parent.mkdir(parents=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x240:r=24:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=160:sample_rate=48000:duration=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-shortest",
            p.source,
        ]
    )
    prepare(p)
    folder = p.folder(p.scenes[0])
    units = [
        {
            "id": 1,
            "start": 0.5,
            "end": 1.6,
            "speaker": "speaker_0",
            "ja": "Synthetic source unit; no real dialogue.",
        }
    ]
    write(folder / "units.json", units)
    write(folder / "independent.json", {"text": "Synthetic fixture"})
    write(folder / "scribe.json", {"words": []})
    p.record(
        "transcribe",
        [folder / "source.mp3"],
        [folder / n for n in ["units.json", "independent.json", "scribe.json"]],
        {k: p.model(k) for k in ["scribe", "transcribe"]},
    )
    script = {
        "source_notes": "Synthetic mechanics fixture",
        "characters": [
            {
                "id": "lead",
                "name": "Synthetic lead",
                "voice": "Orus",
                "style": "Clear",
                "evidence": "Fixture",
            }
        ],
        "lines": [
            {
                "id": "line-01",
                "scene": "dialogue",
                "source_ids": [1],
                "start": 0.5,
                "end": 1.6,
                "character_id": "lead",
                "ja": "Synthetic unit",
                "text": "Siap.",
                "direction": "Calm",
                "uncertainty": "Synthetic fixture",
            }
        ],
        "omissions": [],
    }
    write(p.work / "script.json", script)
    review(p, "Synthetic fixture review, no language quality claim")
    t = np.arange(144000) / 48000
    bed = np.repeat((0.025 * np.sin(2 * np.pi * 880 * t))[:, None], 2, axis=1).astype(
        "float32"
    )
    speech = np.repeat((0.1 * np.sin(2 * np.pi * 160 * t))[:, None], 2, axis=1).astype(
        "float32"
    )
    stems = folder / "stems"
    stems.mkdir()
    sf.write(stems / "background.wav", bed, 48000, subtype="FLOAT")
    sf.write(stems / "speech.wav", speech, 48000, subtype="FLOAT")
    p.record(
        "separate",
        [folder / "original.wav"],
        [stems / "background.wav", stems / "speech.wav"],
        {"fixture": "hand-made tones, not model separation"},
    )
    # Populate the exact paid cache. Any accidental network request fails the test.
    line, spec = specs(p)[0]
    audio = io.BytesIO()
    with wave.open(audio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        t = np.arange(12000) / 24000
        w.writeframes((np.sin(2 * np.pi * 330 * t) * 6000).astype("<i2").tobytes())
    data = {
        "status": "completed",
        "outputs": [
            {
                "type": "audio",
                "mime_type": "audio/wav",
                "data": base64.b64encode(audio.getvalue()).decode(),
            }
        ],
    }
    Jobs(p.work / "api").request("voice", spec, lambda: Response(data))
    with (
        patch("requests.post", side_effect=AssertionError("No paid jobs in fixtures")),
        patch.dict(os.environ, {}, clear=True),
    ):
        generate(p, submit=True)
    return p


class MediaContracts(unittest.TestCase):
    def test_real_480p_single_track_export_and_stale_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp))
            fit(p)
            render(p)
            report = verify(p)
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["output_probe"]["streams"]), 2)
            self.assertTrue(p.output.with_suffix(".srt").exists())
            # Passthrough is copied into the single master before global loudness normalization.
            raw, sr = sf.read(p.work / "mix.id.raw.wav", always_2d=True)
            original, _ = sf.read(
                p.folder(p.scenes[1]) / "original.wav", always_2d=True
            )
            np.testing.assert_allclose(raw[3 * sr :], original, atol=1e-7)
            # No original speech-tone is mixed into the dialogue bed outside our new performance.
            segment = raw[2 * sr : int(2.5 * sr), 0]
            spectrum = abs(np.fft.rfft(segment))
            freq = np.fft.rfftfreq(len(segment), 1 / sr)
            self.assertGreater(
                spectrum[np.argmin(abs(freq - 880))],
                100 * spectrum[np.argmin(abs(freq - 160))] + 1,
            )
            original_hash = sha(p.output)
            with self.assertRaises(ValueError):
                render(p)
            self.assertEqual(sha(p.output), original_hash)
            # Adding previously absent source corrections invalidates editorial acceptance.
            write(p.work / "source-overrides.json", [])
            with self.assertRaises(ValueError):
                verify(p)
            (p.work / "source-overrides.json").unlink()
            script = read(p.work / "script.json")
            script["lines"][0]["text"] = "Tahan!"
            write(p.work / "script.json", script)
            with self.assertRaises(ValueError):
                verify(p)

    def test_song_cannot_enter_dub_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = read(ROOT / "project.example.json")
            config["scenes"][0]["contains_song"] = True
            path = Path(tmp) / "config.json"
            write(path, config)
            with self.assertRaises(ValueError):
                Project(path, root=tmp)

    def test_trim_preserves_voiced_region_and_rejects_silence(self):
        tone = 0.2 * np.sin(2 * np.pi * 220 * np.arange(12000) / 24000)
        trimmed, left, right = trim_edges(
            np.concatenate([np.zeros(6000), tone, np.zeros(8000)]), 24000
        )
        self.assertGreaterEqual(len(trimmed) / 24000, 0.5)
        self.assertLess(len(trimmed) / 24000, 0.65)
        self.assertLess(left, 0.25)
        self.assertGreater(right, 0.2)
        with self.assertRaises(ValueError):
            trim_edges(np.zeros(24000), 24000)

    def test_acting_notes_stay_out_of_spoken_text(self):
        data = payload(
            {
                "model": "gemini-3.8-flash-tts",
                "text": "Tahan!",
                "style": "Shout urgently",
                "voice": "Orus",
            }
        )
        content = data["input"][0]["content"][0]
        self.assertEqual(content["text"], "Tahan!")
        self.assertEqual(content["annotations"][0]["style"], "Shout urgently")

    def test_truncated_wav_is_rejected(self):
        with self.assertRaises((ValueError, EOFError, wave.Error)):
            audio_wav(
                {
                    "outputs": [
                        {"type": "audio", "data": base64.b64encode(b"RIFFbad").decode()}
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
