"""
cli_common.py — Shared utilities for safe AI CLI wrapper scripts.

Provides safe CLI resolution on Windows that avoids:
  1. shutil.which() picking up stray .bat/.cmd files in the current directory
  2. Executing .cmd files that pop up unwanted cmd.exe windows
"""

import os
import re
import sys
import shutil
import subprocess
from pathlib import Path


WINDOWS_SHIM_SUFFIXES = {".bat", ".cmd"}


def get_project_cwd() -> str:
    """
    Return the working directory that AI CLIs should operate in.
    
    This is the directory the user cd'd into before running relay.py,
    so the AIs can read/modify the user's project files.
    
    We use the real process cwd (not the script's directory), because
    relay.py is designed to be run FROM the project directory:
        cd /my/project && python /path/to/cli/relay.py
    """
    return os.getcwd()


# ── npm global directory resolution ───────────────────────────────────────────

def find_npm_global_dir() -> str | None:
    """Find the npm global installation directory (e.g. C:\\Users\\X\\AppData\\Roaming\\npm)."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm_dir = os.path.join(appdata, "npm")
            if os.path.isdir(npm_dir):
                return npm_dir

    # Fallback: ask npm directly
    try:
        result = subprocess.run(
            ["npm", "prefix", "-g"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
            stdin=subprocess.DEVNULL,
        )
        prefix = result.stdout.strip()
        if prefix and os.path.isdir(prefix):
            return prefix
    except Exception:
        pass

    return None


# ── CLI argument resolution ───────────────────────────────────────────────────

def _node_command(js_path: str) -> list[str]:
    return [shutil.which("node") or "node", os.path.normpath(js_path)]


def _parse_windows_npm_shim(cmd_file: str) -> list[str] | None:
    """Resolve an npm Node entry point without executing its .cmd shim."""
    try:
        text = Path(cmd_file).read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return None

    base_dir = str(Path(cmd_file).resolve().parent)
    patterns = (
        r'"%~dp0([^"\r\n]+\.js)"',
        r'"%dp0%\\?([^"\r\n]+\.js)"',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        relative = match.group(1).lstrip("\\/").replace("\\", os.sep)
        candidate = os.path.join(base_dir, relative)
        if os.path.isfile(candidate):
            return _node_command(candidate)
    return None


def find_cli_args(cli_name: str, js_candidates: list[str]) -> list[str]:
    """Resolve a safe executable argv prefix for an AI CLI.

    Windows batch shims are parsed but never executed. This avoids cmd.exe
    metacharacter injection and its 8191-character command-line limit.
    """
    override_name = f"{cli_name.upper()}_CLI_PATH"
    override = os.environ.get(override_name)
    if override:
        override_path = os.path.abspath(os.path.expanduser(override))
        if not os.path.exists(override_path):
            raise FileNotFoundError(f"{override_name} does not exist: {override_path}")
        suffix = Path(override_path).suffix.lower()
        if suffix in WINDOWS_SHIM_SUFFIXES:
            parsed = _parse_windows_npm_shim(override_path)
            if parsed:
                return parsed
            raise RuntimeError(
                f"Refusing unsafe Windows shim from {override_name}; point it "
                "to a native executable or .js entry point"
            )
        return _node_command(override_path) if suffix == ".js" else [override_path]

    which_cmd = shutil.which(cli_name)
    if sys.platform != "win32":
        return [which_cmd or cli_name]

    # Native executables, including modern Claude Code, are safe to run directly.
    if which_cmd and Path(which_cmd).suffix.lower() not in WINDOWS_SHIM_SUFFIXES:
        return [which_cmd]

    npm_dir = find_npm_global_dir()
    if npm_dir:
        for relative in js_candidates:
            candidate = os.path.join(npm_dir, relative)
            if os.path.isfile(candidate):
                return _node_command(candidate)

    cmd_locations = []
    if npm_dir:
        cmd_locations.append(os.path.join(npm_dir, f"{cli_name}.cmd"))
    if which_cmd and Path(which_cmd).suffix.lower() in WINDOWS_SHIM_SUFFIXES:
        cmd_locations.append(which_cmd)

    for cmd_file in dict.fromkeys(cmd_locations):
        if os.path.isfile(cmd_file):
            parsed = _parse_windows_npm_shim(cmd_file)
            if parsed:
                return parsed

    raise FileNotFoundError(
        f"Could not find a safe {cli_name} executable. Install it or set "
        f"{override_name} to a native executable or JavaScript entry point."
    )
