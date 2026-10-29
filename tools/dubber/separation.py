"""Pinned ELUATE/Bandit v2 adapter retaining music AND effects, dropping speech."""

from concurrent.futures import ThreadPoolExecutor
import sys
import time
import requests
from .core import need, read, run, sha, write

ELUATE_COMMIT = "1b40cd9deae8fb0831b2550d6d54a517aba8731d"
CHECKPOINT_SHA = "abcfccf65446752a057f4a302c941479a54b7560ebf8d7bca039d2ea98e64cfc"
CHECKPOINT_SIZE = 446680129
CHECKPOINT_URL = (
    "https://zenodo.org/records/12701995/files/checkpoint-multi.ckpt?download=1"
)


def paths(project):
    settings = project.config.get("separator", {})
    return project.path(settings.get("checkout", "third_party/eluate")), project.path(
        settings.get("checkpoint", "models/bandit-v2-multi.ckpt")
    )


def setup(project, download=True):
    checkout, checkpoint = paths(project)
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "https://github.com/anaxoniclabs/ELUATE.git", checkout])
        run(["git", "-C", checkout, "checkout", "--detach", ELUATE_COMMIT])
    import subprocess

    need(
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == ELUATE_COMMIT,
        "Separator checkout differs from the tested pin",
    )
    telemetry = checkout / "eluate/utils/telemetry.py"
    content = telemetry.read_text(encoding="utf-8")
    if "except ModuleNotFoundError" not in content:
        need(
            "import resource\n" in content
            and "    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss"
            in content,
            "Upstream telemetry changed; inspect before patching",
        )
        content = content.replace(
            "import resource\n",
            "try:\n    import resource\nexcept ModuleNotFoundError:  # Windows\n    resource = None\n",
            1,
        )
        content = content.replace(
            "    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss",
            "    if resource is None:\n        import psutil\n        return psutil.Process().memory_info().peak_wset / (1024**2)\n    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss",
            1,
        )
        telemetry.write_text(content, encoding="utf-8")
    if not download:
        return
    if checkpoint.exists():
        need(
            sha(checkpoint) == CHECKPOINT_SHA, "Separator checkpoint checksum mismatch"
        )
        print("Separator checkpoint verified.")
        return
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    parts = checkpoint.parent / "bandit-download-parts"
    parts.mkdir(exist_ok=True)
    chunk = 16 * 1024 * 1024

    def part(index):
        lo = index * chunk
        hi = min(CHECKPOINT_SIZE, lo + chunk) - 1
        path = parts / f"{index:03d}.part"
        if path.exists() and path.stat().st_size == hi - lo + 1:
            return path
        for attempt in range(3):
            try:
                response = requests.get(
                    CHECKPOINT_URL + f"&range_part={index}",
                    headers={"Range": f"bytes={lo}-{hi}"},
                    timeout=(20, 60),
                )
                need(
                    response.status_code == 206
                    and response.headers.get("Content-Range")
                    == f"bytes {lo}-{hi}/{CHECKPOINT_SIZE}"
                    and len(response.content) == hi - lo + 1,
                    "Incomplete checkpoint range",
                )
                temp = path.with_suffix(".partial")
                temp.write_bytes(response.content)
                temp.replace(path)
                print(f"Checkpoint part {index + 1} saved", flush=True)
                return path
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)

    with ThreadPoolExecutor(max_workers=8) as pool:
        ordered = list(pool.map(part, range((CHECKPOINT_SIZE + chunk - 1) // chunk)))
    partial = checkpoint.with_suffix(".partial")
    with partial.open("wb") as stream:
        for p in ordered:
            with p.open("rb") as source:
                while block := source.read(1024 * 1024):
                    stream.write(block)
    need(
        partial.stat().st_size == CHECKPOINT_SIZE and sha(partial) == CHECKPOINT_SHA,
        "Assembled checkpoint checksum mismatch; do not load it",
    )
    partial.replace(checkpoint)


def separate(project):
    project.prepared()
    checkout, checkpoint = paths(project)
    need(
        checkout.exists() and checkpoint.exists(),
        "Run setup-separator and install separation dependencies first",
    )
    need(sha(checkpoint) == CHECKPOINT_SHA, "Unverified model; refused to load")
    sys.path.insert(0, str(checkout))
    import torch
    import numpy as np
    import soundfile as sf
    from eluate.core.separator import BanditSeparator

    settings = project.config.get("separator", {})
    device = settings.get("device", "cuda")
    need(
        device != "cuda" or torch.cuda.is_available(),
        "CUDA unavailable; install the matching PyTorch build, or deliberately select cpu",
    )
    separator = BanditSeparator(
        config_path=checkout / "eluate/configs/bandit_v2.yaml",
        checkpoint_path=checkpoint,
        device=torch.device(device),
    )
    separator.config.inference.batch_size = settings.get("batch_size", 2)
    inputs = [checkpoint]
    outputs = []
    try:
        for scene in project.edits():
            if scene["mode"] != "dub":
                continue
            folder = project.folder(scene)
            source = folder / "original.wav"
            inputs.append(source)
            stems = folder / "stems"
            stems.mkdir(exist_ok=True)
            marker = stems / "separation.json"
            spec = {
                "source_sha256": sha(source),
                "checkpoint_sha256": CHECKPOINT_SHA,
                "adapter": "music+sfx-v1",
            }
            names = [
                stems / (s + ".wav") for s in ["speech", "music", "sfx", "background"]
            ]
            valid = (
                marker.exists()
                and read(marker).get("spec") == spec
                and all(
                    p.exists() and sha(p) == read(marker)["outputs"].get(p.name)
                    for p in names
                )
            )
            if not valid:
                start = time.time()
                mix = separator._load_audio(source)
                paths_out, writers = separator._open_stem_writers(
                    stems, 2, persisted_stems={"speech", "music", "sfx"}
                )
                try:
                    separator._demix_streaming(mix, writers, lambda p: None)
                finally:
                    for writer in writers.values():
                        writer.close()
                arrays = {}
                for name, path in paths_out.items():
                    data, rate = sf.read(path, dtype="float32", always_2d=True)
                    need(
                        rate == 48000
                        and data.shape == (mix.shape[1], 2)
                        and np.isfinite(data).all(),
                        "Invalid separated stem",
                    )
                    sf.write(stems / (name + ".wav"), data, rate, subtype="FLOAT")
                    arrays[name] = data
                sf.write(
                    stems / "background.wav",
                    arrays["music"] + arrays["sfx"],
                    48000,
                    subtype="FLOAT",
                )
                write(
                    marker,
                    {
                        "spec": spec,
                        "outputs": {p.name: sha(p) for p in names},
                        "elapsed_seconds": time.time() - start,
                        "background": "music+sfx estimates; original speech excluded",
                    },
                )
            outputs += names + [marker]
            print(scene["id"] + ": music/effects bed ready", flush=True)
    finally:
        separator.close()
    project.record("separate", inputs, outputs, {"checkpoint_sha256": CHECKPOINT_SHA})
