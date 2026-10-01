import ast
import io
import pathlib
import unittest
from unittest import mock

import relay


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PromptTests(unittest.TestCase):
    def test_current_input_is_capped(self):
        with mock.patch.object(relay, "MAX_CONTEXT_CHARS", 1024):
            prompt = relay.build_prompt([], "x" * 5000)
        self.assertLessEqual(len(prompt), 1024)
        self.assertIn("[input truncated]", prompt)

    def test_large_current_input_is_not_displaced_by_history_notice(self):
        history = [{"human": "old", "responses": {"claude": "old answer"}}]
        with mock.patch.object(relay, "MAX_CONTEXT_CHARS", 1024):
            prompt = relay.build_prompt(history, "x" * 5000)
        self.assertEqual(len(prompt), 1024)
        self.assertTrue(prompt.endswith("[input truncated]"))

    def test_history_keeps_complete_rounds(self):
        history = [
            {
                "human": "question",
                "responses": {"claude": "answer", "codex": "review"},
            }
        ]
        with mock.patch.object(relay, "MAX_CONTEXT_CHARS", 4096):
            prompt = relay.build_prompt(history, "next")
        self.assertIn("[human] question", prompt)
        self.assertIn("[claude] answer", prompt)
        self.assertTrue(prompt.endswith("[human] next"))

    def test_only_selected_executor_gets_full_mode(self):
        with (
            mock.patch.object(relay, "RELAY_MODE", "full"),
            mock.patch.object(relay, "RELAY_EXECUTOR", "gemini"),
        ):
            self.assertEqual(relay._non_interactive_env("gemini")["RELAY_AGENT_MODE"], "full")
            self.assertEqual(relay._non_interactive_env("codex")["RELAY_AGENT_MODE"], "readonly")

    def test_errors_are_excluded_from_future_history(self):
        responses = {"claude": "good", "codex": "[error: timeout]", "gemini": "also good"}
        diagnostics = {
            "claude": {"status": "ok"},
            "codex": {"status": "timeout"},
            "gemini": {"status": "ok"},
        }
        self.assertEqual(
            relay.successful_responses(responses, diagnostics),
            {"claude": "good", "gemini": "also good"},
        )


class CompatibilityTests(unittest.TestCase):
    def test_all_python_files_parse_as_python_310(self):
        for path in ROOT.glob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 10),
                )

    def test_redirected_windows_input_uses_standard_input(self):
        redirected = io.StringIO("exit\n")
        with (
            mock.patch.object(relay.sys, "platform", "win32"),
            mock.patch.object(relay.sys, "stdin", redirected),
        ):
            self.assertEqual(relay._read_input(""), "exit")


if __name__ == "__main__":
    unittest.main()
