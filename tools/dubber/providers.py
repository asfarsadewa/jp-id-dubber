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
        return base.with_name(base.name + ".receipt.json"), base.with_name(
            base.name + ".result.json"
        )

    def import_completed(self, source, label, metadata):
        """Copy an exact completed cache; never overwrite uncertain submissions."""
        old_receipt, old_result = source.paths(label, metadata)
        if not old_receipt.exists() and not old_result.exists():
            return False
        need(
            old_receipt.exists() and old_result.exists(),
            "Incomplete source cache; inspect its receipt",
        )
        state = read(old_receipt)
        need(
            state.get("status") == "completed"
            and state.get("metadata") == metadata
            and state.get("result_sha256") == sha(old_result),
            "Source cache is uncertain or corrupt; it cannot authorize another job",
        )
        receipt, result = self.paths(label, metadata)
        origin = {
            "receipt": str(old_receipt.resolve()),
            "result_sha256": state["result_sha256"],
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        if receipt.exists():
            current = read(receipt)
            need(
                current.get("metadata") == metadata,
                "Destination receipt metadata differs",
            )
            if current.get("status") == "completed":
                need(
                    result.exists() and current.get("result_sha256") == sha(result),
                    "Corrupt destination cache",
                )
                return True
            need(
                current.get("status") == "importing"
                and current.get("imported_from") == origin,
                "Destination has an uncertain submission; preserve its receipt and resolve it first",
            )
        else:
            need(
                not result.exists(),
                "Destination has an orphan result; inspect it first",
            )
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "status": "importing",
                        "metadata": metadata,
                        "imported_from": origin,
                    },
                    stream,
                )
        write(result, read(old_result))
        write(receipt, {**state, "result_sha256": sha(result), "imported_from": origin})
        return True

    def request(
        self, label, metadata, send, background=False, resume=None, before_send=None
    ):
        receipt, result = self.paths(label, metadata)
        # Old dotted labels discarded their hash. Adopt only an exact, verified
        # completed match; uncertain legacy attempts must never cause a new POST.
        legacy_base = self.folder / (label + "-" + digest(metadata)[:24])
        legacy_receipt = legacy_base.with_suffix(".receipt.json")
        legacy_result = legacy_base.with_suffix(".result.json")
        if (
            not receipt.exists()
            and legacy_receipt != receipt
            and legacy_receipt.exists()
        ):
            state = read(legacy_receipt)
            need(
                state.get("status") == "completed",
                "Uncertain legacy dotted-label receipt; inspect it before another submission",
            )
            if state.get("metadata") == metadata:
                need(
                    legacy_result.exists()
                    and state.get("result_sha256") == sha(legacy_result),
                    "Corrupt legacy cache; preserve its receipt",
                )
                write(result, read(legacy_result))
                write(
                    receipt,
                    {
                        **state,
                        "result_sha256": sha(result),
                        "migrated_from": legacy_receipt.name,
                    },
                )
        if result.exists():
            need(
                receipt.exists()
                and read(receipt).get("status") == "completed"
                and read(receipt).get("metadata") == metadata
                and read(receipt).get("result_sha256") == sha(result),
                "Cached result has no matching completed receipt",
            )
            return read(result)
        if receipt.exists():
            state = read(receipt)
            need(
                state.get("metadata") == metadata,
                "Receipt metadata differs from the current request; inspect the cache",
            )
            need(
                background and state.get("id") and resume,
                f"Existing {state.get('status')} receipt: {receipt.name}. Inspect provider outcome; do not delete it or resubmit blindly",
            )
            # A failed GET never authorizes a new POST.
            response = resume(state["id"])
        else:
            if before_send:
                before_send()
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

    return jobs.request(
        label,
        meta,
        send,
        before_send=lambda: env_key(
            "ELEVENLABS_API_KEY" if scribe else "OPENAI_API_KEY"
        ),
    )


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

    data = jobs.request(
        "adapt",
        meta,
        send,
        background=True,
        resume=resume,
        before_send=lambda: env_key("OPENAI_API_KEY"),
    )
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
