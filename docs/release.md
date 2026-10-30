# Public release

Normal dubbing does not authorize publication. For an authorized public push, publish sanitized tooling only. There are no approved media exceptions.

The include set is root docs/license/config examples/dependencies, `tools/`, `tests/`, `prompts/`, `docs/`, `.agents/` and `.github/`. Files must be bounded UTF-8 text. The Git index and committed tree are authoritative. Do not track local configs, media, corrections, scripts/transcripts, generated voices, stems, captions, frames, weights, checkouts, receipts, personal paths or credentials. Synthetic fixtures are hand-authored mechanics examples, not episode extracts.

```sh
python tools/verify_project.py
git add AGENTS.md README.md LICENSE .gitignore .gitattributes .env.example project.example.json requirements.txt requirements.lock.txt requirements-separator.txt requirements-cleanup.txt tools tests prompts docs .agents .github
python tools/public_audit.py
git diff --cached --stat
git diff --cached
```

Run redacted Gitleaks on a tracked-only snapshot before the first commit; do not copy private work into that snapshot. Scan intended history after committing:

```sh
gitleaks git --redact --no-banner --log-opts=--all
```

The custom audit prints file names/reasons, never matched values. Repair findings before publication. Check installation and `python tools/verify_project.py` from a clean clone without episode configs or keys. CI must not need real media, paid jobs, weights or provider secrets.

After pushing, verify visibility, default branch/head, remote file tree, clean-clone checks and CI. Further media publication requires an explicit request. Keep production caches outside the public include set.
