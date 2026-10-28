"""Self-contained directed Gemini TTS, exact words and separate acting metadata."""

import base64
from concurrent.futures import ThreadPoolExecutor
import io
import re
import wave
import requests
from .core import digest, env_key, need, read, sha, write
from .providers import Jobs

REVISION = "2026-05-20"
DEFAULT_STYLE = "Indonesian television animation dub. Natural native Indonesian pronunciation, directed dramatic acting. No background music. Do not speak any instructions."


def voice_spec(project, line, character):
    need(
        project.model("tts") == "gemini-3.8-flash-tts",
        "This bundled contract supports gemini-3.8-flash-tts; deliberately implement/test other models before changing it",
    )
    need(
        isinstance(line.get("take", 1), int)
        and not isinstance(line.get("take", 1), bool)
        and line.get("take", 1) >= 1,
        "Take number must be a positive integer",
    )
    return {
        "model": project.model("tts"),
        "voice": character["voice"],
        "language": "id",
        "text": line["text"],
        "style": " ".join(
            [
                "Language: Bahasa Indonesia.",
                project.config.get("tts_style", DEFAULT_STYLE),
                character["style"],
                line["direction"],
            ]
        ),
        "api_revision": REVISION,
        "take": line.get("take", 1),
    }


def payload(spec):
    return {
        "model": spec["model"],
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": spec["text"],
                        "annotations": [
                            {"type": "speech_metadata", "style": spec["style"]}
                        ],
                    }
                ],
            }
        ],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/wav",
            "sample_rate": 24000,
        },
        "generation_config": {"speech_config": [{"voice": spec["voice"]}]},
    }


def audio_wav(response):
    need(
        response.get("status") in {None, "completed"},
        "Incomplete Gemini interaction; preserve receipt",
    )
    blocks = [
        b
        for step in response.get("steps", [])
        if step.get("type") == "model_output"
        for b in step.get("content", [])
    ] or response.get("outputs", [])
    if not blocks and response.get("output_audio"):
        blocks = [dict(response["output_audio"], type="audio")]
    chunks = []
    rates = set()
    for block in blocks:
        if block.get("type") != "audio" or not block.get("data"):
            continue
        raw = base64.b64decode(block["data"], validate=True)
        mime = block.get("mime_type", "audio/wav")
        if mime.split(";")[0] in {"audio/wav", "audio/x-wav"}:
            with wave.open(io.BytesIO(raw), "rb") as stream:
                need(
                    stream.getnchannels() == 1
                    and stream.getsampwidth() == 2
                    and stream.getcomptype() == "NONE",
                    "Expected mono 16-bit PCM",
                )
                rate = stream.getframerate()
                count = stream.getnframes()
                pcm = stream.readframes(count)
                need(count > 0 and len(pcm) == count * 2, "Truncated returned WAV")
        elif mime.split(";")[0].lower() in {"audio/l16", "audio/pcm"}:
            match = re.search(r"rate=(\d+)", mime)
            rate = int(match.group(1)) if match else 24000
            pcm = raw
            need(pcm and len(pcm) % 2 == 0, "Truncated PCM")
        else:
            raise ValueError("Unsupported Gemini audio encoding")
        chunks.append(pcm)
        rates.add(rate)
    need(
        bool(chunks) and len(rates) == 1,
        "No audio or inconsistent sample rates; inspect saved result",
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rates.pop())
        stream.writeframes(b"".join(chunks))
    return out.getvalue()


def specs(project):
    project.approved()
    script = read(project.work / "script.json")
    characters = {c["id"]: c for c in script["characters"]}
    return [
        (line, voice_spec(project, line, characters[line["character_id"]]))
        for line in script["lines"]
    ]


def generate(project, submit=False):
    entries = specs(project)
    if not submit:
        print(
            __import__("json").dumps(
                [{"id": l["id"], "spec": s, "request": payload(s)} for l, s in entries],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    folder = project.work / "voices"
    folder.mkdir(parents=True, exist_ok=True)
    jobs = Jobs(project.work / "api")

    def one(item):
        line, spec = item
        body = payload(spec)
        receipt, result = jobs.paths("voice", spec)
        if not result.exists() and not receipt.exists():
            env_key("GEMINI_API_KEY")
        data = jobs.request(
            "voice",
            spec,
            lambda: requests.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={
                    "x-goog-api-key": env_key("GEMINI_API_KEY"),
                    "Api-Revision": REVISION,
                },
                json=body,
                timeout=(30, 180),
            ),
        )
        need(
            data.get("model", spec["model"]) == spec["model"],
            "Returned TTS model differs from the requested model; inspect saved result",
        )
        path = folder / (line["id"] + "-" + digest(spec)[:16] + ".wav")
        raw = audio_wav(data)
        if path.exists():
            need(
                path.read_bytes() == raw,
                "Cached take differs from saved provider audio",
            )
        else:
            path.write_bytes(raw)
        with wave.open(str(path), "rb") as stream:
            seconds = stream.getnframes() / stream.getframerate()
        print(line["id"] + f": {seconds:.2f}s recorded", flush=True)
        return {
            "id": line["id"],
            "path": str(path),
            "sha256": sha(path),
            "spec_sha256": digest(spec),
            "seconds_raw": seconds,
            "voice": spec["voice"],
            "model": spec["model"],
        }

    with ThreadPoolExecutor(max_workers=project.config.get("voice_workers", 3)) as pool:
        takes = list(pool.map(one, entries))
    write(project.work / "voice-takes.json", takes)
    project.record(
        "voices",
        [project.work / "script.json"],
        [project.work / "voice-takes.json"] + [t["path"] for t in takes],
        {"specs": [digest(s) for l, s in entries]},
    )


def require_takes(project):
    entries = specs(project)
    project.require("voices", {"specs": [digest(s) for l, s in entries]})
    takes = {t["id"]: t for t in read(project.work / "voice-takes.json")}
    need(
        set(takes) == {l["id"] for l, s in entries},
        "Take manifest differs from reviewed script",
    )
    for line, spec in entries:
        need(
            takes[line["id"]]["spec_sha256"] == digest(spec),
            "A take is stale; run voices to reuse unchanged takes and record only changed ones",
        )
    return takes
