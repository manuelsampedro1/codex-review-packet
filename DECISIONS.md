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

## Optional Readiness Report

Allow callers to embed an external `repo-flightcheck --json` report with `--readiness-report`.

Rationale:

- A useful review packet should show repo setup risks before asking a reviewer to inspect the diff.
- Keeping the report external avoids coupling this tool to Node or to a specific readiness scanner runtime.
- Only warning and failed readiness checks are expanded so clean reports stay compact.

## Generated Verification Checklist

Allow callers to invoke a local `verify-by-change` executable or `.py` script with `--verify-by-change`.

Rationale:

- Review packets are strongest when the diff, repo context, readiness report, and verification plan are produced in one handoff.
- The dependency remains optional and external, so this repo stays standard-library only.
- Running the generator without a shell keeps the integration explicit and avoids command-injection footguns.

## Verification JSON Envelopes

Render `verify-by-change.v1` JSON envelopes passed through `--verification-checklist` as Markdown checklist content instead of embedding raw JSON.

Rationale:

- Automation-friendly artifacts should still be readable in the final review packet.
- The envelope preserves source metadata while the packet presents changed files and commands in reviewer-friendly Markdown.
- Keeping this behind the existing optional checklist input avoids a hard dependency on `verify-by-change`.

## Generated Verification Envelopes

When `--verify-by-change` is used, ask the generator for `--json-envelope` first and render the envelope as Markdown. Fall back to plain Markdown only when the supplied generator does not support that option.

Rationale:

- Direct generation should preserve the same source metadata as externally supplied envelope artifacts.
- Review packets become more useful for automation handoffs when changed files, categories, and commands are structured before rendering.
- The fallback keeps older local scripts usable without weakening current `verify-by-change` integration.

## Surface Sensitive Changes Separately

Add a `Sensitive Change Check` section when changed paths include secret material, authorization or approval logic, receipts, guards, deploy paths, release paths, or workflows.

Rationale:

- Review lanes are useful, but high-risk paths need a second explicit callout so they are not treated as routine code or docs.
- Agent-generated closeouts should verify negative paths, fail-closed behavior, rollback, and leakage risk before claiming safety.
- The rule is path-based and conservative; it asks sharper review questions without pretending to perform a security scan.
