from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codex_review_packet import build_packet, changed_files, parse_status_paths, untracked_file_diff  # noqa: E402


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

    def test_cli_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw) / "repo"
            repo.mkdir()
            init_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            out = pathlib.Path(raw) / "packet.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "codex_review_packet.py"),
                    "--repo",
                    str(repo),
                    "--output",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Wrote review packet", result.stdout)
            self.assertIn("# Review Packet", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
