"""Checks on the actual rendered audio; model opinions are not human acceptance."""

import base64
import json
import requests
from .core import env_key, need, run, sha, write
from .providers import Jobs, transcribe
from .render import verify


def check(project, start=0, seconds=40, submit=False, background_scene=None):
    need(
        0 < seconds <= 60 and start >= 0,
        "Choose a bounded audio sample of at most 60 seconds",
    )
    report = verify(project)
    folder = project.work / "qa"
    folder.mkdir(exist_ok=True)
    if background_scene:
        scene = next(
            (
                s
                for s in project.edits()
                if s["id"] == background_scene and s["mode"] == "dub"
            ),
            None,
        )
        need(scene is not None, "Choose a dub scene for background listening")
        project.require("separate")
        source = project.folder(scene) / "stems/background.wav"
        total = scene["end"] - scene["start"]
    else:
        source = project.output
        total = report["duration_seconds"]
    need(start + seconds <= total + 0.02, "Audio QA range exceeds selected media")
    label = (background_scene or "render") + f"-{start:g}-{seconds:g}"
    clip = folder / (label + ".mp3")
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            start,
            "-i",
            source,
            "-t",
            seconds,
            "-map",
            "0:a:0",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-b:a",
            "96k",
            clip,
        ]
    )
    if not submit:
        print(
            "Prepared "
            + str(clip)
            + "; rerun with --submit for independent ASR/listening calls."
        )
        return
    if not background_scene:
        write(
            folder / (label + ".asr.json"),
            transcribe(project, clip, "rendered-" + label, language="id"),
        )
    instruction = (
        "Listen to these actual separated music/effects estimates. Identify any intelligible Japanese words or severe separation artifacts. Distinguish music, breaths, grunts and actual speech. Do not hallucinate dialogue."
        if background_scene
        else "Listen to this actual Bahasa Indonesia dub. Are words intelligible? Flag rushed, clipped or robotic delivery, spoken stage directions, intelligible Japanese underneath, and music/effects imbalance. Quote only a short phrase you actually hear. Give approximate locations and distinguish uncertainty from a clear problem. This is a model opinion, not human/native-speaker acceptance."
    )
    body = {
        "model": project.model("listen"),
        "modalities": ["text"],
        "max_completion_tokens": 1400,
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Evaluate the attached actual audio."},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(clip.read_bytes()).decode(),
                            "format": "mp3",
                        },
                    },
                ],
            },
        ],
    }
    meta = {"sha256": sha(clip), "model": body["model"], "instruction": instruction}
    jobs = Jobs(project.work / "api")
    response = jobs.request(
        "listening",
        meta,
        lambda: requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + env_key("OPENAI_API_KEY")},
            json=body,
            timeout=(30, 240),
        ),
        before_send=lambda: env_key("OPENAI_API_KEY"),
    )
    assessment = {
        "scope": "Model listening opinion; requires agent/user audition",
        "actual_input_sha256": sha(clip),
        "assessment": response["choices"][0]["message"]["content"],
    }
    write(folder / (label + ".assessment.json"), assessment)
    print(json.dumps(assessment, ensure_ascii=False, indent=2))
