#!/usr/bin/env python3
"""Generate a compact Markdown review packet from a local git repository."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any, Iterable

CONTEXT_FILES = ("AGENTS.md", "README.md", "DECISIONS.md", "TODO.md")
TASK_CONTRACT_FILES = ("AGENT_TASK.md", "TASK_CONTRACT.md")
TASK_CONTRACT_REQUIRED_SECTIONS = (
    "Objective",
    "Acceptance Criteria",
    "Context",
    "Constraints",
    "Expected Changes",
    "Verification",
    "Risks",
    "Out of Scope",
)

REVIEW_LANE_GUIDANCE = {
    "Agent instructions": "Check whether agent behavior, scope, or safety rules changed.",
    "CI and release": "Check executable gates, deploy paths, environment assumptions, and rollback impact.",
    "Security and permissions": "Check secrets, auth boundaries, permission checks, and sensitive operations.",
    "Data and persistence": "Check migrations, schemas, storage formats, and data compatibility.",
    "Tests and verification": "Check whether tests cover the behavior and whether verification commands changed.",
    "Product and docs": "Check user-facing claims, decisions, runbooks, and TODO follow-through.",
    "Application code": "Check correctness, regressions, edge cases, and integration behavior.",
    "Unmapped": "Check manually; this path did not match a known review lane.",
}

SENSITIVE_CHANGE_GUIDANCE = {
    "Secret material": "Confirm no real credentials, private keys, tokens, webhook URLs, or production identifiers are present. If a real secret appears, rotate it before merge.",
    "Authorization and approval": "Check allow, deny, expiry, malformed-input, missing-approval, and failure paths; do not verify only the happy path.",
    "Deploy or release path": "Check fail-closed behavior, environment assumptions, rollback path, and whether production actions require explicit approval.",
}

SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SECRET_TOKENS = {"secret", "secrets", "credential", "credentials", "token", "tokens", "private", "key"}
AUTH_TOKENS = {
    "auth",
    "authorize",
    "authorization",
    "approval",
    "approvals",
    "guard",
    "permission",
    "permissions",
    "policy",
    "policies",
    "receipt",
    "receipts",
    "security",
}
DEPLOY_TOKENS = {"deploy", "deployment", "release", "rollback"}

CODE_SUFFIXES = (
    ".cjs",
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
)

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


def review_lane_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    name = pathlib.PurePosixPath(normalized).name.lower()

    if name in {"agents.md", "claude.md"} or lower.startswith((".codex/", ".cursor/")):
        return "Agent instructions"
    if lower.startswith(".github/workflows/") or any(part in lower for part in ("deploy", "release", "ci.yml", "ci.yaml")):
        return "CI and release"
    if secret_material_path(lower) or auth_sensitive_path(lower) or "keychain" in lower:
        return "Security and permissions"
    if any(part in lower for part in ("migration", "schema", "database", "storage", "localstorage", "prisma", "supabase")) or lower.endswith(".sql"):
        return "Data and persistence"
    if lower.startswith(("test/", "tests/")) or any(part in lower for part in ("test_", ".test.", ".spec.", "verify", "verification")):
        return "Tests and verification"
    if name in {"readme.md", "decisions.md", "todo.md"} or lower.startswith(("docs/", "radar/", "recipes/", "labs/")) or lower.endswith(".md"):
        return "Product and docs"
    if lower.endswith(CODE_SUFFIXES):
        return "Application code"
    return "Unmapped"


def path_tokens(normalized: str) -> set[str]:
    tokens: set[str] = set()
    for segment in normalized.replace("\\", "/").replace("@", "/").split("/"):
        tokens.update(token_parts(segment))
    return tokens


def token_parts(value: str) -> list[str]:
    parts: list[str] = []
    current = []
    for character in value.lower():
        if character.isalnum():
            current.append(character)
            continue
        if current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return parts


def secret_material_path(normalized: str) -> bool:
    path = pathlib.PurePosixPath(normalized.replace("\\", "/"))
    if path.name in SECRET_FILENAMES or path.suffix in SECRET_SUFFIXES:
        return True
    return bool(path_tokens(normalized) & SECRET_TOKENS)


def auth_sensitive_path(normalized: str) -> bool:
    return bool(path_tokens(normalized) & AUTH_TOKENS)


def deploy_sensitive_path(normalized: str) -> bool:
    return normalized.startswith(".github/workflows/") or bool(path_tokens(normalized) & DEPLOY_TOKENS)


def sensitive_change_map(files: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    for path in files:
        normalized = path.replace("\\", "/").lower()
        if secret_material_path(normalized):
            buckets.setdefault("Secret material", []).append(path)
        if auth_sensitive_path(normalized):
            buckets.setdefault("Authorization and approval", []).append(path)
        if deploy_sensitive_path(normalized):
            buckets.setdefault("Deploy or release path", []).append(path)
    return buckets


def sensitive_change_section(files: list[str]) -> str:
    buckets = sensitive_change_map(files)
    if not buckets:
        return ""

    parts = [
        "## Sensitive Change Check",
        "",
        "These paths need explicit risk review before merge.",
        "",
    ]
    for label, guidance in SENSITIVE_CHANGE_GUIDANCE.items():
        paths = buckets.get(label)
        if not paths:
            continue
        parts.append(f"### {label}")
        parts.append("")
        parts.append(f"Focus: {guidance}")
        parts.append("")
        parts.extend(f"- `{path}`" for path in paths)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def review_map(files: list[str]) -> dict[str, list[str]]:
    lanes: dict[str, list[str]] = {}
    for path in files:
        lane = review_lane_for_path(path)
        lanes.setdefault(lane, []).append(path)
    return lanes


def review_map_section(files: list[str]) -> str:
    if not files:
        return "## Review Map\n\n_No review lanes; no changed files detected._\n"

    lanes = review_map(files)
    parts = ["## Review Map", ""]
    for lane in REVIEW_LANE_GUIDANCE:
        lane_files = lanes.get(lane)
        if not lane_files:
            continue
        parts.append(f"### {lane}")
        parts.append("")
        parts.append(f"Focus: {REVIEW_LANE_GUIDANCE[lane]}")
        parts.append("")
        parts.extend(f"- `{path}`" for path in lane_files)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


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


def detect_task_contract(repo: pathlib.Path) -> pathlib.Path | None:
    for name in TASK_CONTRACT_FILES:
        path = repo / name
        if path.exists() and path.is_file():
            return path
    return None


def task_contract_section(path: pathlib.Path, max_lines: int) -> str:
    text = path.read_text(encoding="utf-8")
    analysis = analyze_task_contract(text)
    body = limit_lines(text.strip() or "_Task contract is empty._", max_lines, "task contract")

    parts = [
        "## Task Contract",
        "",
        f"Source: `{path}`",
        "",
        f"- Status: `{analysis['status']}`",
        f"- Required sections: `{analysis['present_count']}/{len(TASK_CONTRACT_REQUIRED_SECTIONS)}`",
    ]

    missing = analysis["missing_sections"]
    placeholders = analysis["placeholder_sections"]
    parts.append(f"- Missing sections: {', '.join(missing) if missing else 'none'}")
    parts.append(f"- Placeholder markers: {', '.join(placeholders) if placeholders else 'none'}")
    parts.extend(["", "```md", body, "```", ""])
    return "\n".join(parts)


def analyze_task_contract(text: str) -> dict[str, Any]:
    sections = markdown_sections(text)
    normalized_required = {normalize_heading(name): name for name in TASK_CONTRACT_REQUIRED_SECTIONS}
    present = [label for key, label in normalized_required.items() if key in sections]
    missing = [label for key, label in normalized_required.items() if key not in sections]
    placeholder_sections = [
        sections[key]["title"]
        for key in sections
        if key
        if has_task_contract_placeholder(sections[key]["body"])
    ]
    if has_task_contract_placeholder(sections.get("", {"body": ""})["body"]):
        placeholder_sections.insert(0, "Preamble")

    return {
        "status": "warn" if missing or placeholder_sections else "pass",
        "present_count": len(present),
        "missing_sections": missing,
        "placeholder_sections": placeholder_sections,
    }


def markdown_sections(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {"": {"title": "Preamble", "body": ""}}
    current_key = ""
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            title = match.group(1).strip()
            current_key = normalize_heading(title)
            sections[current_key] = {"title": title, "body": ""}
            continue
        sections.setdefault(current_key, {"title": "Preamble", "body": ""})
        sections[current_key]["body"] += f"{line}\n"
    return sections


def has_task_contract_placeholder(text: str) -> bool:
    markers = {"todo", "tbd", "placeholder", "replace me"}
    for line in text.splitlines():
        stripped = line.strip().strip("-* ").strip()
        lower = stripped.lower()
        if lower in markers:
            return True
        if lower.startswith(("todo:", "tbd:", "placeholder:", "replace me:")):
            return True
        if re.fullmatch(r"\[(todo|tbd|placeholder|replace me)\]", lower):
            return True
    return False


def normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def verification_text_section(source: str, text: str, max_lines: int) -> str:
    text = text.strip()
    if not text:
        text = "_Verification checklist is empty._"
    text = limit_lines(text, max_lines, "verification checklist")
    return f"""## Verification Checklist

Source: `{source}`

```md
{text}
```
"""


def verification_checklist_section(path: pathlib.Path, max_lines: int) -> str:
    text = path.read_text(encoding="utf-8")
    envelope = parse_verification_envelope(text)
    if envelope is not None:
        return verification_envelope_section(str(path), envelope, max_lines)
    return verification_text_section(str(path), text, max_lines)


def parse_verification_envelope(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != "verify-by-change.v1":
        return None
    return payload


def verification_envelope_section(source: str, payload: dict[str, Any], max_lines: int) -> str:
    schema = str(payload.get("schema_version", "unknown"))
    source_label = verification_source_label(payload.get("source"))
    context_lines = verification_envelope_context_lines(payload)
    context = "\n".join(context_lines)
    if context:
        context = f"\n{context}\n"
    body = verification_envelope_markdown(payload)
    body = limit_lines(body, max_lines, "verification checklist")
    return f"""## Verification Checklist

Source: `{source}`
Envelope: `{schema}`
Verification source: `{source_label}`
{context}

```md
{body}
```
"""


def verification_source_label(source: object) -> str:
    if not isinstance(source, dict):
        return "unknown"
    source_type = source.get("type") or "unknown"
    details: list[str] = [str(source_type)]
    repo = source.get("repo")
    review_packet = source.get("review_packet")
    base = source.get("base")
    if repo:
        details.append(f"repo={repo}")
    if review_packet:
        details.append(f"review_packet={review_packet}")
    if base:
        details.append(f"base={base}")
    if source.get("staged"):
        details.append("staged=true")
    if source.get("include_working_tree"):
        details.append("include_working_tree=true")
    return ", ".join(details)


def verification_envelope_context_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    task_contract = payload.get("task_contract")
    if isinstance(task_contract, dict):
        status = str(task_contract.get("status") or "unknown")
        required_sections = str(
            task_contract.get("required_sections")
            or task_contract.get("requiredSections")
            or "unknown"
        )
        lines.append(f"Task contract: `{status}` ({required_sections} required sections)")
        source = task_contract.get("source")
        if source:
            lines.append(f"Task contract source: `{source}`")
        missing = verification_envelope_list(task_contract.get("missing_sections"))
        placeholders = verification_envelope_list(task_contract.get("placeholder_markers"))
        if missing:
            lines.append(f"Missing task sections: {', '.join(missing)}")
        if placeholders:
            lines.append(f"Task contract placeholders: {', '.join(placeholders)}")
    return lines


def verification_envelope_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def verification_envelope_markdown(payload: dict[str, Any]) -> str:
    changed_files = payload.get("changed_files", [])
    categories = payload.get("categories", {})
    lines = ["# Verification Checklist", ""]
    if payload.get("empty") or not changed_files:
        lines.extend([
            "No changed files detected.",
            "",
            "- Confirm the target ref, staged state, or working tree is what you intended to verify.",
        ])
        return "\n".join(lines).rstrip()

    if isinstance(changed_files, list):
        lines.append("Changed files:")
        lines.append("")
        for path in changed_files:
            lines.append(f"- `{path}`")
        lines.append("")

    if not isinstance(categories, dict) or not categories:
        lines.append("- No verification categories were supplied.")
        return "\n".join(lines).rstrip()

    for name, category in categories.items():
        if not isinstance(category, dict):
            continue
        lines.append(f"## {str(name).replace('_', ' ').title()}")
        lines.append("")
        files = category.get("files", [])
        commands = category.get("commands", [])
        if isinstance(files, list):
            lines.extend(f"- `{path}`" for path in files)
            lines.append("")
        if isinstance(commands, list):
            lines.extend(f"- {command}" for command in commands)
            lines.append("")
    return "\n".join(lines).rstrip()


def verify_by_change_command(
    command: str,
    repo: pathlib.Path,
    base: str | None,
    staged: bool,
    json_envelope: bool = False,
) -> str:
    command_path = pathlib.Path(command)
    args = [str(command_path), "--repo", str(repo)]
    if command_path.suffix == ".py":
        args = [sys.executable, *args]
    if json_envelope:
        args.append("--json-envelope")
    if staged:
        args.append("--staged")
    elif base:
        args.extend(["--base", base])

    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "verify-by-change command failed"
        raise SystemExit(message)
    return result.stdout


def unsupported_json_envelope_error(message: str) -> bool:
    lower = message.lower()
    return "--json-envelope" in lower and any(
        phrase in lower
        for phrase in (
            "unrecognized",
            "unknown",
            "invalid option",
            "no such option",
        )
    )


def generated_verification_checklist_section(
    command: str,
    repo: pathlib.Path,
    base: str | None,
    staged: bool,
    max_lines: int,
) -> str:
    try:
        text = verify_by_change_command(command, repo, base, staged, json_envelope=True)
    except SystemExit as exc:
        message = str(exc)
        if not unsupported_json_envelope_error(message):
            raise
        text = verify_by_change_command(command, repo, base, staged)

    envelope = parse_verification_envelope(text)
    if envelope is not None:
        return verification_envelope_section(f"verify-by-change: {command}", envelope, max_lines)
    return verification_text_section(f"verify-by-change: {command}", text, max_lines)


def readiness_report_section(path: pathlib.Path, max_checks: int) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") == "repo-flightcheck.agent-contract.v1":
        return readiness_contract_section(path, payload, max_checks)

    summary: dict[str, Any] = payload.get("summary", {})
    checks: list[dict[str, Any]] = payload.get("checks", [])
    next_fixes: list[str] = payload.get("nextFixes", [])

    score = summary.get("score", "unknown")
    points_possible = summary.get("pointsPossible", 100)
    passed = summary.get("passed", 0)
    warnings = summary.get("warnings", 0)
    failed = summary.get("failed", 0)
    critical_failures = summary.get("criticalFailures", 0)
    stack = payload.get("stack", "unknown")

    attention_checks = [check for check in checks if check.get("status") != "pass"][:max_checks]

    parts = [
        "## Repo Readiness",
        "",
        f"Source: `{path}`",
        "",
        f"- Score: `{score}/{points_possible}`",
        f"- Stack: `{stack}`",
        f"- Summary: `{passed}` passed, `{warnings}` warnings, `{failed}` failed, `{critical_failures}` critical failures.",
        "",
    ]

    if attention_checks:
        parts.extend(["Attention checks:", ""])
        for check in attention_checks:
            status = str(check.get("status", "unknown")).upper()
            title = check.get("title", "Untitled check")
            message = check.get("message", "No message.")
            parts.append(f"- `{status}` {title}: {message}")
        omitted = len([check for check in checks if check.get("status") != "pass"]) - len(attention_checks)
        if omitted > 0:
            parts.append(f"- `...` {omitted} more readiness checks omitted")
        parts.append("")
    else:
        parts.extend(["No warning or failed readiness checks.", ""])

    if next_fixes:
        parts.extend(["Next fixes:", ""])
        parts.extend(f"- {fix}" for fix in next_fixes[:max_checks])
        if len(next_fixes) > max_checks:
            parts.append(f"- `...` {len(next_fixes) - max_checks} more fixes omitted")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def ci_run_section(path: pathlib.Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    run = normalize_ci_run_payload(payload)

    parts = [
        "## CI Evidence",
        "",
        f"Source: `{path}`",
        "",
        f"- Run: `{run['id']}`",
        f"- Workflow: `{run['name']}`",
        f"- Status: `{run['status']}`",
        f"- Conclusion: `{run['conclusion']}`",
    ]

    if run["head_branch"]:
        parts.append(f"- Branch: `{run['head_branch']}`")
    if run["head_sha"]:
        parts.append(f"- SHA: `{run['head_sha']}`")
    if run["event"]:
        parts.append(f"- Event: `{run['event']}`")
    if run["html_url"]:
        parts.append(f"- URL: <{run['html_url']}>")

    parts.append("")
    return "\n".join(parts)


def published_head_section(path: pathlib.Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    proof = normalize_published_head_payload(payload)

    parts = [
        "## Published HEAD",
        "",
        f"Source: `{path}`",
        "",
        f"- Status: `{proof['status']}`",
        f"- Message: {proof['message']}",
    ]

    if proof["source_schema"]:
        parts.append(f"- Schema: `{proof['source_schema']}`")
    if proof["remote"]:
        parts.append(f"- Remote: `{sanitize_remote_text(proof['remote'])}`")
    if proof["branch"]:
        parts.append(f"- Branch: `{proof['branch']}`")
    if proof["local_head"]:
        parts.append(f"- Local HEAD: `{proof['local_head']}`")
    if proof["remote_head"]:
        parts.append(f"- Remote HEAD: `{proof['remote_head']}`")
    if proof["commit_url"]:
        parts.append(f"- Commit URL: <{proof['commit_url']}>")
    if proof["ci_url"]:
        parts.append(f"- CI URL: <{proof['ci_url']}>")
    if proof["evidence"]:
        parts.extend(["", "Evidence:"])
        parts.extend(f"- `{sanitize_remote_text(item)}`" for item in proof["evidence"])

    parts.append("")
    return "\n".join(parts)


def normalize_published_head_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("Published HEAD proof must be a JSON object.")

    check = repo_flightcheck_git_remote_check(payload)
    if check is not None:
        return {
            "source_schema": "repo-flightcheck",
            "status": str(check.get("status", "unknown")),
            "message": str(check.get("message", "No message.")),
            "remote": "",
            "branch": "",
            "local_head": "",
            "remote_head": "",
            "commit_url": "",
            "ci_url": "",
            "evidence": [str(item) for item in check.get("evidence", []) if item],
        }

    status = payload.get("status")
    if status is None and "published" in payload:
        status = "pass" if payload.get("published") else "warn"
    if not status:
        raise SystemExit("Published HEAD proof must include status or published.")

    return {
        "source_schema": str(payload.get("schema_version") or payload.get("schemaVersion") or ""),
        "status": str(status),
        "message": str(payload.get("message") or "No message."),
        "remote": str(payload.get("remote") or payload.get("remote_url") or ""),
        "branch": str(payload.get("branch") or ""),
        "local_head": str(payload.get("local_head") or payload.get("localHead") or ""),
        "remote_head": str(payload.get("remote_head") or payload.get("remoteHead") or ""),
        "commit_url": str(payload.get("commit_url") or payload.get("commitUrl") or ""),
        "ci_url": str(payload.get("ci_url") or payload.get("ciUrl") or ""),
        "evidence": [str(item) for item in payload.get("evidence", []) if item] if isinstance(payload.get("evidence"), list) else [],
    }


def repo_flightcheck_git_remote_check(payload: dict[str, Any]) -> dict[str, Any] | None:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if isinstance(check, dict) and (check.get("id") == "git-remote" or check.get("title") == "Git remote"):
            return check
    return None


def sanitize_remote_text(text: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", str(text))


def normalize_ci_run_payload(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise SystemExit("CI run report must be a JSON object.")

    if isinstance(payload.get("workflow_runs"), list):
        runs = payload["workflow_runs"]
        if not runs:
            raise SystemExit("CI run list is empty.")
        payload = runs[0]

    if not isinstance(payload, dict):
        raise SystemExit("CI run report entry must be a JSON object.")

    run_id = payload.get("id") or payload.get("run_id")
    name = payload.get("name") or payload.get("workflow_name")
    status = payload.get("status")

    if not run_id or not name or not status:
        raise SystemExit("CI run report must include id, name, and status.")

    conclusion = payload.get("conclusion")
    return {
        "id": str(run_id),
        "name": str(name),
        "status": str(status),
        "conclusion": "null" if conclusion is None else str(conclusion),
        "head_branch": str(payload.get("head_branch") or ""),
        "head_sha": str(payload.get("head_sha") or ""),
        "event": str(payload.get("event") or ""),
        "html_url": str(payload.get("html_url") or ""),
    }


def readiness_contract_section(path: pathlib.Path, payload: dict[str, Any], max_checks: int) -> str:
    required: list[dict[str, Any]] = payload.get("requiredBeforeAgent", [])
    recommended: list[dict[str, Any]] = payload.get("recommendedBeforeAgent", [])
    next_fixes: list[str] = payload.get("nextFixes", [])

    parts = [
        "## Repo Readiness",
        "",
        f"Source: `{path}`",
        "",
        "- Contract: `repo-flightcheck.agent-contract.v1`",
        f"- Ready: `{str(payload.get('ready', False)).lower()}`",
        f"- Score: `{payload.get('score', 'unknown')}/100`",
        f"- Threshold: `{payload.get('threshold', 'unknown')}`",
        f"- Stack: `{payload.get('stack', 'unknown')}`",
        f"- Summary: `{len(required)}` required blockers, `{len(recommended)}` recommendations, `{payload.get('criticalFailures', 0)}` critical failures.",
        "",
    ]

    append_contract_checks(parts, "Required before agent:", required, max_checks)
    append_contract_checks(parts, "Recommended before agent:", recommended, max_checks)

    if not required and not recommended:
        parts.extend(["No required blockers or recommendations.", ""])

    if next_fixes:
        parts.extend(["Next fixes:", ""])
        parts.extend(f"- {fix}" for fix in next_fixes[:max_checks])
        if len(next_fixes) > max_checks:
            parts.append(f"- `...` {len(next_fixes) - max_checks} more fixes omitted")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def append_contract_checks(parts: list[str], title: str, checks: list[dict[str, Any]], max_checks: int) -> None:
    if not checks:
        return

    parts.extend([title, ""])
    for check in checks[:max_checks]:
        status = str(check.get("status", "unknown")).upper()
        check_title = check.get("title", "Untitled check")
        message = check.get("message", "No message.")
        parts.append(f"- `{status}` {check_title}: {message}")
    omitted = len(checks) - min(len(checks), max_checks)
    if omitted > 0:
        parts.append(f"- `...` {omitted} more readiness checks omitted")
    parts.append("")


def build_packet(
    repo: pathlib.Path,
    base: str | None,
    staged: bool,
    max_lines: int,
    max_untracked_lines: int = 80,
    max_diff_lines: int | None = None,
    verification_checklist: pathlib.Path | None = None,
    verify_by_change: str | None = None,
    max_verification_lines: int = 120,
    readiness_report: pathlib.Path | None = None,
    max_readiness_checks: int = 8,
    ci_run: pathlib.Path | None = None,
    published_head: pathlib.Path | None = None,
    task_contract: pathlib.Path | None = None,
    max_task_contract_lines: int = 120,
) -> str:
    files = changed_files(repo, base, staged)
    diff = limit_lines(diff_body(repo, base, staged, max_untracked_lines).strip(), max_diff_lines, "diff")
    context = context_sections(repo, max_lines)
    task_contract_path = task_contract or detect_task_contract(repo)
    verification_block = (
        f"\n{verification_checklist_section(verification_checklist, max_verification_lines)}"
        if verification_checklist
        else (
            f"\n{generated_verification_checklist_section(verify_by_change, repo, base, staged, max_verification_lines)}"
            if verify_by_change
            else ""
        )
    )
    readiness_block = (
        f"\n{readiness_report_section(readiness_report, max_readiness_checks)}"
        if readiness_report
        else ""
    )
    ci_block = (
        f"\n{ci_run_section(ci_run)}"
        if ci_run
        else ""
    )
    published_head_block = (
        f"\n{published_head_section(published_head)}"
        if published_head
        else ""
    )
    task_contract_block = (
        f"\n{task_contract_section(task_contract_path, max_task_contract_lines)}"
        if task_contract_path
        else ""
    )

    file_block = "\n".join(f"- `{path}`" for path in files) if files else "- No changed files detected."
    review_block = review_map_section(files)
    sensitive_block = sensitive_change_section(files)
    context_block = "\n\n".join(context) if context else "_No top-level repo context files found._"
    diff_block = diff if diff else "# No diff detected."

    base_label = "staged changes" if staged else (base or "working tree")

    return f"""# Review Packet

Repo: `{repo}`
Base: `{base_label}`

## Changed Files

{file_block}

{review_block}
{sensitive_block}

## Repo Context

{context_block}
{task_contract_block}
{readiness_block}
{ci_block}
{published_head_block}

## Diff

```diff
{diff_block}
```
{verification_block}

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
    parser.add_argument("--verification-checklist", help="Optional Markdown checklist to include in the packet.")
    parser.add_argument(
        "--verify-by-change",
        help="Optional verify-by-change executable or .py script to generate and include a checklist.",
    )
    parser.add_argument("--verification-lines", type=positive_int, default=120, help="Max lines for the verification checklist block.")
    parser.add_argument("--readiness-report", help="Optional repo-flightcheck JSON report to include in the packet.")
    parser.add_argument("--readiness-checks", type=positive_int, default=8, help="Max warning or failed readiness checks to include.")
    parser.add_argument("--ci-run", help="Optional GitHub Actions run JSON to include in the packet.")
    parser.add_argument("--published-head", help="Optional published-HEAD proof JSON to include in the packet.")
    parser.add_argument("--task-contract", help="Optional AGENT_TASK.md or TASK_CONTRACT.md file to include in the packet.")
    parser.add_argument("--task-contract-lines", type=positive_int, default=120, help="Max lines for the task contract block.")
    parser.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def main() -> int:
    args = parse_args()
    if args.verification_checklist and args.verify_by_change:
        raise SystemExit("Use either --verification-checklist or --verify-by-change, not both.")
    repo = pathlib.Path(args.repo).resolve()
    packet = build_packet(
        repo,
        args.base,
        args.staged,
        args.context_lines,
        args.untracked_lines,
        args.diff_lines,
        pathlib.Path(args.verification_checklist).resolve() if args.verification_checklist else None,
        args.verify_by_change,
        args.verification_lines,
        pathlib.Path(args.readiness_report).resolve() if args.readiness_report else None,
        args.readiness_checks,
        pathlib.Path(args.ci_run).resolve() if args.ci_run else None,
        pathlib.Path(args.published_head).resolve() if args.published_head else None,
        pathlib.Path(args.task_contract).resolve() if args.task_contract else None,
        args.task_contract_lines,
    )
    if args.output:
        pathlib.Path(args.output).write_text(packet, encoding="utf-8")
        print(f"Wrote review packet to {shlex.quote(args.output)}")
    else:
        sys.stdout.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
