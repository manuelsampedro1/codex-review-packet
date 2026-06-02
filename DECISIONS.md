# Decisions

## Standard Library Only

Use Python standard library plus local `git`.

Rationale:

- Easier to run inside Codex or any local repo.
- No dependency install tax for a simple workflow helper.
- Keeps the repo readable for clients evaluating how I build small tools.

## Markdown First Output

Emit Markdown, not JSON, as the primary output format.

Rationale:

- The main use case is human plus model review.
- Markdown is easier to inspect, paste, and tweak in GitHub or editors.
- It also doubles as saved review context for later audits.

## Working Tree Evidence

When reviewing a working tree, include staged, unstaged, and untracked evidence in the packet.

Rationale:

- Codex review packets should match what a reviewer actually needs to inspect.
- Listing an untracked file without showing any content weakens the handoff.
- Staged-only mode remains available when the reviewer intentionally wants the index.

## Bounded Diff Context

Allow callers to cap the combined diff block with `--diff-lines`.

Rationale:

- Review packets should stay useful inside model context windows.
- Large diffs still need an honest omission marker instead of silently dropping content.
- The default remains uncapped so local reviewers can choose the tradeoff explicitly.

## Optional Verification Checklist

Allow callers to embed an external Markdown verification checklist with `--verification-checklist`.

Rationale:

- Review packets are stronger when they include both the diff and the proposed verification plan.
- Keeping the checklist external avoids coupling this repo to a specific verification generator.
- A line cap keeps the packet usable in model context while preserving an explicit omission marker.

## Review Map Before Diff

Include a path-derived review map before repo context and diff details.

Rationale:

- Mixed agent diffs need routing, not only a flat changed-file list.
- Simple lanes help reviewers focus on CI, security, data, tests, docs, agent instructions, and application code without hiding judgment behind a score.
- The map stays deterministic and path-based so a reviewer can challenge or ignore it easily.
