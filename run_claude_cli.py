#!/usr/bin/env python3
"""
run_claude_cli.py
Wrapper script for Claude CLI — reads from stdin, calls `claude -p`, writes to stdout.
Used by relay.py as the "claude" model subprocess.
"""

import sys
import subprocess
import io
import os
import json

from cli_common import find_cli_args, get_project_cwd

SYSTEM_PROMPT = (
    "Your identity is [claude]. You are participating in a multi-AI chat room. "
    "Messages you receive are prefixed with [speaker_name] tags "
    "(e.g. [human], [claude], [codex], [gemini]). "
    "Please respond directly to the conversation based on the content. "
    "When responding, do NOT prefix your own name. The relay system handles that automatically. "
    "Do NOT respond to messages intended for other AIs (e.g. [codex], [gemini]). "
    "Do NOT roleplay as other speakers. Speak only as [claude]. "
    "You MAY use tools when the human asks you to. "
    "Do NOT use tools unprompted -- only when explicitly requested or clearly needed to answer. "
    "IMPORTANT: Do NOT narrate your internal steps or tool usage. "
    "Do NOT say things like 'Let me read file X' or 'I will now check Y'. "
    "Skip all process narration and give the result directly. "
    "Be concise: provide conclusions, analysis, and actionable answers without filler."
)

CLI_JS_CANDIDATES = [
    os.path.join("node_modules", "@anthropic-ai", "claude-code", "cli.js"),
    os.path.join("node_modules", "@anthropic-ai", "claude-code", "dist", "cli.js"),
    os.path.join("node_modules", "@anthropic-ai", "claude-code", "bin", "cli.js"),
]

try:
    RELAY_TIMEOUT_SEC = max(1, int(os.environ.get("RELAY_TIMEOUT_SEC", 600)))
except (ValueError, TypeError):
    RELAY_TIMEOUT_SEC = 600

RELAY_MODE = os.environ.get(
    "RELAY_AGENT_MODE", os.environ.get("RELAY_MODE", "readonly")
).lower().strip()


def _configure_stdio() -> None:
    if hasattr(sys.stdin, "buffer"):
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True
        )


def call_claude(message: str) -> str:
    args = find_cli_args("claude", CLI_JS_CANDIDATES)
    args.extend([
        "-p",
        "--append-system-prompt", SYSTEM_PROMPT,
        "--output-format", "text",
        "--no-session-persistence",
        "--permission-prompts", "none",
    ])
    if RELAY_MODE == "full":
        args.extend(["--permission-mode", "bypassPermissions"])
    else:
        args.extend(["--permission-mode", "plan"])

    result = subprocess.run(
        args,
        input=message,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # relay.py owns the earlier timeout so it can terminate the whole tree.
        timeout=RELAY_TIMEOUT_SEC + 5,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
        cwd=get_project_cwd(),
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no error output"
        raise RuntimeError(f"Claude CLI exited with {result.returncode}: {detail[:1000]}")
    if not result.stdout.strip():
        raise RuntimeError("Claude CLI returned an empty response")
    return result.stdout.strip()


def main():
    _configure_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg_obj = json.loads(line)
            payload = msg_obj.get("payload", "")
            sender = msg_obj.get("from", "unknown")
            if not payload:
                continue
            full_msg = f"[{sender}] {payload}"
            response = call_claude(full_msg)
            if response:
                print(response, flush=True)
                print("END_OF_RESPONSE", flush=True)
        except json.JSONDecodeError:
            response = call_claude(line)
            if response:
                print(response, flush=True)
                print("END_OF_RESPONSE", flush=True)
        except subprocess.TimeoutExpired:
            print(f"[claude wrapper] Timeout after {RELAY_TIMEOUT_SEC}s", file=sys.stderr)
            return 124
        except Exception as e:
            print(f"[claude wrapper error] {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
