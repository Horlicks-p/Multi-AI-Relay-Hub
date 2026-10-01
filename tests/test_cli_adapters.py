import os
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import cli_common
import run_claude_cli
import run_codex_cli
import run_gemini_cli


def completed(stdout="answer"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class AdapterTests(unittest.TestCase):
    def test_claude_uses_stdin_and_appends_system_prompt(self):
        with (
            mock.patch.object(run_claude_cli, "find_cli_args", return_value=["claude"]),
            mock.patch.object(run_claude_cli.subprocess, "run", return_value=completed()) as run,
            mock.patch.object(run_claude_cli, "RELAY_MODE", "readonly"),
        ):
            self.assertEqual(run_claude_cli.call_claude("line 1\nline 2"), "answer")
        args = run.call_args.args[0]
        self.assertNotIn("line 1\nline 2", args)
        self.assertEqual(run.call_args.kwargs["input"], "line 1\nline 2")
        self.assertIn("--append-system-prompt", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "plan")

    def test_codex_uses_full_stdin_prompt_and_explicit_sandbox(self):
        with (
            mock.patch.object(run_codex_cli, "find_cli_args", return_value=["codex"]),
            mock.patch.object(run_codex_cli.subprocess, "run", return_value=completed()) as run,
            mock.patch.object(run_codex_cli, "RELAY_MODE", "full"),
        ):
            self.assertEqual(run_codex_cli.call_codex("review this"), "answer")
        args = run.call_args.args[0]
        self.assertEqual(args[-1], "-")
        self.assertIn("--ephemeral", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertIn("review this", run.call_args.kwargs["input"])

    def test_gemini_uses_stdin_and_plan_mode(self):
        with (
            mock.patch.object(run_gemini_cli, "find_cli_args", return_value=["gemini"]),
            mock.patch.object(run_gemini_cli.subprocess, "run", return_value=completed()) as run,
            mock.patch.object(run_gemini_cli, "RELAY_MODE", "readonly"),
        ):
            self.assertEqual(run_gemini_cli.call_gemini("review this"), "answer")
        args = run.call_args.args[0]
        self.assertNotIn("review this", args)
        self.assertEqual(args[args.index("--approval-mode") + 1], "plan")
        self.assertIn("review this", run.call_args.kwargs["input"])

    def test_cli_errors_are_not_returned_as_answers(self):
        failure = SimpleNamespace(returncode=7, stdout="", stderr="bad option")
        with (
            mock.patch.object(run_gemini_cli, "find_cli_args", return_value=["gemini"]),
            mock.patch.object(run_gemini_cli.subprocess, "run", return_value=failure),
        ):
            with self.assertRaisesRegex(RuntimeError, "exited with 7"):
                run_gemini_cli.call_gemini("hello")


class WindowsShimTests(unittest.TestCase):
    def test_parses_modern_percent_dp0_shim_without_executing_cmd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            entry = root / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("// test", encoding="utf-8")
            shim = root / "gemini.cmd"
            shim.write_text(
                '"%_prog%"  "%dp0%\\node_modules\\@google\\gemini-cli\\bundle\\gemini.js" %*',
                encoding="utf-8",
            )
            resolved = cli_common._parse_windows_npm_shim(str(shim))
        self.assertIsNotNone(resolved)
        self.assertEqual(pathlib.Path(resolved[-1]), entry)
        self.assertNotEqual(pathlib.Path(resolved[0]).suffix.lower(), ".cmd")


if __name__ == "__main__":
    unittest.main()
