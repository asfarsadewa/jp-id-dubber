# Register, timing and vocal performance

Read this while adapting, casting or reviewing performances. The preferred direction is **clean, slightly theatrical Indonesian**: concise commands, consistent ranks, warmer conversational phrasing among teammates. A character keeps the same personality and voice while choosing language appropriate to the relationship.

## Speech register and pronunciation

Local config `register_defaults` specifies `self`, `address`, `forms_of_address` and `honorifics`. A reviewed character's `register` contains a default profile and explicit relationship overrides. For example:

```json
{
  "default": {"self":"aku", "address":"kau", "forms_of_address":[], "honorifics":[]},
  "relationships": [
    {"addressee_id":"commander", "self":"saya", "address":"Anda", "forms_of_address":["Komandan"], "honorifics":[], "notes":"Respectful report to the commanding officer."},
    {"addressee_id":"younger-sibling", "self":"aku", "address":"kamu", "forms_of_address":["Dik"], "honorifics":[], "notes":"Familiar, protective speech in private."}
  ]
}
```

These are examples, not universal translations of Japanese pronouns. Establish age, rank, relationship and attitude from the source. Decide whether `aku/saya`, `kamu/kau/Anda`, family terms, ranks and retained Japanese honorifics fit that relationship. Record the choice rather than changing it between scenes without a reason. A line's `addressee_id` is empty when uncertain. Known profiles travel through `cast` and the adaptation payload. The agent reviews wording against the profile; code checks the structure and does not mechanically replace pronouns.

Record recurring names, machines and attack pronunciations in local config:

```json
"pronunciations": [
  {"term":"Machine A", "spoken_form":"Ma-sin A", "notes":"Confirmed preferred reading; keep normal spelling in dialogue and captions."}
]
```

Use only confirmed readings. The adapter supplies the records to adaptation and places relevant pronunciation instructions in TTS style metadata. It leaves exact spoken text normally spelled. Unused pronunciation entries do not invalidate an unrelated voice take. Changes still need editorial review; only changed voice specifications need recording.

## Source-linked performance timing

All source-unit and performance times are relative to the **resolved extracted scene**, as recorded in `edit-timeline.json`. An ordinary line starts from the earliest start and latest end of its cited effective source units. A deviation over **0.25 s** at either boundary requires:

```json
"timing_adjustment": {
  "reason":"Measured held-shot gap allows a slightly later delivery.",
  "evidence":"Describe the bounded source interval, observed pause and unchanged next utterance."
}
```

Fix incorrect ASR bounds in `source-overrides.json` first. A delivery adjustment must not conceal wrong source timing. It does not relax the fit stage's 1.12× speed limit, next-utterance collision guard or separate 0.4 s maximum end extension. `review` writes sealed `script-timing.json` with source windows, derived bounds, authored bounds and significant shifts, before voices are generated.

The 0.25 s threshold is not permission to serialize simultaneous source speech. Review checks intersections between cited effective source windows on the audio sample grid, independently of authored line times. Known source overlap remains unsupported even when both new line boundaries fall within the individual timing tolerance. Correct inaccurate source bounds with evidence; do not shift or relabel a genuine overlap to evade the guard.

Repeated source IDs are rejected unless they are declared parts of one utterance. Prefer splitting a merged unit in the correction layer so every new unit has a stable ID. If one source unit intentionally supplies several performances, each line declares its source subrange and chronological part:

```json
"source_splits": [
  {"source_id":3, "part":1, "parts":2, "start":4.2, "end":4.9,
   "reason":"The long utterance has two distinct breath phrases.",
   "evidence":"Describe the source pause and the words assigned to this part."}
]
```

Every part needs evidence, unique numbering, a nonoverlapping subrange inside the source unit and all expected companion parts. The first/last parts must account for the unit's outer bounds; correct source boundaries or omissions first if they do not. Source subranges drive the initial timing windows. This distinguishes deliberate segmentation from accidentally dubbing the same words twice; it does not automatically establish semantic correctness.

## Overlaps and vocal efforts: current policy

Inspect the source for interruptions, simultaneous shouts, laughter, gasps, cries and exertion. Separation's speech stem may contain nonlexical efforts that disappear from the M&E bed. Do not assume they survive simply because they have no transcript words.

Record meaningful selected events in the script's `performance_events` array:

```json
{
  "id":"effort-01", "scene":"dialogue-01", "start":4.2, "end":4.9,
  "character_id":"", "kind":"gasp", "decision":"unresolved", "line_ids":[],
  "reason":"An audible reaction contributes to the action beat.",
  "evidence":"Describe bounded source listening and visible character evidence."
}
```

Supported kinds are `overlap`, `interruption`, `shout`, `laughter`, `gasp`, `exertion` and `other`. Decisions are:

- `handled_as_line`: matching serial performances represent the event, with line IDs, source evidence and direction. A known `character_id` must match every referenced performance. For an explicitly sequential multi-performer event, leave `character_id` empty and set distinct known `character_ids`; referenced lines must represent exactly that complete group. The group field cannot override a known single identity or enable simultaneous lanes. A missing source effort may need an inserted effective unit first; describe the vocal sound rather than inventing Japanese lexical dialogue.
- `omit`: a deliberate, evidenced editorial omission, with no rendered line IDs. Do not use this automatically for all nonlexical sounds.
- `unresolved`: source listening or an unsupported treatment is still needed. This blocks final review and voice generation for the selected scene.

Drafts can retain unresolved events. `review` seals their decisions in `performance-plan.json`. An event explicitly marked `kind: "overlap"` cannot be `handled_as_line` while simultaneous lanes are unsupported. The source-window guard also rejects known simultaneity when an event is relabelled or the authored lines no longer overlap. Do not serialize an interruption, reintroduce the full Japanese mix, or claim simultaneous support. Preserve auditioned efforts/takes and avoid cloning original actors as an assumed requirement.

## Planned overlap-mixer extension

Implement separate timed lanes with per-lane collision rules, explicit overlap groups, stable speaker identity and source references. Keep simultaneous start/end times intact. Give vocal efforts explicit source/recording provenance; use isolated source efforts only when their isolation is verified, otherwise generate directed replacements and audition them. Mixing needs peak/headroom checks, balanced simultaneous voices and protection against intelligible Japanese leakage.

Validation should include deterministic fixtures with interruptions and two/three simultaneous shouts, observable timing for every lane, clipping/headroom checks and retained M&E. Then audition actual action scenes containing breaths, exertion and overlaps. Only after that demonstration should the skill describe multi-lane performance as supported.
