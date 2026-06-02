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
    limit_lines,
    parse_status_paths,
    readiness_report_section,
    review_lane_for_path,
    review_map_section,
    untracked_file_diff,
    verification_checklist_section,
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


if __name__ == "__main__":
    unittest.main()
