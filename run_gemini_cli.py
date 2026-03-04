#!/usr/bin/env python3
"""
run_gemini_cli.py
Wrapper script for Gemini CLI — reads from stdin, calls `gemini -p`, writes to stdout.
Used by relay.py as the "gemini" model subprocess.
"""

import sys
import subprocess
import io
import os
import json

from cli_common import find_cli_args, get_project_cwd

sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='ignore', write_through=True)

SYSTEM_PROMPT = (
    "Your identity is [gemini]. You are participating in a multi-AI chat room. "
    "Messages you receive are prefixed with [speaker_name] tags "
    "(e.g. [human], [claude], [codex], [gemini]). "
    "Please respond directly to the conversation based on the content. "
    "When responding, do NOT prefix your own name. The relay system handles that automatically. "
    "Do NOT respond to messages intended for other AIs (e.g. [claude], [codex]). "
    "Do NOT roleplay as other speakers. Speak only as [gemini]. "
    "You MAY use tools when the human asks you to. "
    "Do NOT use tools unprompted -- only when explicitly requested or clearly needed to answer. "
    "IMPORTANT: Do NOT narrate your internal steps or tool usage. "
    "Do NOT say things like 'Let me read file X' or 'I will now check Y'. "
    "Skip all process narration and give the result directly. "
    "Be concise: provide conclusions, analysis, and actionable answers without filler."
)

CLI_JS_CANDIDATES = [
    os.path.join("node_modules", "@google", "gemini-cli", "dist", "index.js"),
    os.path.join("node_modules", "@google", "gemini-cli", "bin", "gemini.js"),
    os.path.join("node_modules", "gemini-cli", "bin", "gemini.js"),
    os.path.join("node_modules", "@google", "gemini-cli", "dist", "cli.js"),
]

try:
    RELAY_TIMEOUT_SEC = int(os.environ.get("RELAY_TIMEOUT_SEC", 600))
except (ValueError, TypeError):
    RELAY_TIMEOUT_SEC = 600

RELAY_MODE = os.environ.get("RELAY_MODE", "readonly").lower().strip()


def call_gemini(message: str) -> str:
    try:
        # Prepend system prompt for identity
        full_message = f"{SYSTEM_PROMPT}\n\n{message}"
        safe_message = full_message.replace("\n", " \\n ")

        args = find_cli_args("gemini", CLI_JS_CANDIDATES)
        args.extend([
            "-p", safe_message,
            "-o", "text",
        ])
        if RELAY_MODE == "full":
            args.append("--yolo")  # ★ full 模式才自動批准所有工具操作

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=RELAY_TIMEOUT_SEC,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
            cwd=get_project_cwd(),
            stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"[gemini wrapper] Timeout after {RELAY_TIMEOUT_SEC}s", file=sys.stderr)
        return ""
    except FileNotFoundError:
        print("[gemini wrapper] gemini CLI not found", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[gemini wrapper error] {e}", file=sys.stderr)
        return ""


def main():
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
            response = call_gemini(full_msg)
            if response:
                print(response, flush=True)
                print("END_OF_RESPONSE", flush=True)
        except json.JSONDecodeError:
            response = call_gemini(line)
            if response:
                print(response, flush=True)
                print("END_OF_RESPONSE", flush=True)
        except Exception as e:
            print(f"[gemini wrapper error] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
