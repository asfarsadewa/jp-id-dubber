# Source conditioning, stem repairs and smaller exports

These commands run locally. They do not submit ASR, adaptation or voice jobs. Use the Python environment with the dependencies for the selected operation; the config still resolves paths from the repository root.

## Preserve an audio tail

If preparation reports audio after the final picture frame, create a separate conditioned source **before** initializing the production slug:

```sh
python tools/dub.py condition-source --source input/episode.mp4 --output input/episode.conditioned.mp4
```

The command audits the CFR timestamp grid and selected audio bounds, then adds the minimum number of held final frames. It uses lossless H.264 and copies the original audio packets. Verification compares every decoded original frame, the added frames, audio packet hashes, decoded audio hashes and audio presentation bounds. The original is retained, and the destination must be new. No-tail sources and unsupported timestamp origins fail explicitly.

Use the returned conditioned duration when planning full coverage. It can be slightly longer than the original container duration. Lossless working copies can be large; the delivered 480p files use normal compression. Conditioning is a separate, evidenced source treatment, not an automatic way to bypass a preparation failure.

## Repair residual voices in music

First run `separate` and listen to the actual music/effects bed. Use optional refinement only for observed residual words, shouts or vocal efforts. It is not a blanket cleanup filter: subtracting a vocal estimate can also affect instruments.

Install `requirements-cleanup.txt` in the same environment as the platform-appropriate PyTorch/torchaudio build and separator dependencies. Fetch the pinned model explicitly:

```sh
python -m pip install -r requirements-cleanup.txt
python tools/dub.py --config local/episode-01.json setup-cleanup
```

The adapter uses Demucs 4.0.1 and the full-hash-verified `htdemucs` checkpoint. `refine` does not download models implicitly. Patch-only work does not need Demucs or its weights.

Add an optional `postprocess` object to the local config. The following values are **schema examples**, not episode timings:

```json
{
  "postprocess": {
    "device": "cuda",
    "scenes": [
      {
        "id": "dialogue-01",
        "cleanup": [
          {
            "source_ids": [3],
            "reason": "A spoken call remains in the music estimate.",
            "evidence": "Describe bounded listening to the original and separated stems."
          }
        ],
        "repairs": [
          {
            "start": 12.0,
            "end": 13.8,
            "donor_start": 20.0,
            "fade": 0.1,
            "reason": "An isolated residual remains after model cleanup.",
            "evidence": "Describe listening that confirms the residual and a clean instrumental donor."
          }
        ]
      }
    ]
  }
}
```

Choose existing `dub` scenes. Cleanup cites effective source-unit IDs, including evidenced inserted or split units. The mask follows each cited unit with 0.12 s lead, 0.24 s trail and 0.05 s fades. Overlapping mask regions merge. Model estimates are cached by raw music content, model identity and device, so changing a window reuses the estimate.

Music repairs are optional. Their times are relative to the **resolved extracted scene**. Use short, compatible instrumental donors confirmed by listening. Target windows cannot overlap; donors must be outside every target. The command uses an unchanged donor snapshot, matches neighboring score levels with bounded gain and crossfades the repair. It never patches the full Japanese mix or changes the SFX stem.

```sh
python tools/dub.py --config local/episode-01.json refine
python tools/dub.py --config local/episode-01.json fit
```

Raw `stems/speech.wav`, `music.wav`, `sfx.wav` and `background.wav` stay intact. Already postprocessed separation markers are rejected; restore raw separation in a separate working project while retaining existing voices and exports. Derived files are in `stems/refined/`. The background is repaired music plus original effects. `speech-reference.wav` contains original speech plus the removed music components; it is used **only to measure replacement voice levels**, never as an audible original-language layer. This prevents attack calls misclassified as music from producing nearly silent replacement voices.

The refinement seal binds raw stems, source units, corrections, model identity and plan. Plan, source or artifact changes block mixing until refinement is refreshed. Removing a plan also invalidates its old mix. Source component sums are checked; music outside the declared windows and all raw effects are preserved. Songs remain separate passthrough scenes.

After repairs, listen to the derived bed with `qa --background-scene SCENE`, then to the rendered mix. Fix the smallest affected stage. Do not overwrite raw stems or edit stage seals manually.

## Make a smaller 480p copy

After a current, verified main render:

```sh
python tools/dub.py --config local/episode-01.json compact
# Optional explicit destination:
python tools/dub.py --config local/episode-01.json compact --output dubbed/episode-01.small.mp4
```

The default is `dubbed/<slug>.id.480p.small.mp4`. The encoder uses the original source picture and shared edit timeline, H.264 `veryslow`/CRF 29, and an exact AAC copy of the existing Indonesian track. It preserves 480p, aspect ratio, frame rate, every selected frame, full audio coverage and fast start. It never replaces the original delivery or source. An existing destination requires `--overwrite`.

Verification fully decodes the result and compares both encoded audio payloads and decoded PCM against the main export. A candidate that fails verification or does not shrink is retained for inspection and is not promoted. Results and logs are separate from the main delivery's verification. Size savings depend on the picture; compare actual exported frames for softness and caption readability.
