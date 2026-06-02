from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codex_review_packet import (  # noqa: E402
    build_packet,
    changed_files,
    ci_run_section,
    generated_verification_checklist_section,
    limit_lines,
    normalize_ci_run_payload,
    parse_status_paths,
    parse_verification_envelope,
    published_head_section,
    readiness_report_section,
    review_lane_for_path,
    review_map_section,
    sensitive_change_section,
    task_contract_section,
    untracked_file_diff,
    verification_checklist_section,
    verification_envelope_section,
    verification_envelope_markdown,
)


def run(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def init_repo(path: pathlib.Path) -> None:
    run("git", "init", cwd=path)
    run("git", "config", "user.name", "Test User", cwd=path)
    run("git", "config", "user.email", "test@example.com", cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    run("git", "add", "README.md", cwd=path)
    run("git", "commit", "-m", "initial", cwd=path)


def valid_task_contract() -> str:
    return """# Agent Task

## Objective

Ship a focused repo improvement.

## Acceptance Criteria

- Packet includes the task contract.

## Context

Reviewers need the expected outcome before reading the diff and may compare TODO.md.

## Constraints

- Keep the tool local-first.

## Expected Changes

- Add a bounded task contract section.

## Verification

- Run unit tests.

## Risks

- Packet context can become too large.

## Out of Scope

- Hosted review workflows.
"""


class ReviewPacketTests(unittest.TestCase):
    def test_parse_status_paths_handles_renames(self) -> None:
        output = " M README.md\nA  script.sh\nR  old.txt -> new.txt\n?? scratch.js\n"

        self.assertEqual(
            parse_status_paths(output),
            ["README.md", "script.sh", "new.txt", "scratch.js"],
        )

    def test_review_lane_for_path_routes_common_agent_review_risks(self) -> None:
        cases = {
            "AGENTS.md": "Agent instructions",
            ".github/workflows/ci.yml": "CI and release",
            "src/auth/session.py": "Security and permissions",
            "migrations/001_init.sql": "Data and persistence",
            "tests/test_cli.py": "Tests and verification",
            "docs/runbook.md": "Product and docs",
            "src/app.py": "Application code",
            "assets/logo.bin": "Unmapped",
        }

        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(review_lane_for_path(path), expected)

    def test_review_map_section_groups_files_with_focus_prompts(self) -> None:
        section = review_map_section([
            "AGENTS.md",
            ".github/workflows/ci.yml",
            "tests/test_cli.py",
            "src/app.py",
        ])

        self.assertIn("## Review Map", section)
        self.assertIn("### Agent instructions", section)
        self.assertIn("Focus: Check whether agent behavior", section)
        self.assertIn("- `.github/workflows/ci.yml`", section)
        self.assertIn("### Tests and verification", section)
        self.assertIn("### Application code", section)

    def test_sensitive_change_section_flags_secrets_auth_and_deploy_paths(self) -> None:
        section = sensitive_change_section([
            ".env",
            "config/private-key.pem",
            "permission_protocol/client.py",
            "src/approval-server.ts",
            "scripts/deploy.sh",
            ".github/workflows/release.yml",
        ])

        self.assertIn("## Sensitive Change Check", section)
        self.assertIn("### Secret material", section)
        self.assertIn("- `.env`", section)
        self.assertIn("- `config/private-key.pem`", section)
        self.assertIn("### Authorization and approval", section)
        self.assertIn("- `permission_protocol/client.py`", section)
        self.assertIn("- `src/approval-server.ts`", section)
        self.assertIn("### Deploy or release path", section)
        self.assertIn("- `scripts/deploy.sh`", section)
        self.assertIn("- `.github/workflows/release.yml`", section)

    def test_sensitive_change_section_is_empty_for_routine_paths(self) -> None:
        self.assertEqual(sensitive_change_section(["README.md", "src/app.py"]), "")

    def test_working_tree_packet_includes_staged_unstaged_and_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)

            (repo / "staged.py").write_text("print('staged')\n", encoding="utf-8")
            run("git", "add", "staged.py", cwd=repo)
            (repo / "README.md").write_text("initial\nunstaged\n", encoding="utf-8")
            (repo / "notes.md").write_text("untracked note\n", encoding="utf-8")

            packet = build_packet(repo, base=None, staged=False, max_lines=20, max_untracked_lines=10)

            self.assertIn("- `staged.py`", packet)
            self.assertIn("- `README.md`", packet)
            self.assertIn("- `notes.md`", packet)
            self.assertIn("## Review Map", packet)
            self.assertIn("### Application code", packet)
            self.assertIn("### Product and docs", packet)
            self.assertIn("+print('staged')", packet)
            self.assertIn("+unstaged", packet)
            self.assertIn("diff --git a/notes.md b/notes.md", packet)
            self.assertIn("+untracked note", packet)

    def test_working_tree_packet_includes_sensitive_change_check(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)

            (repo / ".env").write_text("PP_API_KEY=example-placeholder\n", encoding="utf-8")
            (repo / "permission_protocol").mkdir()
            (repo / "permission_protocol" / "client.py").write_text("print('approval path')\n", encoding="utf-8")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "deploy.sh").write_text("echo deploy\n", encoding="utf-8")

            packet = build_packet(repo, base=None, staged=False, max_lines=20, max_untracked_lines=10)

            self.assertIn("## Sensitive Change Check", packet)
            self.assertIn("### Secret material", packet)
            self.assertIn("- `.env`", packet)
            self.assertIn("### Authorization and approval", packet)
            self.assertIn("- `permission_protocol/client.py`", packet)
            self.assertIn("### Deploy or release path", packet)
            self.assertIn("- `scripts/deploy.sh`", packet)

    def test_staged_packet_excludes_unstaged_and_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)

            (repo / "staged.py").write_text("print('staged')\n", encoding="utf-8")
            run("git", "add", "staged.py", cwd=repo)
            (repo / "README.md").write_text("initial\nunstaged\n", encoding="utf-8")
            (repo / "notes.md").write_text("untracked note\n", encoding="utf-8")

            packet = build_packet(repo, base=None, staged=True, max_lines=20, max_untracked_lines=10)

            self.assertEqual(changed_files(repo, base=None, staged=True), ["staged.py"])
            self.assertIn("+print('staged')", packet)
            self.assertNotIn("+unstaged", packet)
            self.assertNotIn("untracked note", packet)

    def test_untracked_preview_limits_lines_and_marks_omission(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            (repo / "notes.md").write_text("one\ntwo\nthree\n", encoding="utf-8")

            diff = untracked_file_diff(repo, "notes.md", max_lines=2)

            self.assertIn("+one", diff)
            self.assertIn("+two", diff)
            self.assertIn("1 more lines omitted", diff)
            self.assertNotIn("+three", diff)

    def test_limit_lines_marks_omitted_diff_lines(self) -> None:
        limited = limit_lines("one\ntwo\nthree\nfour", max_lines=2, label="diff")

        self.assertEqual(limited, "one\ntwo\n# ... 2 more diff lines omitted")

    def test_packet_can_limit_combined_diff_lines(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)
            (repo / "README.md").write_text("initial\none\ntwo\nthree\nfour\n", encoding="utf-8")

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                max_untracked_lines=10,
                max_diff_lines=6,
            )

            self.assertIn("# ...", packet)
            self.assertIn("more diff lines omitted", packet)
            self.assertNotIn("+four", packet)

    def test_task_contract_section_summarizes_limited_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            contract = pathlib.Path(raw) / "AGENT_TASK.md"
            contract.write_text(valid_task_contract(), encoding="utf-8")

            section = task_contract_section(contract, max_lines=8)

            self.assertIn("## Task Contract", section)
            self.assertIn(f"Source: `{contract}`", section)
            self.assertIn("Status: `pass`", section)
            self.assertIn("Required sections: `8/8`", section)
            self.assertIn("Missing sections: none", section)
            self.assertIn("Placeholder markers: none", section)
            self.assertIn("# Agent Task", section)
            self.assertIn("# ...", section)
            self.assertIn("more task contract lines omitted", section)

    def test_task_contract_section_warns_on_missing_sections_and_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            contract = pathlib.Path(raw) / "TASK_CONTRACT.md"
            contract.write_text(
                """# Agent Task

## Objective

TODO

## Context

placeholder
""",
                encoding="utf-8",
            )

            section = task_contract_section(contract, max_lines=40)

            self.assertIn("Status: `warn`", section)
            self.assertIn("Required sections: `2/8`", section)
            self.assertIn("Acceptance Criteria", section)
            self.assertIn("Expected Changes", section)
            self.assertIn("Placeholder markers: Objective, Context", section)

    def test_packet_auto_includes_repo_task_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)
            (repo / "AGENT_TASK.md").write_text(valid_task_contract(), encoding="utf-8")
            (repo / "README.md").write_text("changed\n", encoding="utf-8")

            packet = build_packet(repo, base=None, staged=False, max_lines=20)

            self.assertIn("## Task Contract", packet)
            self.assertIn("Source: `" + str(repo / "AGENT_TASK.md") + "`", packet)
            self.assertIn("Status: `pass`", packet)
            self.assertIn("## Diff", packet)

    def test_packet_can_include_explicit_task_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            contract = pathlib.Path(raw) / "task.md"
            contract.write_text(valid_task_contract(), encoding="utf-8")

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                task_contract=contract,
                max_task_contract_lines=12,
            )

            self.assertIn("## Task Contract", packet)
            self.assertIn(f"Source: `{contract}`", packet)
            self.assertIn("Status: `pass`", packet)
            self.assertIn("# ...", packet)

    def test_verification_checklist_section_includes_limited_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            checklist = pathlib.Path(raw) / "checklist.md"
            checklist.write_text("# Verification Checklist\n\n## Python\n\n- run tests\n- inspect docs\n", encoding="utf-8")

            section = verification_checklist_section(checklist, max_lines=4)

            self.assertIn("## Verification Checklist", section)
            self.assertIn(f"Source: `{checklist}`", section)
            self.assertIn("# Verification Checklist", section)
            self.assertIn("# ... 2 more verification checklist lines omitted", section)
            self.assertNotIn("- inspect docs", section)

    def test_verification_checklist_section_summarizes_verify_by_change_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            checklist = pathlib.Path(raw) / "checklist.json"
            checklist.write_text(
                """{
  "schema_version": "verify-by-change.v1",
  "source": {
    "type": "review_packet",
    "review_packet": "/tmp/review-packet.md"
  },
  "changed_files": ["README.md", "verify_by_change.py"],
  "empty": false,
  "task_contract": {
    "source": "/tmp/AGENT_TASK.md",
    "status": "pass",
    "required_sections": "8/8",
    "missing_sections": [],
    "placeholder_markers": []
  },
  "categories": {
    "docs": {
      "files": ["README.md"],
      "commands": ["Review rendered Markdown and verify links if public-facing."]
    },
    "python": {
      "files": ["verify_by_change.py"],
      "commands": ["Run `python3 -m py_compile` on changed Python files."]
    }
  }
}
""",
                encoding="utf-8",
            )

            section = verification_checklist_section(checklist, max_lines=40)

            self.assertIn("Envelope: `verify-by-change.v1`", section)
            self.assertIn("Verification source: `review_packet, review_packet=/tmp/review-packet.md`", section)
            self.assertIn("Task contract: `pass` (8/8 required sections)", section)
            self.assertIn("Task contract source: `/tmp/AGENT_TASK.md`", section)
            self.assertIn("Changed files:", section)
            self.assertIn("- `verify_by_change.py`", section)
            self.assertIn("## Python", section)
            self.assertIn("Run `python3 -m py_compile`", section)
            self.assertNotIn('"schema_version"', section)

    def test_verification_envelope_section_summarizes_task_contract_gaps(self) -> None:
        section = verification_envelope_section(
            "/tmp/verification-envelope.json",
            {
                "schema_version": "verify-by-change.v1",
                "source": {"type": "review_packet"},
                "changed_files": ["README.md"],
                "empty": False,
                "task_contract": {
                    "source": "/tmp/AGENT_TASK.md",
                    "status": "warn",
                    "required_sections": "6/8",
                    "missing_sections": ["Risks", "Out of Scope"],
                    "placeholder_markers": ["Objective"],
                },
                "categories": {
                    "docs": {
                        "files": ["README.md"],
                        "commands": ["Review rendered Markdown."],
                    },
                },
            },
            max_lines=40,
        )

        self.assertIn("Task contract: `warn` (6/8 required sections)", section)
        self.assertIn("Missing task sections: Risks, Out of Scope", section)
        self.assertIn("Task contract placeholders: Objective", section)
        self.assertIn("## Docs", section)

    def test_verification_envelope_markdown_handles_empty_envelope(self) -> None:
        markdown = verification_envelope_markdown({
            "schema_version": "verify-by-change.v1",
            "source": {"type": "git"},
            "changed_files": [],
            "empty": True,
            "categories": {},
        })

        self.assertIn("No changed files detected.", markdown)
        self.assertIn("Confirm the target ref", markdown)

    def test_parse_verification_envelope_ignores_other_json(self) -> None:
        self.assertIsNone(parse_verification_envelope('{"schema_version":"other"}'))
        self.assertIsNone(parse_verification_envelope('["not", "an", "envelope"]'))

    def test_packet_can_include_external_verification_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            checklist = pathlib.Path(raw) / "verification.md"
            checklist.write_text("## Docs\n\n- Review rendered Markdown.\n", encoding="utf-8")

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                verification_checklist=checklist,
            )

            self.assertIn("## Verification Checklist", packet)
            self.assertIn("## Docs", packet)
            self.assertIn("- Review rendered Markdown.", packet)

    def test_packet_can_include_verify_by_change_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            checklist = pathlib.Path(raw) / "verification-envelope.json"
            checklist.write_text(
                """{
  "schema_version": "verify-by-change.v1",
  "source": {"type": "git", "repo": "/tmp/repo"},
  "changed_files": ["README.md"],
  "empty": false,
  "categories": {
    "docs": {
      "files": ["README.md"],
      "commands": ["Review rendered Markdown and verify links if public-facing."]
    }
  }
}
""",
                encoding="utf-8",
            )

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                verification_checklist=checklist,
            )

            self.assertIn("Envelope: `verify-by-change.v1`", packet)
            self.assertIn("Verification source: `git, repo=/tmp/repo`", packet)
            self.assertIn("Changed files:", packet)
            self.assertIn("Review rendered Markdown", packet)

    def test_packet_can_generate_verification_checklist_with_verify_by_change_script(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text(
                "import sys\n"
                "print('# Verification Checklist')\n"
                "print('')\n"
                "print('## Generated')\n"
                "print('')\n"
                "print('- args: ' + ' '.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                verify_by_change=str(script),
            )

            self.assertIn("## Verification Checklist", packet)
            self.assertIn(f"Source: `verify-by-change: {script}`", packet)
            self.assertIn("## Generated", packet)
            self.assertIn("--repo", packet)
            self.assertIn("--json-envelope", packet)
            self.assertIn(str(repo), packet)

    def test_generated_verification_checklist_renders_verify_by_change_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text(
                "import json, sys\n"
                "repo = sys.argv[sys.argv.index('--repo') + 1]\n"
                "print(json.dumps({\n"
                "  'schema_version': 'verify-by-change.v1',\n"
                "  'source': {'type': 'git', 'repo': repo},\n"
                "  'changed_files': ['README.md'],\n"
                "  'empty': False,\n"
                "  'categories': {\n"
                "    'docs': {\n"
                "      'files': ['README.md'],\n"
                "      'commands': ['Review rendered Markdown and verify links if public-facing.']\n"
                "    }\n"
                "  }\n"
                "}))\n",
                encoding="utf-8",
            )

            section = generated_verification_checklist_section(
                str(script),
                repo,
                base=None,
                staged=False,
                max_lines=40,
            )

            self.assertIn("Envelope: `verify-by-change.v1`", section)
            self.assertIn(f"Verification source: `git, repo={repo}`", section)
            self.assertIn("Changed files:", section)
            self.assertIn("## Docs", section)
            self.assertIn("Review rendered Markdown", section)
            self.assertNotIn('"schema_version"', section)

    def test_generated_verification_checklist_forwards_staged_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            run("git", "add", "README.md", cwd=repo)
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text(
                "import sys\n"
                "print('# Verification Checklist')\n"
                "print('- args: ' + ' '.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )

            section = generated_verification_checklist_section(
                str(script),
                repo,
                base=None,
                staged=True,
                max_lines=20,
            )

            self.assertIn("--staged", section)

    def test_generated_verification_checklist_falls_back_for_older_verify_by_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text(
                "import sys\n"
                "if '--json-envelope' in sys.argv:\n"
                "    print('error: unrecognized arguments: --json-envelope', file=sys.stderr)\n"
                "    raise SystemExit(2)\n"
                "print('# Verification Checklist')\n"
                "print('## Generated')\n"
                "print('- args: ' + ' '.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )

            section = generated_verification_checklist_section(
                str(script),
                repo,
                base=None,
                staged=False,
                max_lines=20,
            )

            self.assertIn("## Generated", section)
            self.assertIn("--repo", section)
            self.assertNotIn("--json-envelope", section)

    def test_readiness_report_section_summarizes_repo_flightcheck_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "repo-flightcheck.json"
            report.write_text(
                """{
  "stack": "python",
  "summary": {
    "score": 84,
    "pointsPossible": 100,
    "passed": 10,
    "warnings": 2,
    "failed": 1,
    "criticalFailures": 1
  },
  "checks": [
    {"title": "README guidance", "status": "pass", "message": "README exists."},
    {"title": "Verification command", "status": "fail", "message": "No reliable verification command detected."},
    {"title": "CI workflow", "status": "warn", "message": "No GitHub Actions workflow detected."}
  ],
  "nextFixes": [
    "Verification command: expose one obvious test command.",
    "CI workflow: add a small workflow."
  ]
}
""",
                encoding="utf-8",
            )

            section = readiness_report_section(report, max_checks=1)

            self.assertIn("## Repo Readiness", section)
            self.assertIn("Score: `84/100`", section)
            self.assertIn("Stack: `python`", section)
            self.assertIn("`FAIL` Verification command", section)
            self.assertIn("1 more readiness checks omitted", section)
            self.assertIn("Verification command: expose one obvious test command.", section)

    def test_readiness_report_section_notes_clean_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "repo-flightcheck.json"
            report.write_text(
                """{
  "stack": "node",
  "summary": {
    "score": 100,
    "pointsPossible": 100,
    "passed": 14,
    "warnings": 0,
    "failed": 0,
    "criticalFailures": 0
  },
  "checks": [
    {"title": "README guidance", "status": "pass", "message": "README exists."}
  ],
  "nextFixes": []
}
""",
                encoding="utf-8",
            )

            section = readiness_report_section(report, max_checks=8)

            self.assertIn("No warning or failed readiness checks.", section)
            self.assertNotIn("Attention checks:", section)

    def test_readiness_report_section_summarizes_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "repo-flightcheck-contract.json"
            report.write_text(
                """{
  "schemaVersion": "repo-flightcheck.agent-contract.v1",
  "stack": "node",
  "ready": false,
  "threshold": 80,
  "score": 96,
  "criticalFailures": 0,
  "requiredBeforeAgent": [
    {
      "title": "Working tree",
      "status": "warn",
      "message": "Working tree has changed paths."
    },
    {
      "title": "CI workflow",
      "status": "warn",
      "message": "No GitHub Actions workflow detected."
    }
  ],
  "recommendedBeforeAgent": [
    {
      "title": "License",
      "status": "warn",
      "message": "No license file found."
    }
  ],
  "nextFixes": [
    "Working tree: start from a clean Git state."
  ]
}
""",
                encoding="utf-8",
            )

            section = readiness_report_section(report, max_checks=1)

            self.assertIn("Contract: `repo-flightcheck.agent-contract.v1`", section)
            self.assertIn("Ready: `false`", section)
            self.assertIn("Score: `96/100`", section)
            self.assertIn("Threshold: `80`", section)
            self.assertIn("`2` required blockers, `1` recommendations", section)
            self.assertIn("Required before agent:", section)
            self.assertIn("`WARN` Working tree", section)
            self.assertIn("1 more readiness checks omitted", section)
            self.assertIn("Recommended before agent:", section)
            self.assertIn("`WARN` License", section)
            self.assertIn("Working tree: start from a clean Git state.", section)

    def test_readiness_report_section_notes_clean_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "repo-flightcheck-contract.json"
            report.write_text(
                """{
  "schemaVersion": "repo-flightcheck.agent-contract.v1",
  "stack": "node",
  "ready": true,
  "threshold": 80,
  "score": 100,
  "criticalFailures": 0,
  "requiredBeforeAgent": [],
  "recommendedBeforeAgent": [],
  "nextFixes": []
}
""",
                encoding="utf-8",
            )

            section = readiness_report_section(report, max_checks=8)

            self.assertIn("Ready: `true`", section)
            self.assertIn("No required blockers or recommendations.", section)
            self.assertNotIn("Required before agent:", section)

    def test_ci_run_section_summarizes_github_actions_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "ci-run.json"
            report.write_text(
                """{
  "id": 123,
  "name": "CI",
  "status": "completed",
  "conclusion": "success",
  "head_branch": "main",
  "head_sha": "abc123",
  "event": "push",
  "html_url": "https://github.com/example/repo/actions/runs/123"
}
""",
                encoding="utf-8",
            )

            section = ci_run_section(report)

            self.assertIn("## CI Evidence", section)
            self.assertIn(f"Source: `{report}`", section)
            self.assertIn("Run: `123`", section)
            self.assertIn("Workflow: `CI`", section)
            self.assertIn("Status: `completed`", section)
            self.assertIn("Conclusion: `success`", section)
            self.assertIn("Branch: `main`", section)
            self.assertIn("SHA: `abc123`", section)
            self.assertIn("URL: <https://github.com/example/repo/actions/runs/123>", section)

    def test_ci_run_section_accepts_workflow_runs_list_payload(self) -> None:
        payload = normalize_ci_run_payload({
            "workflow_runs": [
                {
                    "id": 456,
                    "name": "build",
                    "status": "in_progress",
                    "conclusion": None,
                }
            ]
        })

        self.assertEqual(payload["id"], "456")
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(payload["conclusion"], "null")

    def test_published_head_section_summarizes_dedicated_proof_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "published-head.json"
            report.write_text(
                """{
  "schema_version": "published-head.v1",
  "status": "pass",
  "message": "Local HEAD is published.",
  "remote": "https://token@example.com/example/repo.git",
  "branch": "main",
  "local_head": "abc123",
  "remote_head": "abc123",
  "commit_url": "https://github.com/example/repo/commit/abc123",
  "ci_url": "https://github.com/example/repo/actions/runs/123",
  "evidence": ["https://token@example.com/example/repo.git", "origin/main: abc123"]
}
""",
                encoding="utf-8",
            )

            section = published_head_section(report)

            self.assertIn("## Published HEAD", section)
            self.assertIn(f"Source: `{report}`", section)
            self.assertIn("Status: `pass`", section)
            self.assertIn("Schema: `published-head.v1`", section)
            self.assertIn("Remote: `https://example.com/example/repo.git`", section)
            self.assertIn("Branch: `main`", section)
            self.assertIn("Local HEAD: `abc123`", section)
            self.assertIn("Remote HEAD: `abc123`", section)
            self.assertIn("Commit URL: <https://github.com/example/repo/commit/abc123>", section)
            self.assertIn("CI URL: <https://github.com/example/repo/actions/runs/123>", section)
            self.assertIn("- `origin/main: abc123`", section)
            self.assertNotIn("token@", section)

    def test_published_head_section_extracts_repo_flightcheck_git_remote_check(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / "repo-flightcheck.json"
            report.write_text(
                """{
  "summary": {"score": 98},
  "checks": [
    {
      "id": "git-remote",
      "title": "Git remote",
      "status": "warn",
      "message": "Origin remote is reachable, but local HEAD is not published on origin/main.",
      "evidence": [
        "git@github.com:example/repo.git",
        "local HEAD: abc123",
        "origin/main: def456"
      ]
    }
  ]
}
""",
                encoding="utf-8",
            )

            section = published_head_section(report)

            self.assertIn("## Published HEAD", section)
            self.assertIn("Status: `warn`", section)
            self.assertIn("Schema: `repo-flightcheck`", section)
            self.assertIn("local HEAD is not published", section)
            self.assertIn("- `local HEAD: abc123`", section)
            self.assertIn("- `origin/main: def456`", section)

    def test_packet_can_include_repo_readiness_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            report = pathlib.Path(raw) / "readiness.json"
            report.write_text(
                """{
  "stack": "node",
  "summary": {
    "score": 100,
    "pointsPossible": 100,
    "passed": 14,
    "warnings": 0,
    "failed": 0,
    "criticalFailures": 0
  },
  "checks": [],
  "nextFixes": []
}
""",
                encoding="utf-8",
            )

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                readiness_report=report,
            )

            self.assertIn("## Repo Readiness", packet)
            self.assertIn("Score: `100/100`", packet)
            self.assertIn("## Diff", packet)

    def test_packet_can_include_ci_run_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            ci_run = pathlib.Path(raw) / "ci-run.json"
            ci_run.write_text(
                """{
  "id": 123,
  "name": "CI",
  "status": "completed",
  "conclusion": "success",
  "head_sha": "abc123"
}
""",
                encoding="utf-8",
            )

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                ci_run=ci_run,
            )

            self.assertIn("## CI Evidence", packet)
            self.assertIn("Run: `123`", packet)
            self.assertIn("Conclusion: `success`", packet)
            self.assertIn("SHA: `abc123`", packet)
            self.assertIn("## Diff", packet)

    def test_packet_can_include_published_head_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            proof = pathlib.Path(raw) / "published-head.json"
            proof.write_text(
                """{
  "schema_version": "published-head.v1",
  "published": true,
  "message": "Origin remote is reachable and local HEAD is published on origin/main.",
  "branch": "main",
  "local_head": "abc123",
  "remote_head": "abc123"
}
""",
                encoding="utf-8",
            )

            packet = build_packet(
                repo,
                base=None,
                staged=False,
                max_lines=20,
                published_head=proof,
            )

            self.assertIn("## Published HEAD", packet)
            self.assertIn("Status: `pass`", packet)
            self.assertIn("local HEAD is published", packet)
            self.assertIn("## Diff", packet)

    def test_cli_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            checklist = pathlib.Path(raw) / "verification.md"
            checklist.write_text("## Docs\n\n- Review rendered Markdown.\n", encoding="utf-8")
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--verification-checklist",
                    str(checklist),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("# Review Packet", content)
            self.assertIn("Review rendered Markdown", content)

    def test_cli_writes_packet_with_json_envelope_verification_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            checklist = pathlib.Path(raw) / "checks.json"
            checklist.write_text(
                """{
  "schema_version": "verify-by-change.v1",
  "source": {"type": "explicit_paths"},
  "changed_files": ["README.md"],
  "empty": false,
  "categories": {
    "docs": {
      "files": ["README.md"],
      "commands": ["Review rendered Markdown and verify links if public-facing."]
    }
  }
}
""",
                encoding="utf-8",
            )
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--verification-checklist",
                    str(checklist),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("Envelope: `verify-by-change.v1`", content)
            self.assertIn("Verification source: `explicit_paths`", content)

    def test_cli_writes_packet_with_generated_verify_by_change_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text(
                "import sys\n"
                "print('# Verification Checklist')\n"
                "print('## Generated')\n"
                "print('- args: ' + ' '.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--verify-by-change",
                    str(script),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("verify-by-change:", content)
            self.assertIn("## Generated", content)

    def test_cli_rejects_two_verification_sources(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            checklist = pathlib.Path(raw) / "verification.md"
            checklist.write_text("## Docs\n", encoding="utf-8")
            script = pathlib.Path(raw) / "verify_by_change.py"
            script.write_text("print('# Verification Checklist')\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--verification-checklist",
                    str(checklist),
                    "--verify-by-change",
                    str(script),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Use either --verification-checklist or --verify-by-change", result.stderr)

    def test_cli_writes_packet_with_readiness_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            report = pathlib.Path(raw) / "readiness.json"
            report.write_text(
                """{
  "stack": "node",
  "summary": {
    "score": 100,
    "pointsPossible": 100,
    "passed": 14,
    "warnings": 0,
    "failed": 0,
    "criticalFailures": 0
  },
  "checks": [],
  "nextFixes": []
}
""",
                encoding="utf-8",
            )
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--readiness-report",
                    str(report),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("## Repo Readiness", content)
            self.assertIn("Score: `100/100`", content)

    def test_cli_writes_packet_with_ci_run_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            ci_run = pathlib.Path(raw) / "ci-run.json"
            ci_run.write_text(
                """{
  "id": 123,
  "name": "CI",
  "status": "completed",
  "conclusion": "success",
  "html_url": "https://github.com/example/repo/actions/runs/123"
}
""",
                encoding="utf-8",
            )
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--ci-run",
                    str(ci_run),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("## CI Evidence", content)
            self.assertIn("Conclusion: `success`", content)
            self.assertIn("URL: <https://github.com/example/repo/actions/runs/123>", content)

    def test_cli_writes_packet_with_published_head_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            proof = pathlib.Path(raw) / "published-head.json"
            proof.write_text(
                """{
  "schema_version": "published-head.v1",
  "status": "warn",
  "message": "Origin remote is reachable, but local HEAD is not published on origin/main.",
  "branch": "main",
  "local_head": "abc123",
  "remote_head": "def456"
}
""",
                encoding="utf-8",
            )
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--published-head",
                    str(proof),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("## Published HEAD", content)
            self.assertIn("Status: `warn`", content)
            self.assertIn("Remote HEAD: `def456`", content)

    def test_cli_writes_packet_with_explicit_task_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            contract = pathlib.Path(raw) / "task.md"
            contract.write_text(valid_task_contract(), encoding="utf-8")
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--task-contract",
                    str(contract),
                    "--task-contract-lines",
                    "12",
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            content = out.read_text(encoding="utf-8")
            self.assertIn("## Task Contract", content)
            self.assertIn(f"Source: `{contract.resolve()}`", content)
            self.assertIn("Required sections: `8/8`", content)


if __name__ == "__main__":
    unittest.main()
