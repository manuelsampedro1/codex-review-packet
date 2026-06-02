# AGENTS.md

## Goal

- Keep `codex-review-packet` a small local CLI for sharper AI-assisted repo reviews.
- Preserve inspectable Markdown output that a reviewer can paste into Codex, Claude Code, or GitHub.

## Product Constraints

- Standard library only; do not add runtime dependencies or hosted services.
- Keep generated packets explicit about omissions, source files, and external reports.
- Prefer optional inputs over hard coupling to sibling tools.

## Engineering

- Python 3.11+.
- Keep behavior in `codex_review_packet.py` unless a split is clearly justified.
- Add unit coverage for every new packet section or CLI flag.
- Avoid hidden scoring beyond externally supplied reports.

## Verification

- `python3 -m py_compile codex_review_packet.py`
- `python3 -m unittest discover -s tests`
- `python3 codex_review_packet.py --repo . >/tmp/review-packet.md`
- `python3 codex_review_packet.py --repo . --diff-lines 80 >/tmp/review-packet-capped.md`
