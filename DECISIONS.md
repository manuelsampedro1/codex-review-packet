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

