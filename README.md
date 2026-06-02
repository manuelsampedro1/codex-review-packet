# codex-review-packet

Generate a compact Markdown packet for Codex or Claude Code before a repo review.

The problem: most AI review output gets generic when the model sees only a diff and no repo rules. This tool bundles the diff, nearby repo context, and a suggested review prompt into one handoff file.

## What It Does

- Reads a local Git repository.
- Collects changed files from `HEAD` against a base ref or from the index.
- In working-tree mode, includes staged, unstaged, and untracked file evidence.
- Pulls nearby repo context from files such as `AGENTS.md`, `README.md`, `DECISIONS.md`, and `TODO.md`.
- Builds a review map that routes changed files into review lanes such as CI, security, tests, docs, agent instructions, and application code.
- Can cap the combined diff block so large packets stay usable in model context.
- Can embed a `repo-flightcheck --json` readiness report so review packets include repo setup risks before the diff.
- Can embed a Markdown verification checklist or a `verify-by-change --json-envelope` artifact.
- Can invoke a local `verify-by-change` executable or `.py` script directly and embed the generated checklist.
- Writes one Markdown packet that can be pasted into Codex or attached to another review workflow.

## Why This Exists

I wanted a small utility that makes AI repo reviews sharper without adding a hosted service or another framework. The output is designed for real use in local Codex sessions.

## Stack

- Python 3.11+
- Standard library only
- Git CLI installed locally

## Quick Start

Install from a local checkout:

```sh
python3 -m pip install -e .
```

```sh
python3 codex_review_packet.py --repo /path/to/repo --base origin/main --output review-packet.md
```

Staged review:

```sh
python3 codex_review_packet.py --repo /path/to/repo --staged
```

Working-tree review with limited untracked previews:

```sh
python3 codex_review_packet.py --repo /path/to/repo --untracked-lines 40 --output review-packet.md
```

Large review with a capped diff block:

```sh
python3 codex_review_packet.py --repo /path/to/repo --diff-lines 300 --output review-packet.md
```

Review packet with a change-aware verification checklist:

```sh
python3 /path/to/verify-by-change/verify_by_change.py --repo /path/to/repo --output /tmp/verification-checklist.md
python3 codex_review_packet.py \
  --repo /path/to/repo \
  --verification-checklist /tmp/verification-checklist.md \
  --output review-packet.md
```

Review packet with a machine-readable verification envelope rendered as Markdown:

```sh
python3 /path/to/verify-by-change/verify_by_change.py \
  --repo /path/to/repo \
  --json-envelope \
  --output /tmp/verification-envelope.json
python3 codex_review_packet.py \
  --repo /path/to/repo \
  --verification-checklist /tmp/verification-envelope.json \
  --output review-packet.md
```

Review packet that generates the checklist directly from a sibling `verify-by-change` checkout:

```sh
python3 codex_review_packet.py \
  --repo /path/to/repo \
  --verify-by-change /path/to/verify-by-change/verify_by_change.py \
  --output review-packet.md
```

Review packet with repo readiness context:

```sh
node /path/to/repo-flightcheck/bin/repo-flightcheck.js /path/to/repo --json > /tmp/repo-readiness.json
python3 codex_review_packet.py \
  --repo /path/to/repo \
  --readiness-report /tmp/repo-readiness.json \
  --output review-packet.md
```

The repo also includes a small sample report at `examples/readiness-report.json` for local smoke tests.

## Example Output

````md
# Review Packet

Base ref: origin/main
Changed files:
- README.md
- scripts/deploy.sh

## Review Map
### CI and release
Focus: Check executable gates, deploy paths, environment assumptions, and rollback impact.
- `scripts/deploy.sh`

### Product and docs
Focus: Check user-facing claims, decisions, runbooks, and TODO follow-through.
- `README.md`

## Repo Context
### AGENTS.md
...

## Repo Readiness
Score: `84/100`
...

## Diff
```diff
...
```

## Suggested Review Prompt
Review this change like a strict senior engineer...
````

## Status

Working v1. The packet is intended to be inspectable and easy to modify, not "smart" in hidden ways.

## Verification

Run from this repo:

```sh
python3 -m py_compile codex_review_packet.py
python3 -m unittest discover -s tests
make test
make build
make lint
python3 codex_review_packet.py --repo . >/tmp/review-packet.md
python3 codex_review_packet.py --repo . --diff-lines 80 >/tmp/review-packet-capped.md
printf '## Python\n\n- Run unit tests.\n' >/tmp/verification-checklist.md
python3 codex_review_packet.py --repo . --verification-checklist /tmp/verification-checklist.md >/tmp/review-packet-with-checklist.md
python3 /Users/manuelsampedro/Documents/Codex/2026-05-24/flagships/verify-by-change/verify_by_change.py README.md codex_review_packet.py --json-envelope --output /tmp/verification-envelope.json
python3 codex_review_packet.py --repo . --verification-checklist /tmp/verification-envelope.json >/tmp/review-packet-with-envelope.md
printf 'import sys\nprint("# Verification Checklist")\nprint("## Generated")\nprint("- args: " + " ".join(sys.argv[1:]))\n' >/tmp/fake_verify_by_change.py
python3 codex_review_packet.py --repo . --verify-by-change /tmp/fake_verify_by_change.py >/tmp/review-packet-generated-checklist.md
python3 codex_review_packet.py --repo . --readiness-report examples/readiness-report.json >/tmp/review-packet-with-readiness.md
grep -q '## Review Map' /tmp/review-packet.md
grep -q 'Envelope: `verify-by-change.v1`' /tmp/review-packet-with-envelope.md
grep -q 'verify-by-change:' /tmp/review-packet-generated-checklist.md
grep -q '## Repo Readiness' /tmp/review-packet-with-readiness.md
test -s /tmp/review-packet.md
```

## Files

- `codex_review_packet.py`: CLI entrypoint.
- `tests/`: working-tree packet, staged packet, and CLI coverage.
- `examples/`: small sample inputs for packet sections.
- `AGENTS.md`: repo contract for AI-assisted work.
- `pyproject.toml`: local install and CLI metadata.
- `Makefile`: short verification aliases.
- `DECISIONS.md`: small design notes for the repo.
