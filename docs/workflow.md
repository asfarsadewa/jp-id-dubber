# Production workflow

Run commands from the repo root, using the Python environment that has the dependencies. Examples below use `python`; on Windows this can be `.venv\Scripts\python.exe`. Read `AGENTS.md` and the project-local skill first. No prior chat or personal helper directory is required.

## Select and resume

```sh
python tools/dub.py init --source input/episode.mp4 --slug episode-01-pilot --start 120 --end 180
python tools/dub.py --config local/episode-01-pilot.json doctor
python tools/dub.py --config local/episode-01-pilot.json status
```

Paths inside a config resolve from the repo root. Default folders are `work/<slug>/` and `dubbed/`. Keep an established episode slug and artifacts. A slug is bound to a source hash; changed source media needs a new slug. These local files are ignored.

`scope` is `pilot` or `full`. Each scene has `id`, absolute source `start`/`end`, `mode` (`dub` or `passthrough`) and a description. Optional `contains_song: true` requires passthrough. Full scope must cover both selected streams without gaps, in order. The current selection is the first video (`v:0`) and first audio (`a:0`); a longer unselected track does not extend that selection. Split theme/ending songs from dialogue; leave original singing in the single final audio mix. For long episodes use short scenes so review, ASR and adaptation stay bounded. The adapter limits an adaptation request to 240 seconds.

Prepare resolves requested boundaries once onto the actual source frame grid. `edit-timeline.json` records requested/resolved source ranges, frame counts, and global output frame/sample offsets. Audio extraction, mixing, captions and picture use this same timeline. Source-unit times refer to the resolved extracted scene, whose start may differ from the requested start by less than one frame. Adjacent full-episode coverage encodes the continuous picture once; disjoint pilot excerpts select exact frame ranges and receive one global output clock. The exporter currently requires constant frame timing and a video origin matching the media origin; prepare rejects unsupported sources before paid work. Normalize a separate source copy deliberately if needed.

The frame audit compares integer timestamps against the expected CFR grid quantized into the stream's `time_base`, rather than using a fixed microsecond tolerance. Ordinary millisecond-rounded CFR Matroska timestamps are supported; genuine irregularity, missing frames and unsupported origin offsets still fail. The source audit records selected audio bounds by intersecting packet presentation ranges with decoded frame sample bounds, accounting for codec padding once across FFmpeg versions. Full coverage uses these selected-stream endpoints rather than the container duration alone. The source audio audit is cached by source identity; it decodes audio only when that cache needs refresh.

If a selected range includes audio after the last picture frame, preparation stops before ASR/TTS jobs. Use the bundled `condition-source` command to make a separately verified lossless picture-hold copy, then initialize a new slug against it and plan coverage using its duration. The original is retained. See [source conditioning](postproduction.md#preserve-an-audio-tail). A bounded pilot before the tail still works without conditioning. Audio that ends earlier than the picture is retained and the remaining picture interval stays quiet.

Before paid work, inspect status and uncertain/pending receipts. Completed caches can be reused without keys. There is no paid `all` command. A configured model is never silently replaced.

## Prepare and review Japanese

```sh
python tools/dub.py --config local/episode-01-pilot.json prepare
python tools/dub.py --config local/episode-01-pilot.json transcribe --submit
```

Prepare audits the frame grid and decodes source audio once to a cached 48 kHz stereo PCM clock. Exact sample-index slices supply each scene's WAV and ASR MP3, with leading audio offsets and silent picture tails preserved. It avoids input seeking, which can discard an AAC frame on conditioned sources in FFmpeg 9. The work folder also contains source identity, probe data and contact sheets. Its seal binds the source, scope, scene selection and shared clocks. Transcribe makes cached Scribe and independent ASR requests only for dub scenes. Inspect `scenes/<id>/scribe.json`, `independent.json` and `units.json`; source audio and sampled original frames are authoritative. Labels can merge characters or split one person. Listen to bounded source and inspect corresponding picture for uncertain names, commands, relationships or utterance edges. Dialogue snapshots are keyed by source content, absolute time and extraction settings, so moving a scene cannot reuse its former character frames.

Keep raw outputs intact. Corrections live in `work/<slug>/source-overrides.json`, an array such as:

```json
[
  {"scene":"dialogue-01","source_id":3,"ja":"Corrected Japanese wording","start":4.2,"end":5.1,"reason":"Re-listened to the bounded source and confirmed these words/times."}
]
```

Fields `ja`, `start` and `end` are optional replacements; every edit needs a reason and valid source unit. Record confirmed terms and relationships in local config `glossary` and `series_context`. Put established character records in optional config `cast` when adapting later scenes/episodes. Never translate from burned English subtitles as the primary source.

The legacy form above is `op: "replace"`. If Scribe missed an utterance entirely, insert a confirmed unit rather than editing raw `units.json`:

```json
{"op":"insert", "scene":"dialogue-01", "unit":{"id":"manual-ack","start":4.2,"end":4.9,"ja":"はい。","speaker":"manual"}, "reason":"Primary ASR missed the acknowledgment.", "evidence":"Independent ASR plus bounded source listening at +4.2 to +4.9 s."}
```

For a merged utterance, use `op: "split"`, `source_id`, and a `units` array of two or more children, plus mandatory `reason` and `evidence`. Each child has a new stable lowercase string ID, Japanese text and bounds inside the parent. Effective units are derived from raw data plus corrections; raw ASRs and unit files stay unchanged. Inserted IDs must be unique and in bounds. Raw integer IDs and corrected string IDs are both valid in `source_ids`. For a partial nonlexical omission, correct the effective unit's lexical bounds/text and explain the excluded sound, rather than both dubbing and wholly omitting the same unit.

## Adapt, assemble and seal review

```sh
python tools/dub.py --config local/episode-01-pilot.json adapt --submit
```

Adapt sends both ASRs, corrected units, ASR audio-event hints, per-utterance contact sheets, confirmed context, registers and pronunciation records to the model. It returns a draft, not editorial approval. The draft is saved even if semantic/timing validation stops the command, so repair it locally rather than buying another draft unnecessarily. A pending response prints its saved ID and exits with code **3**. Rerun the **same command/config** to GET it; do not alter the prompt or create another response to evade an uncertain receipt.

For a long episode, adapt one scene at a time:

```sh
python tools/dub.py --config local/episode-01.json adapt --scene dialogue-01 --submit
python tools/dub.py --config local/episode-01.json adapt --scene dialogue-02 --submit
python tools/dub.py --config local/episode-01.json assemble
```

Per-scene drafts are in `scripts/<scene>.draft.json`. An edited `scripts/<scene>.json` takes precedence at assembly. Resolve conflicting voices and identity assignments first; reconcile anonymous speakers across scenes using source evidence. `assemble` builds `script.draft.json` without overwriting `script.json`. Character metadata needs editorial reconciliation even when voice IDs match.

Review into `work/<slug>/script.json`. Each character has stable `id`, `name`, named `voice`, acting `style`, and identity `evidence`. Each line has unique lowercase `id`, `scene`, source-unit IDs, relative scene `start`/`end`, `character_id`, Japanese `ja`, exact spoken Indonesian `text`, separate `direction`, and `uncertainty`. Cover each source unit or put it in `omissions` with a reason. Same-character fragments can merge into one performance with combined source IDs. Preserve meaning while writing concise, natural Indonesian. Do not voice stage directions, translate songs or invent missing dialogue.

Read [performance guidance](performance.md) for character `register` defaults/relationships, `addressee_id`, recurring pronunciation, source-linked timing and event treatment. Prefer clean, slightly theatrical Indonesian: concise commands, consistent ranks, warm teammate conversation. Initial line bounds come from cited effective units. Deviations over 0.25 s require `timing_adjustment` reason/evidence; repeated source IDs need explicit bounded `source_splits` with complete chronological parts. A discovered wrong ASR boundary belongs in source corrections first.

```sh
python tools/dub.py --config local/episode-01-pilot.json review --note "Describe actual source checks, cast decisions, phrase merges and unresolved doubts."
```

This is the agent's checkpoint within an authorized task, not another user permission prompt. Review writes sealed `script-timing.json` and `performance-plan.json`. The seal binds script, source corrections, context, glossary, register defaults, pronunciation records and established cast. Changes invalidate downstream acceptance. Meaningful shouts, laughter, gasps, exertion and overlaps need explicit decisions in `performance_events`; unresolved selected events block final review. Handled events must match all known performer identities. Declared overlaps cannot count as handled by serial lines, and overlapping cited source windows remain blocked even when authored timings have been made sequential. It is not a native-speaker certificate.

## Record and retake

```sh
python tools/dub.py --config local/episode-01-pilot.json voices
python tools/dub.py --config local/episode-01-pilot.json voices --submit
```

The first command previews requests, with exact text separate from acting/pronunciation metadata. The second records up to three distinct specifications concurrently. Identical repeated occurrences share one in-flight generation and cached WAV, while retaining distinct line timing/IDs. Stable voices and concise styles preserve casting. Requests use the bundled adapter, not a global skill. Old takes remain; unchanged specs are reused. To deliberately record another performance with identical text/direction, increment that line's positive integer `take`, re-review and rerun voices. Never increment it to evade an uncertain attempt.

Use conversational Indonesian that preserves meaning, agency and relationships. Major characters should have distinct voices. Explain deliberate minor-role doubling and uncertainty in the review note. Audition actual WAVs; a voice label does not guarantee acting or pronunciation.

When extending a pilot under another config, preserve established voice IDs, exact styles and unchanged wording before review. Import matching completed requests without network calls:

```sh
python tools/dub.py --config local/episode-01.json reuse-voices --from-config local/episode-01-pilot.json
python tools/dub.py --config local/episode-01.json voices
```

Only exact request specifications match, including model, text, voice, style and take. Uncertain or corrupt receipts block import; existing uncertain destination jobs cannot be replaced. `voices --submit` then materializes cached WAVs and records only unmatched requests within the authorized scope. Transcript reuse is separate: changed audio extraction or scene clocks require their own evidence, and raw ASRs must remain intact.

## Source separation

Use Python 3.12. Install platform-appropriate PyTorch first. The pilot's RTX GPU worked with this CUDA build:

```sh
python -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements-separator.txt
python tools/dub.py --config local/episode-01-pilot.json setup-separator --no-download
python -m pip install --no-deps -e third_party/eluate
python tools/dub.py --config local/episode-01-pilot.json setup-separator
python tools/dub.py --config local/episode-01-pilot.json separate
```

For CPU, install a suitable matching PyTorch build and deliberately set `separator.device` to `cpu`. Do not fall back silently when CUDA is missing. Verify a real CUDA tensor operation when setting up a new GPU environment.

Setup checks out pinned ELUATE, applies the narrow Windows POSIX-telemetry compatibility patch and downloads the multilingual Bandit v2 checkpoint in resumable byte ranges. It verifies the complete SHA-256 before loading. It does not install packages or send episode content to a separation API. First setup downloads roughly 447 MB; later runs reuse it. Dependencies and weights live in ignored folders.

Separation retains `speech.wav`, `music.wav` and `sfx.wav`; `background.wav` is **music + sfx**. The original speech estimate measures level and is not mixed into Indonesian scenes. Listen to the background for residual words, distorted music or missing effects. Source/checkpoint hashes guard cached stems. The adapter uses pinned internal ELUATE APIs; upgrade deliberately and replay integration checks.

## Fit and mix

Listen to the actual separated bed before fitting. If residual voices remain in music, configure evidenced cleanup windows or an instrumental repair and run optional `refine`; see [stem postproduction](postproduction.md#repair-residual-voices-in-music). It preserves raw stems and creates a separate level reference from source speech plus removed music components. Fitting uses that reference only for gain measurement; it never mixes original speech into the output. Plan changes invalidate refinement and its mix, while cached voices remain reusable.

```sh
python tools/dub.py --config local/episode-01-pilot.json fit
```

Inspect `timing-analysis.json`. Fit removes conservative edge silence, keeps pitch, and caps `atempo` at **1.12×**. It matches voices to separated speech levels, keeps music/effects, supports line `effect: "radio"`, and builds a single loudness-normalized master. Passthrough intervals copy original audio before global normalization; no original-language alternate is created.

Overlong takes stop delivery. Condense naturally or adjust direction and retake. For measured held-shot gaps only, a line may set `approved_end_extension` up to **0.4 seconds** (default 0.22). Preserve source times and explain the evidence. Do not move later lines to hide overruns. After edits run review, voices (unchanged takes are cached), then fit. True simultaneous performances remain blocked; record and resolve treatment without silently dropping or serializing them. The [overlap extension plan](performance.md#planned-overlap-mixer-extension) defines the lane mixer and acceptance work still needed.

## Render, verify and audition

```sh
python tools/dub.py --config local/episode-01-pilot.json render
python tools/dub.py --config local/episode-01-pilot.json verify
python tools/dub.py --config local/episode-01-pilot.json qa --start 0 --seconds 40
python tools/dub.py --config local/episode-01-pilot.json qa --start 0 --seconds 40 --submit
python tools/dub.py --config local/episode-01-pilot.json qa --background-scene dialogue-01 --start 0 --seconds 30 --submit
```

Render verifies a candidate before replacement; existing outputs require `render --overwrite`. Verify requires current source, editorial, voice and mix seals. It checks 480p/aspect ratio, H.264/yuv420p, frame rate and exact frame count, full-scope coverage of both selected streams, one `ind` AAC stereo stream, duration, fast start, source identity and full decoding. It independently audits the delivered audio packets through the master timeline's final sample, so complete picture duration cannot hide a shortened encoded audio track. It never caps a delayed concatenated picture to conceal drift. Synthetic regressions compare actual picture/audio markers across fractional cuts and cover audio tails, truncated encoded audio and millisecond CFR MKVs. `qa` extracts actual output audio; `--submit` additionally buys independent ASR/listening. Background QA uses actual M&E.

Open actual exported frames with an image viewer and audition the rendered Indonesian track. Check character changes, names, shouted warnings, radio lines, transitions, Japanese bleed, music/effects balance and edge cuts. Model timestamps are approximate. Repair concrete problems and regenerate only affected stages. Distinguish mechanics, model opinions and user acceptance. This does not perform mouth-perfect lip synthesis or remove burned captions/watermarks.

Deliver `dubbed/<slug>.id.480p.mp4`, plus optional SRT, review TSV, script and cast sidecars. Keep `work/<slug>/` as local provenance. Before paid full-episode work, establish requested scope/casting if it has not already been given; an accepted pilot alone does not authorize a full episode.

For a smaller copy, run `compact` after the main render. It uses the shared source-picture timeline and copies the existing Indonesian AAC unchanged. The new file and verification are separate; the original stays available. See [smaller exports](postproduction.md#make-a-smaller-480p-copy).

## Recovery and maintenance

Completed caches require matching request metadata and result hash. Decimal QA starts/durations retain their complete hashed filenames. An exact verified completed legacy decimal cache can migrate locally; uncertain legacy attempts still block submission. A claimed/uncertain request with no result blocks another POST. Known background IDs resume via GET, including after retrieval errors. Keys are checked before claiming a new paid attempt; there are no automatic paid retries.

Preparation receipts require selected-stream clock version 3 and `audio_clock_version: 1`; reviews require performance policy version 2. Older seals are intentionally stale. Run status, refresh preparation, and inspect affected source evidence/review before further work. The source PCM cache adds local working storage. Keep all prior receipts and takes. Changed extraction hashes may require a new source transcription within an authorized processing task; do not blindly rerun the whole episode or regenerate unchanged cached voices. Updating the tools does not alter delivered files.

For a definitely rejected request, fix the cause and archive its receipt locally only after verifying no job was accepted. Keep evidence; never archive an unknown receipt to bypass the guard. Restore/retrieve corrupted results rather than pretending a new request is the old one. Cache/style/model changes create different jobs; inspect status first.

Before changing code, read callers. Run `python tools/verify_project.py`. Fixtures use synthetic media and fake cached responses without keys; they prove mechanics, not translation, performance or separation quality. See [release.md](release.md) before publication.
