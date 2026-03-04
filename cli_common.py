"""
cli_common.py — Shared utilities for AI CLI wrapper scripts.

Provides safe CLI resolution on Windows that avoids:
  1. shutil.which() picking up stray .bat/.cmd files in the current directory
  2. Executing .cmd files that pop up unwanted cmd.exe windows
"""

import os
import sys
import shutil
import subprocess


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

def find_cli_args(cli_name: str, js_candidates: list[str]) -> list[str]:
    """
    Resolve the correct command-line args to invoke a Node.js-based CLI tool.

    On Windows:
      0. Check CLAUDE_CLI_PATH environment variable (if cli_name is "claude")
      1. Try to find the cli.js via known paths under npm global dir → [node, cli.js]
      2. Try to parse the .cmd file to extract the js path → [node, cli.js]
      3. Fallback to the full-path .cmd file (not cwd-relative)
    On Linux/macOS:
      - Simply use shutil.which()

    Args:
        cli_name:      The CLI command name (e.g. "claude", "codex", "gemini")
        js_candidates: List of relative paths under npm_dir to try for cli.js
                       (e.g. ["node_modules/@anthropic-ai/claude-code/cli.js"])
    """
    # ── 0. Manual Override ──
    if cli_name == "claude":
        env_path = os.environ.get("CLAUDE_CLI_PATH")
        if env_path and os.path.exists(env_path):
            if env_path.endswith(".js"):
                node_path = shutil.which("node") or "node"
                return [node_path, env_path]
            return [env_path]

    if sys.platform != "win32":
        cmd = shutil.which(cli_name) or cli_name
        return [cmd]

    npm_dir = find_npm_global_dir()
    node_path = shutil.which("node") or "node"

    # ── 1. Try known js_candidates ──
    if npm_dir:
        for rel_path in js_candidates:
            full_path = os.path.join(npm_dir, rel_path)
            if os.path.exists(full_path):
                print(f"[{cli_name} wrapper] Using node + cli.js: {full_path}", file=sys.stderr)
                return [node_path, full_path]

    # ── 2. Try parsing the .cmd file ──
    # Look for .cmd in npm global dir OR in PATH
    cmd_locations = []
    if npm_dir:
        cmd_locations.append(os.path.join(npm_dir, f"{cli_name}.cmd"))
    
    # Also check where it actually is (shutil.which)
    which_cmd = shutil.which(cli_name)
    if which_cmd and which_cmd.lower().endswith((".cmd", ".bat")):
        cmd_locations.append(which_cmd)

    for cmd_file in cmd_locations:
        if os.path.exists(cmd_file):
            try:
                with open(cmd_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip().strip('"')
                        # Extract JS path from line like: node "%~dp0\node_modules\..." %*
                        if ".js" in line and cli_name in line.lower():
                            # Resolve %~dp0 to the cmd file's directory
                            base_dir = os.path.dirname(cmd_file)
                            js_path = line.replace("%~dp0", base_dir + os.sep)
                            parts = js_path.split('"')
                            for part in parts:
                                part = part.strip()
                                # Clean up potential extra flags or arguments
                                if " " in part and not os.path.exists(part):
                                    part = part.split(" ")[0]
                                if part.endswith(".js") and os.path.exists(part):
                                    print(f"[{cli_name} wrapper] Parsed from .cmd: {part}", file=sys.stderr)
                                    return [node_path, part]
            except Exception:
                pass

    # ── 3. Fallback: full-path .cmd or .exe ──
    for cmd_file in cmd_locations:
        if os.path.exists(cmd_file):
            # Special case for Claude: we MUST use JS version if possible to avoid popup windows or broken shims
            if cli_name == "claude" and not cmd_file.endswith(".js"):
                # If we're here, we found a .cmd/.exe but failed to parse a .js out of it.
                # Let's try one last place: sibling node_modules
                base_dir = os.path.dirname(cmd_file)
                js_test = os.path.join(base_dir, "node_modules", "@anthropic-ai", "claude-code", "cli.js")
                if os.path.exists(js_test):
                    print(f"[{cli_name} wrapper] Found sibling JS: {js_test}", file=sys.stderr)
                    return [node_path, js_test]
                
                # If still not found, and it's Claude, warn loudly but allow fallback IF it's a .js
                # (Actually, we've already tried js_candidates)
                print(f"[{cli_name} wrapper] WARNING: Failed to find JS version of Claude. "
                      f"Using {cmd_file} may cause popup windows.", file=sys.stderr)

            print(f"[{cli_name} wrapper] Fallback to full-path: {cmd_file}", file=sys.stderr)
            return [cmd_file]

    # Last resort: just the name (rely on PATH)
    if not which_cmd:
        which_cmd = cli_name
    
    if cli_name == "claude" and not which_cmd.endswith(".js"):
         print(f"[{cli_name} wrapper] CRITICAL: Could not find JS path for Claude. "
               "The relay will attempt to use the system command, but this may fail or pop up windows.", file=sys.stderr)

    print(f"[{cli_name} wrapper] WARNING: using {which_cmd}", file=sys.stderr)
    return [which_cmd]
