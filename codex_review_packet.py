#!/usr/bin/env python3
"""Generate a compact Markdown review packet from a local git repository."""

from __future__ import annotations

import argparse
import pathlib
import shlex
import subprocess
import sys
from typing import Iterable

CONTEXT_FILES = ("AGENTS.md", "README.md", "DECISIONS.md", "TODO.md")

REVIEW_PROMPT = """Review this repo change like a strict senior engineer.

Focus on findings that would change whether the diff should merge.
Check correctness, regressions, missing verification, and scope drift.
Return findings first. If there are no meaningful findings, say that explicitly.
"""


def run_git(repo: pathlib.Path, args: Iterable[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git command failed")
    return result.stdout


def has_head(repo: pathlib.Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def parse_status_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def status_paths(repo: pathlib.Path, prefix: str | None = None) -> list[str]:
    output = run_git(repo, ["status", "--porcelain", "--untracked-files=all"])
    paths: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        if prefix and not line.startswith(prefix):
            continue
        paths.extend(parse_status_paths(line))
    return paths


def changed_files(repo: pathlib.Path, base: str | None, staged: bool) -> list[str]:
    if staged:
        output = run_git(repo, ["diff", "--cached", "--name-only"])
    elif base:
        if not has_head(repo):
            return status_paths(repo)
        output = run_git(repo, ["diff", "--name-only", f"{base}...HEAD"])
    else:
        return status_paths(repo)
    return [line.strip() for line in output.splitlines() if line.strip()]


def untracked_file_diff(repo: pathlib.Path, raw_path: str, max_lines: int) -> str:
    path = repo / raw_path
    if not path.exists() or path.is_dir():
        return f"# untracked file unavailable: {raw_path}"

    data = path.read_bytes()
    header = f"diff --git a/{raw_path} b/{raw_path}\nnew file mode 100644\n--- /dev/null\n+++ b/{raw_path}\n@@"

    if b"\0" in data:
        return f"{header}\n# Binary untracked file omitted."

    lines = data.decode("utf-8", errors="replace").splitlines()
    selected = lines[:max_lines]
    body = "\n".join(f"+{line}" for line in selected)
    omitted = len(lines) - len(selected)
    if omitted:
        body = f"{body}\n+# ... {omitted} more lines omitted" if body else f"+# ... {omitted} more lines omitted"
    return f"{header}\n{body}".rstrip()


def working_tree_diff(repo: pathlib.Path, max_untracked_lines: int) -> str:
    parts: list[str] = []
    staged_diff = run_git(repo, ["diff", "--cached"]).strip()
    unstaged_diff = run_git(repo, ["diff"]).strip()

    if staged_diff:
        parts.append(staged_diff)
    if unstaged_diff:
        parts.append(unstaged_diff)
    for path in status_paths(repo, prefix="?? "):
        parts.append(untracked_file_diff(repo, path, max_untracked_lines))
    return "\n\n".join(part for part in parts if part).strip()


def diff_body(repo: pathlib.Path, base: str | None, staged: bool, max_untracked_lines: int) -> str:
    if staged:
        return run_git(repo, ["diff", "--cached"])
    if base:
        if not has_head(repo):
            files = changed_files(repo, base, staged=False)
            return "\n".join(f"# new file: {path}" for path in files)
        return run_git(repo, ["diff", f"{base}...HEAD"])
    return working_tree_diff(repo, max_untracked_lines)


def limit_lines(text: str, max_lines: int | None, label: str) -> str:
    if max_lines is None:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    return "\n".join([*lines[:max_lines], f"# ... {omitted} more {label} lines omitted"])


def context_sections(repo: pathlib.Path, max_lines: int) -> list[str]:
    sections: list[str] = []
    for name in CONTEXT_FILES:
        path = repo / name
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        excerpt = "\n".join(lines[:max_lines]).strip()
        if not excerpt:
            continue
        sections.append(f"### {name}\n\n```md\n{excerpt}\n```")
    return sections


def build_packet(
    repo: pathlib.Path,
    base: str | None,
    staged: bool,
    max_lines: int,
    max_untracked_lines: int = 80,
    max_diff_lines: int | None = None,
) -> str:
    files = changed_files(repo, base, staged)
    diff = limit_lines(diff_body(repo, base, staged, max_untracked_lines).strip(), max_diff_lines, "diff")
    context = context_sections(repo, max_lines)

    file_block = "\n".join(f"- `{path}`" for path in files) if files else "- No changed files detected."
    context_block = "\n\n".join(context) if context else "_No top-level repo context files found._"
    diff_block = diff if diff else "# No diff detected."

    base_label = "staged changes" if staged else (base or "working tree")

    return f"""# Review Packet

Repo: `{repo}`
Base: `{base_label}`

## Changed Files

{file_block}

## Repo Context

{context_block}

## Diff

```diff
{diff_block}
```

## Suggested Review Prompt

```text
{REVIEW_PROMPT.strip()}
```
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Path to the git repository.")
    parser.add_argument("--base", help="Base ref to diff against, for example origin/main.")
    parser.add_argument("--staged", action="store_true", help="Use staged changes instead of a base ref.")
    parser.add_argument("--context-lines", type=int, default=80, help="Max lines per context file.")
    parser.add_argument("--untracked-lines", type=int, default=80, help="Max preview lines per untracked text file.")
    parser.add_argument("--diff-lines", type=positive_int, help="Max lines for the combined diff block.")
    parser.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def main() -> int:
    args = parse_args()
    repo = pathlib.Path(args.repo).resolve()
    packet = build_packet(
        repo,
        args.base,
        args.staged,
        args.context_lines,
        args.untracked_lines,
        args.diff_lines,
    )
    if args.output:
        pathlib.Path(args.output).write_text(packet, encoding="utf-8")
        print(f"Wrote review packet to {shlex.quote(args.output)}")
    else:
        sys.stdout.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
