---
name: jp-id-dubber
description: Produce directed Bahasa Indonesia dubs from Japanese film or television, preserving music and effects and exporting verified single-track 480p MP4s. Use for this repository's source review, casting, voice retakes, stem repairs, smaller exports and dubbing pipeline work.
---

# Japanese to Indonesian dubbing

Work from the repository containing `tools/dub.py` and `project.example.json`. Read the [production workflow](../../../docs/workflow.md) when processing or repairing an episode. Provider adapters and audio tools are bundled here; no global TTS skill or earlier chat is required.

For adaptation, casting, timing or vocal-effort decisions, read [performance guidance](../../../docs/performance.md). Prefer clean, slightly theatrical Indonesian, concise orders, consistent ranks and warmer teammate conversation. Record default and relationship-specific pronouns/address, plus confirmed recurring pronunciations.

## Start or resume

- Identify the video, local config and requested scope. A short pilot is the default; do not turn an audition into a paid full episode. Use `init` for a new slug. JSON paths resolve from the repo root.
- Run offline `doctor` and `status` before submitting jobs. Reuse transcripts, response IDs, voice caches and stems. Run only the stage that needs work. There is no paid `all` command.
- `transcribe`, `adapt`, `voices` and `qa` can make paid calls. Follow their explicit `--submit` interface within the authorized scope. `voices` without it previews requests; `qa` without it only extracts local audio.
- A pending background response exits with code 3 and preserves its ID. Rerun the same adaptation to GET that response. Do not change prompts or delete receipts to evade an uncertain job. No silent model fallback is implemented.
- When extending a pilot under another config, carry forward the established cast/styles and unchanged wording. After review, use `reuse-voices --from-config local/PILOT.json` to import exact completed requests without API calls. Preview voices before submitting only unmatched work. Changed audio extractions or scene clocks do not automatically qualify for transcript reuse.

## Editorial and voice work

1. Select measured dialogue ranges. Mark songs `passthrough` and split nearby dialogue into `dub` scenes. Full scope covers the source without gaps. Adapt at most 240 seconds per request, usually one scene at a time.
2. Prepare source audio and contact sheets. Scribe v2 supplies Japanese words/times and anonymous labels; independent ASR supplies a second view. Listen to source evidence for names, commands, overlaps and edge cuts. Do not rely on a fixed speaker mapping or translate from burned English captions.
3. Keep raw ASR unchanged. Put evidenced replacements, missing-utterance insertions or merged-unit splits in local `work/<slug>/source-overrides.json`, with stable IDs and bounded evidence. Use confirmed context/glossary and cast. Use descriptive anonymous identities where evidence is weak. Do not reuse another series' names or timings.
4. Adapt directly into conversational Bahasa Indonesia. Preserve agency, negation, relationships, threats and plot information. Combine same-character fragments into complete breath phrases. Leave breathing room. Spoken `text` contains words only; acting notes go in `direction` and character `style`.
5. Review into `script.json`: source coverage/omissions, character registers, stable voices, timing and uncertainties. Initial performance bounds come from cited source units; shifts over 0.25 s need an explicit reason and evidence. Repeated source IDs need declared chronological parts or corrected split units. `review --note` seals timing/event reports and records editorial review. Re-review after script/source changes; assess pronunciation, acting and mix quality by listening before delivery.
6. Preview voice requests, then generate. Cache identity includes model, voice, text, style and take number. An ordinary rerun records only changed specs. For an intentional new performance with identical wording, increment the line's positive integer `take`; retain old takes and receipts. Never increment it to evade an uncertain submission.
7. Separate original speech, music and effects with the verified Bandit v2 checkpoint. The Indonesian bed is **music + effects**. Do not place new voices over the full Japanese mix. Listen to actual stems for residual words/artifacts.
8. Fit actual recordings. Trim edge silence conservatively, keep pitch, and cap speedup at 1.12×. Condense or retake failed lines. A measured held-shot gap can justify an explicit end extension up to 0.4 s; preserve source times and record evidence. Never shift later dialogue to hide an overrun.

For observed residual words or shouts inside music, read [stem postproduction](../../../docs/postproduction.md#repair-residual-voices-in-music) and use optional `refine` before fitting. Configure source-linked windows and listening evidence; use a short instrumental repair only with a verified compatible donor. Raw stems remain immutable. Derived M&E and a separate complete source-level reference are sealed outputs. The reference controls replacement gain only and must never be mixed audibly. Listen to the derived bed and final mix; cleanup can also affect instruments. Do not overwrite raw stems or rewrite stage seals manually.

## Deliver and maintain

Run fit, render and verify. Delivery is a 480p H.264/AAC MP4 with exactly one Bahasa Indonesia stream, original aspect ratio/nominal frame rate, music/effects, fast start and full decoding. Song intervals retain original singing within that same stream. Existing burned captions/watermarks remain unless picture edits were requested. Timing is approximate, not mouth-perfect lip synchronization.

Preparation binds source selection, scope and a shared resolved frame/sample edit timeline. Use its extracted-scene times for ASR, source corrections and performances; do not independently round video cuts or caption offsets. Existing preparation seals need refresh after this timeline upgrade. Inspect caches before any new paid work.

Preparation decodes source audio once into a cached PCM clock and slices exact sample ranges. Keep this path rather than input-seeking AAC clips: decoder seeking can discard a frame even at zero. On older work folders, refresh preparation for `audio_clock_version: 1`, inspect changed evidence and reuse completed requests/takes wherever their actual hashes/specifications still match. Delivered files stay intact.

Full scope covers both selected source streams (`v:0`/`a:0`) and the encoded audio through the master's final sample. A selected audio tail beyond picture is rejected before paid work. Use the bundled `condition-source --source VIDEO --output input/CONDITIONED.mp4` for an explicitly verified lossless picture hold; read [source conditioning](../../../docs/postproduction.md#preserve-an-audio-tail). Initialize a new slug against that copy and plan to its returned duration. CFR validation respects the source `time_base`, including millisecond Matroska rounding, while retaining genuine irregularity/origin checks.

Inventory overlapping speech and meaningful shouts, laughter, gasps and exertion in `performance_events`. Choose a supported serial line or evidenced omission; unresolved events block final review. Simultaneous lanes are a planned extension, and actual overlaps remain blocked. Never serialize overlaps or assume vocal efforts survived separation.

Handled events must match every known performer. Use explicit `character_ids` only for supported sequential groups, with an empty single `character_id`. Declared overlap events cannot be handled by serial lines; intersections between cited source windows also block serialized rewrites independently of the 0.25 s timing threshold.

Inspect actual exported frames and audition the rendered Indonesian track. Independent rendered-audio ASR and model listening opinions support the review; fix observed problems and re-verify. Distinguish mechanics, model opinions and user audition. Deliver the MP4 and optional SRT/review/cast sidecars from `dubbed/`.

For a requested smaller copy, run `compact` after the main render; see [compact exports](../../../docs/postproduction.md#make-a-smaller-480p-copy). It re-encodes source picture with the shared edit clock and copies the existing Indonesian AAC unchanged. It preserves the original export and writes separate verification. Check actual frames for softness and caption legibility. All these postproduction commands are local; model setup downloads are explicit.

Before code changes inspect callers; afterward run `python tools/verify_project.py`. Tone fixtures do not prove translation, separation or acting quality. After workflow changes, reread the updated skill and relevant guides from the current checkout before resuming affected stages. For publication follow [release checks](../../../docs/release.md). All episode content remains local. [Production validation](../../../docs/validation.md) records measured results.
