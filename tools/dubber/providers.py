"""Atomic paid-job claims, reusable results and resumable Responses IDs."""

from __future__ import annotations
import base64
import json
from pathlib import Path
import time
import requests
from .core import digest, env_key, need, read, sha, write


class Pending(RuntimeError):
    pass


class Jobs:
    def __init__(self, folder):
        self.folder = Path(folder)

    def paths(self, label, metadata):
        base = self.folder / (label + "-" + digest(metadata)[:24])
        return base.with_suffix(".receipt.json"), base.with_suffix(".result.json")

    def request(self, label, metadata, send, background=False, resume=None):
        receipt, result = self.paths(label, metadata)
        if result.exists():
            need(
                receipt.exists() and read(receipt).get("result_sha256") == sha(result),
                "Cached result has no matching completed receipt",
            )
            return read(result)
        if receipt.exists():
            state = read(receipt)
            need(
                background and state.get("id") and resume,
                f"Existing {state.get('status')} receipt: {receipt.name}. Inspect provider outcome; do not delete it or resubmit blindly",
            )
            # A failed GET never authorizes a new POST.
            response = resume(state["id"])
        else:
            self.folder.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents concurrent duplicate submissions.
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "status": "submitting",
                        "metadata": metadata,
                        "started_at": time.time(),
                    },
                    stream,
                )
            try:
                response = send()
            except Exception as error:
                write(
                    receipt,
                    {
                        "status": "unknown",
                        "metadata": metadata,
                        "error_type": type(error).__name__,
                    },
                )
                raise RuntimeError(
                    f"Uncertain submission: {receipt.name}; inspect provider history before another POST"
                ) from None
        state = read(receipt)
        if not 200 <= response.status_code < 300:
            # Keep any known response ID even when retrieval fails.
            state.update(
                {
                    "status": "rejected"
                    if response.status_code in {400, 401, 403, 404, 413, 422, 429}
                    and not state.get("id")
                    else "unknown",
                    "http_status": response.status_code,
                }
            )
            write(receipt, state)
            raise RuntimeError(
                f"Provider HTTP {response.status_code}; inspect {receipt.name}"
            )
        try:
            data = response.json()
        except ValueError:
            state["status"] = "unknown"
            write(receipt, state)
            raise RuntimeError(
                f"Unparseable provider response; preserve {receipt.name}"
            ) from None
        state.update(
            {
                "status": data.get("status", "completed"),
                "metadata": metadata,
                "request_id": response.headers.get("x-request-id")
                or response.headers.get("request-id"),
            }
        )
        if background:
            need(
                bool(data.get("id")),
                "Background response returned no ID; preserve receipt",
            )
            state["id"] = data["id"]
            write(receipt, state)
            if state["status"] in {"queued", "in_progress"}:
                raise Pending(
                    f"Saved {state['id']}; rerun adapt to retrieve it. No new submission."
                )
            need(
                state["status"] == "completed",
                f"Response {state['id']} ended {state['status']}; inspect receipt",
            )
        write(result, data)
        state.update(
            {
                "status": "completed",
                "result_sha256": sha(result),
                "finished_at": time.time(),
            }
        )
        write(receipt, state)
        return data


def transcribe(project, path, label, language="ja", scribe=False):
    model = project.model("scribe" if scribe else "transcribe")
    jobs = Jobs(project.work / "api")
    meta = {
        "sha256": sha(path),
        "model": model,
        "language": language,
        "diarize": scribe,
    }

    # Resolve credentials only for a real network operation, before claiming it.
    def send():
        with Path(path).open("rb") as stream:
            if scribe:
                return requests.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": env_key("ELEVENLABS_API_KEY")},
                    data={
                        "model_id": model,
                        "language_code": language,
                        "diarize": "true",
                        "timestamps_granularity": "word",
                        "tag_audio_events": "true",
                    },
                    files={"file": (Path(path).name, stream, "audio/mpeg")},
                    timeout=(30, 600),
                )
            return requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + env_key("OPENAI_API_KEY")},
                data={"model": model, "language": language, "response_format": "json"},
                files={"file": (Path(path).name, stream, "audio/mpeg")},
                timeout=(30, 600),
            )

    receipt, result = jobs.paths(label, meta)
    if not result.exists() and not receipt.exists():
        env_key("ELEVENLABS_API_KEY" if scribe else "OPENAI_API_KEY")
    return jobs.request(label, meta, send)


def adapt(project, payload, schema, images, instructions):
    content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
    content += [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,"
            + base64.b64encode(Path(p).read_bytes()).decode(),
            "detail": "high",
        }
        for p in images
    ]
    body = {
        "model": project.model("adapt"),
        "instructions": instructions,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "high"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "dub_script",
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": 18000,
        "background": True,
        "store": True,
    }
    jobs = Jobs(project.work / "api")
    # Full body hash includes prompt, images, schema and selected model.
    meta = {"body_sha256": digest(body), "model": body["model"]}

    def send():
        return requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer " + env_key("OPENAI_API_KEY")},
            json=body,
            timeout=(30, 180),
        )

    def resume(identifier):
        return requests.get(
            "https://api.openai.com/v1/responses/" + identifier,
            headers={"Authorization": "Bearer " + env_key("OPENAI_API_KEY")},
            timeout=(30, 60),
        )

    receipt, result = jobs.paths("adapt", meta)
    if not result.exists():
        env_key("OPENAI_API_KEY")
    data = jobs.request("adapt", meta, send, background=True, resume=resume)
    text = "".join(
        c["text"]
        for item in data.get("output", [])
        if item.get("type") == "message"
        for c in item.get("content", [])
        if c.get("type") == "output_text"
    )
    need(
        bool(text),
        "Completed response has no script text; inspect result rather than paying again",
    )
    return json.loads(text)
