"""Optional, source-linked M&E repairs. Raw separation estimates stay immutable."""

from importlib.metadata import version
import math
import numpy as np
import requests
import soundfile as sf
from .core import need, read, sha, write

RATE = 48000
CHECKPOINT_SHA = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
CHECKPOINT_URL = (
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th"
)


def checkpoint(project):
    return project.path("models/htdemucs/955717e8-8726e21a.th")


def setup(project):
    path = checkpoint(project)
    if path.exists():
        need(sha(path) == CHECKPOINT_SHA, "Cleanup checkpoint checksum mismatch")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with requests.get(CHECKPOINT_URL, stream=True, timeout=(30, 60)) as response:
        response.raise_for_status()
        with partial.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                stream.write(chunk)
    need(sha(partial) == CHECKPOINT_SHA, "Cleanup download checksum mismatch")
    partial.replace(path)


def settings(project):
    plan = project.config.get("postprocess", {})
    need(isinstance(plan, dict), "postprocess must be an object")
    if not plan:
        return None
    need(set(plan) <= {"scenes", "device"}, "Unknown postprocess setting")
    scenes = plan.get("scenes")
    need(
        isinstance(scenes, list) and scenes, "postprocess needs a nonempty scenes array"
    )
    need(
        plan.get("device", "cuda") in {"cuda", "cpu"},
        "Cleanup device must be cuda or cpu",
    )
    overrides = project.work / "source-overrides.json"
    return {
        "version": 1,
        "plan": plan,
        "overrides_sha256": sha(overrides) if overrides.exists() else None,
    }


def require(project):
    project.require("separate")
    spec = settings(project)
    if spec:
        project.require("refine", spec)
    return spec


def stems(project, scene):
    active = project.config.get("postprocess", {}).get("scenes", [])
    folder = project.folder(scene) / "stems"
    if any(s["id"] == scene["id"] for s in active):
        return (
            folder / "refined/background.wav",
            folder / "refined/speech-reference.wav",
        )
    return folder / "background.wav", folder / "speech.wav"


def interval(entry, key, total):
    value = entry.get(key)
    need(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= total,
        f"Invalid {key} in postprocess",
    )
    return float(value)


def evidence(entry):
    need(isinstance(entry, dict), "Cleanup windows and music repairs must be objects")
    need(
        all(
            isinstance(entry.get(k), str) and entry[k].strip()
            for k in ("reason", "evidence")
        ),
        "Every cleanup window and music repair needs reason and listening evidence",
    )


def mask_for(entries, units, count):
    mask = np.zeros(count, dtype="float32")
    by_id = {u["id"]: u for u in units}
    for entry in entries:
        evidence(entry)
        need(
            set(entry) <= {"source_ids", "reason", "evidence"},
            "Unknown cleanup-window field",
        )
        ids = entry.get("source_ids")
        need(
            isinstance(ids, list)
            and ids
            and all(type(i) in {int, str} for i in ids)
            and len(set(ids)) == len(ids)
            and all(i in by_id for i in ids),
            "Cleanup must cite distinct effective source IDs",
        )
        for identity in ids:
            unit = by_id[identity]
            a = max(0, round((unit["start"] - 0.12) * RATE))
            b = min(count, round((unit["end"] + 0.24) * RATE))
            need(a < b, "Cleanup source window is empty")
            shape = np.ones(b - a, dtype="float32")
            fade = min(round(0.05 * RATE), (b - a) // 2)
            if fade:
                ramp = np.sin(np.linspace(0, np.pi / 2, fade, dtype="float32")) ** 2
                shape[:fade], shape[-fade:] = ramp, ramp[::-1]
            mask[a:b] = np.maximum(mask[a:b], shape)
    return mask


def patch_music(music, repairs):
    """Copy only evidenced instrumental intervals, from an unchanged donor snapshot."""
    total = len(music) / RATE
    windows = []
    for repair in repairs:
        evidence(repair)
        need(
            set(repair)
            <= {"start", "end", "donor_start", "fade", "reason", "evidence"},
            "Unknown music-repair field",
        )
        start, end = (interval(repair, k, total) for k in ("start", "end"))
        donor = interval(repair, "donor_start", total)
        fade = repair.get("fade", 0.1)
        need(
            start < end
            and donor + end - start <= total
            and isinstance(fade, (int, float))
            and not isinstance(fade, bool)
            and math.isfinite(fade)
            and 0 < fade <= (end - start) / 2,
            "Invalid music-repair bounds/fade",
        )
        a, b, r = (round(x * RATE) for x in (start, end, donor))
        need(a < b and r + b - a <= len(music), "Empty or out-of-bounds music repair")
        windows.append((a, b, r, max(1, round(fade * RATE))))
    for index, (a, b, r, _) in enumerate(windows):
        need(
            all(
                index == j or b <= x or a >= y for j, (x, y, _, _) in enumerate(windows)
            ),
            "Music repairs must not overlap",
        )
        need(
            all(r + b - a <= x or r >= y for x, y, _, _ in windows),
            "A music donor must be outside every repaired interval",
        )
    clean = music.copy()
    changed = np.zeros(len(music), dtype=bool)
    gains = []
    for a, b, r, fade in windows:
        donor = music[r : r + b - a].copy()
        edges = np.concatenate(
            [
                music[max(0, a - RATE // 4) : a],
                music[b : min(len(music), b + RATE // 4)],
            ]
        )
        need(
            len(edges) > 0,
            "A music repair needs neighboring score for level measurement",
        )
        donor_rms = float(np.sqrt(np.mean(donor**2)))
        need(donor_rms > 1e-8, "Instrumental donor is silent")
        gain = float(np.clip(np.sqrt(np.mean(edges**2)) / donor_rms, 0.75, 1.5))
        envelope = np.ones(b - a, dtype="float32")
        ramp = np.sin(np.linspace(0, np.pi / 2, fade, dtype="float32")) ** 2
        envelope[:fade], envelope[-fade:] = ramp, ramp[::-1]
        clean[a:b] = (
            music[a:b] * (1 - envelope[:, None]) + donor * gain * envelope[:, None]
        )
        changed[a:b] = True
        gains.append(gain)
    return clean, changed, gains


def estimates(project, music, device):
    path = checkpoint(project)
    need(
        path.is_file() and sha(path) == CHECKPOINT_SHA,
        "Run setup-cleanup to fetch the verified checkpoint",
    )
    need(
        version("demucs") == "4.0.1",
        "Install requirements-cleanup.txt in the GPU environment",
    )
    import torch
    from demucs.states import load_model
    from demucs.apply import apply_model
    from torchaudio.functional import resample

    need(
        device != "cuda" or torch.cuda.is_available(),
        "Cleanup CUDA unavailable; explicitly choose cpu if needed",
    )
    # Only the full-hash-verified upstream checkpoint is deserialized. Avoid
    # Demucs' implicit model download and PyTorch's changing load defaults.
    model = load_model(torch.load(path, map_location="cpu", weights_only=False)).eval()
    tensor = resample(torch.from_numpy(music.T.copy()), RATE, model.samplerate)
    reference = tensor.mean(0)
    mean, std = reference.mean(), reference.std()
    if float(std) < 1e-7:
        return np.zeros_like(music)
    torch.manual_seed(0)
    with torch.no_grad():
        separated = apply_model(
            model,
            ((tensor - mean) / std)[None],
            device=device,
            shifts=1,
            split=True,
            overlap=0.25,
            progress=False,
            num_workers=0,
        )[0]
        voice = separated[model.sources.index("vocals")] * std + mean
        vocals = (
            resample(voice, model.samplerate, RATE).T.cpu().numpy().astype("float32")
        )
    return np.pad(vocals, ((0, max(0, len(music) - len(vocals))), (0, 0)))[: len(music)]


def refine(project):
    from .editorial import source_units

    project.approved()
    project.require("separate")
    spec = settings(project)
    need(spec is not None, "Configure evidenced postprocess scenes before refine")
    units = source_units(project)
    scenes = {s["id"]: s for s in project.edits() if s["mode"] == "dub"}
    selected = spec["plan"]["scenes"]
    need(
        all(
            isinstance(s, dict) and set(s) <= {"id", "cleanup", "repairs"}
            for s in selected
        ),
        "Unknown postprocess scene field",
    )
    need(
        all(s.get("id") in scenes for s in selected)
        and len({s["id"] for s in selected}) == len(selected),
        "Choose distinct dub scenes for postprocess",
    )
    inputs, outputs, notes = [], [], []
    for entry in selected:
        scene = scenes[entry["id"]]
        cleanup, repairs = entry.get("cleanup", []), entry.get("repairs", [])
        need(
            isinstance(cleanup, list)
            and isinstance(repairs, list)
            and (cleanup or repairs),
            "Each postprocess scene needs cleanup windows or music repairs",
        )
        folder = project.folder(scene)
        raw = folder / "stems"
        arrays = {}
        for name in ("speech", "music", "sfx"):
            path = raw / (name + ".wav")
            arrays[name], rate = sf.read(path, dtype="float32", always_2d=True)
            need(
                rate == RATE
                and arrays[name].shape == (scene["sample_count"], 2)
                and np.isfinite(arrays[name]).all(),
                "Invalid raw stem format/samples",
            )
            inputs.append(path)
        inputs.append(folder / "units.json")
        mask = mask_for(cleanup, units[scene["id"]], scene["sample_count"])
        # Validate all repairs before loading a model or writing derived audio.
        patch_music(arrays["music"], repairs)
        derived = raw / "refined"
        derived.mkdir(exist_ok=True)
        clean = arrays["music"].copy()
        if cleanup:
            model_path = checkpoint(project)
            need(
                model_path.exists() and sha(model_path) == CHECKPOINT_SHA,
                "Run setup-cleanup to fetch the verified checkpoint",
            )
            inputs.append(model_path)
            estimate = raw / "cleanup-estimate.wav"
            marker = raw / "cleanup-estimate.json"
            estimate_spec = {
                "music_sha256": sha(raw / "music.wav"),
                "checkpoint_sha256": CHECKPOINT_SHA,
                "adapter": "htdemucs-4.0.1-v1",
                "device": spec["plan"].get("device", "cuda"),
            }
            valid = (
                marker.exists()
                and read(marker).get("spec") == estimate_spec
                and estimate.exists()
            )
            valid = valid and sha(estimate) == read(marker).get("sha256")
            if not valid:
                vocals = estimates(project, arrays["music"], estimate_spec["device"])
                need(
                    vocals.shape == clean.shape and np.isfinite(vocals).all(),
                    "Invalid cleanup estimate",
                )
                sf.write(estimate, vocals, RATE, subtype="FLOAT")
                write(marker, {"spec": estimate_spec, "sha256": sha(estimate)})
            vocals, rate = sf.read(estimate, dtype="float32", always_2d=True)
            need(
                rate == RATE
                and vocals.shape == clean.shape
                and np.isfinite(vocals).all(),
                "Invalid cached estimate",
            )
            clean -= vocals * mask[:, None]
            inputs += [estimate, marker]
        clean, patched, gains = patch_music(clean, repairs)
        reference = arrays["speech"] + (arrays["music"] - clean)
        background = clean + arrays["sfx"]
        untouched = (mask == 0) & ~patched
        need(
            np.array_equal(clean[untouched], arrays["music"][untouched]),
            "Music changed outside declared windows",
        )
        need(
            np.allclose(
                reference + background,
                arrays["speech"] + arrays["music"] + arrays["sfx"],
                atol=2e-6,
            ),
            "Source component sum was not conserved",
        )
        for name, data in [
            ("music.wav", clean),
            ("speech-reference.wav", reference),
            ("background.wav", background),
        ]:
            need(np.isfinite(data).all(), "Non-finite postprocessed audio")
            path = derived / name
            sf.write(path, data, RATE, subtype="FLOAT")
            outputs.append(path)
        notes.append(
            {
                "scene": scene["id"],
                "outside_declared_windows_exact": True,
                "raw_stems_unchanged": True,
                "sfx_unchanged": True,
                "source_component_sum_conserved": True,
                "repair_gains": gains,
            }
        )
        print(
            scene["id"] + ": refined M&E and separate level reference ready", flush=True
        )
    report = project.work / "postproduction.json"
    write(
        report,
        {
            "settings": spec,
            "scenes": notes,
            "reference_policy": "Source speech plus removed music components; level measurement only, never mixed audibly.",
        },
    )
    outputs.append(report)
    project.record("refine", inputs, outputs, spec)
