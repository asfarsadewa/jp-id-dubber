# Work in this repository

Read [.agents/skills/jp-id-dubber/SKILL.md](.agents/skills/jp-id-dubber/SKILL.md) for Japanese-to-Indonesian dubbing, source review, casting, voice generation, mixing, delivery or pipeline maintenance. This project-local skill works without previous chat history or a global skill installation.

- Start with `python tools/dub.py --config local/EPISODE.json status`. Inspect receipts before any paid job. Unknown submission outcomes never justify a blind retry.
- Make natural Bahasa Indonesia performances from reviewed Japanese. Keep character evidence, confirmed glossary, exact spoken text, and acting directions separate. Anonymous ASR speaker labels do not establish identity.
- Record relationship-specific pronouns/address and recurring pronunciations. Use the shared frame/sample edit timeline and source-linked performance windows. Document timing shifts, source splits and meaningful vocal-effort decisions before recording.
- Default to a short audition pilot. A full episode needs an explicit full-episode request and complete source-range coverage.
- Export one Indonesian audio track in a compact 480p H.264/AAC MP4 in `dubbed/`. Songs pass through with their original singing; never generate translated song vocals. Preserve original music and effects through actual source separation.
- Use reviewed script seals, content-hashed voice takes and bounded fitting. Fix overlong dialogue through adaptation or a retake before loosening timing limits.
- Keep raw separation stems immutable. Use the bundled optional refinement stage for evidenced residual-vocal cleanup and instrumental repairs; its source-level reference is never audible in the dub. Use `condition-source`, `reuse-voices` and `compact` for their documented operations rather than episode-specific helper scripts.
- This repo is independent of the Japanese-to-Javanese subtitler. Do not import its tools, skill, output rules or episode data.
- Keep source media, generated voices, stems, transcripts, scripts, captions, API receipts, local configs, personal paths and keys out of Git. No media publication exception is granted.
- Before changing code, inspect the stage and its callers. Run `python tools/verify_project.py` afterward. Synthetic fixtures establish mechanics; audition establishes performance quality.
- Publish only when requested. Follow [docs/release.md](docs/release.md), including an index audit, tracked diff review, redacted secret scan and remote verification.
- Public documentation should focus on capabilities, commands, measured validation and practical limitations.
