#!/usr/bin/env python3
"""
relay.py -- Multi-AI Code Discussion Panel
Claude | Codex | Gemini

Flow:
  YOU type -> all three AIs answer in parallel (with full conversation history)
  YOU see all three answers -> YOU decide when to continue
  Empty Enter -> AIs react to each other's last round of responses
"""

import sys
import io
import subprocess
import shutil
import time
import os
import json
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── .env auto-load ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; fall back to shell-exported vars

# UTF-8 output on Windows
if sys.platform == "win32":
    # Try to set console to UTF-8
    try:
        subprocess.run(["chcp", "65001"], shell=True, capture_output=True)
    except Exception:
        pass

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="ignore", write_through=True)
sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="ignore")

# ─── Configuration ────────────────────────────────────────────────────────────

# Unified Timeout (Source of Truth)
try:
    TIMEOUT = int(os.environ.get("RELAY_TIMEOUT_SEC", 600))
except (ValueError, TypeError):
    TIMEOUT = 600

# Context Management (24k-32k as agreed)
try:
    MAX_CONTEXT_CHARS = int(os.environ.get("MAX_CONTEXT_CHARS", 32000))
except (ValueError, TypeError):
    MAX_CONTEXT_CHARS = 32000

# Relay Mode: "readonly" (default) or "full"
# readonly = strip bypass-permissions flags from all AI wrappers
# full     = enable bypass-permissions (AI can write/delete files)
RELAY_MODE = os.environ.get("RELAY_MODE", "readonly").lower().strip()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _non_interactive_env() -> dict:
    """Environment hints to keep CLIs in one-shot mode."""
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("TERM", "dumb")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")  # ★ Force UTF-8 for Python subprocesses
    env["RELAY_MODE"] = RELAY_MODE  # ★ Pass mode to wrapper subprocesses
    return env


def _run(args: list, input_data: str = None) -> dict:
    """Run a CLI wrapper and return structured result."""
    started = time.perf_counter()
    
    # Windows 專用的進程啟動標誌，確保完全隱藏視窗
    creationflags = 0
    if sys.platform == "win32":
        # 只使用 CREATE_NO_WINDOW (0x08000000) 
        # (絕對不可加 DETACHED_PROCESS，否則會切斷 stdin/stdout 管線導致 AI 失聰失語)
        creationflags = 0x08000000

    try:
        result = subprocess.run(
            args,
            input=input_data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=TIMEOUT,
            creationflags=creationflags,
            env=_non_interactive_env(),
        )
        output = result.stdout.strip()
        
        # 移除包裝腳本可能輸出的結束標記
        if output.endswith("END_OF_RESPONSE"):
            output = output[:-15].strip()
            
        stderr = result.stderr.strip()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        stderr_preview = stderr[:220]

        if result.returncode != 0:
            reason = stderr_preview if stderr else f"exit={result.returncode}"
            return {
                "status": "error",
                "text": output or f"[error exit={result.returncode}] {reason}",
                "reason": reason,
                "exit_code": result.returncode,
                "elapsed_ms": elapsed_ms,
                "stderr_preview": stderr_preview,
                "stdout_chars": len(output),
            }

        if not output:
            reason = "empty stdout"
            if stderr_preview:
                reason = f"empty stdout; stderr={stderr_preview}"
            return {
                "status": "no_response",
                "text": "(no response)",
                "reason": reason,
                "exit_code": 0,
                "elapsed_ms": elapsed_ms,
                "stderr_preview": stderr_preview,
                "stdout_chars": 0,
            }

        return {
            "status": "ok",
            "text": output,
            "reason": stderr_preview if stderr_preview else "ok",
            "exit_code": 0,
            "elapsed_ms": elapsed_ms,
            "stderr_preview": stderr_preview,
            "stdout_chars": len(output),
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "timeout",
            "text": f"[error: timeout after {TIMEOUT}s]",
            "reason": f"timeout after {TIMEOUT}s",
            "exit_code": None,
            "elapsed_ms": elapsed_ms,
            "stderr_preview": "",
            "stdout_chars": 0,
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "error",
            "text": f"[error: {e}]",
            "reason": str(e)[:220],
            "exit_code": None,
            "elapsed_ms": elapsed_ms,
            "stderr_preview": "",
            "stdout_chars": 0,
        }


def call_wrapper(name: str, prompt: str) -> dict:
    """使用包裝腳本執行請求。"""
    wrapper_map = {
        "claude": "run_claude_cli.py",
        "codex": "run_codex_cli.py",
        "gemini": "run_gemini_cli.py",
    }
    script = os.path.join(SCRIPT_DIR, wrapper_map[name])
    
    # 封裝成 JSON 格式發送給包裝腳本
    msg_obj = {
        "id": "relay-internal",
        "from": "human",
        "payload": prompt
    }
    input_json = json.dumps(msg_obj, ensure_ascii=False) + "\n"
    
    return _run([sys.executable, "-u", script], input_data=input_json)


CALLERS = {
    "claude": lambda p: call_wrapper("claude", p),
    "codex":  lambda p: call_wrapper("codex", p),
    "gemini": lambda p: call_wrapper("gemini", p),
}

# ─── History formatting ───────────────────────────────────────────────────────

def build_prompt(history: list, human_msg: str) -> str:
    """Build prompt with round-based truncation to keep boundaries intact."""
    if not history:
        return f"[human] {human_msg}"
    
    rounds = []
    current_chars = 0
    # Add rounds from newest to oldest until limit reached
    for turn in reversed(history):
        # Format this specific turn
        turn_parts = [f"[human] {turn['human']}"]
        for name in ["claude", "codex", "gemini"]:
            resp = turn["responses"].get(name, "")
            if resp:
                turn_parts.append(f"[{name}] {resp}")
        turn_text = "\n\n".join(turn_parts)
        
        # Check if adding this round exceeds the limit
        if current_chars + len(turn_text) + 2 > MAX_CONTEXT_CHARS:
            break
        
        rounds.insert(0, turn_text)
        current_chars += len(turn_text) + 2
    
    context = "\n\n".join(rounds)
    if len(history) > len(rounds):
        context = f"[history truncated; showing latest {len(rounds)} rounds]\n\n{context}"
    
    return f"{context}\n\n[human] {human_msg}" if context else f"[human] {human_msg}"

# ─── Ask all AIs in parallel ──────────────────────────────────────────────────

def ask_all(human_msg: str, history: list) -> tuple[dict, dict]:
    prompt = build_prompt(history, human_msg)

    responses = {}
    diagnostics = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn, prompt): name for name, fn in CALLERS.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                responses[name] = result.get("text", "(no response)")
                diagnostics[name] = {
                    "status": result.get("status", "error"),
                    "reason": result.get("reason", ""),
                    "elapsed_ms": result.get("elapsed_ms", 0),
                    "exit_code": result.get("exit_code"),
                    "stdout_chars": result.get("stdout_chars", 0),
                }
            except Exception as e:
                responses[name] = f"[error: {e}]"
                diagnostics[name] = {
                    "status": "error",
                    "reason": str(e)[:220],
                    "elapsed_ms": 0,
                    "exit_code": None,
                    "stdout_chars": 0,
                }
    return responses, diagnostics

# ─── ANSI Colors ─────────────────────────────────────────────────────────────

class C:
    """ANSI color codes."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    # AI identity colors
    CLAUDE  = "\033[38;5;141m"   # purple
    CODEX   = "\033[38;5;114m"   # green
    GEMINI  = "\033[38;5;81m"    # cyan
    # UI colors
    HUMAN   = "\033[38;5;220m"   # gold
    BANNER  = "\033[38;5;75m"    # sky blue
    OK      = "\033[38;5;114m"   # green
    WARN    = "\033[38;5;214m"   # orange
    ERR     = "\033[38;5;203m"   # red
    MUTED   = "\033[38;5;245m"   # gray
    LINE    = "\033[38;5;240m"   # dark gray

AI_COLORS = {"claude": C.CLAUDE, "codex": C.CODEX, "gemini": C.GEMINI}
AI_ICONS  = {"claude": "\u25c8", "codex": "\u25a0", "gemini": "\u25b2"}


def _enable_win_ansi():
    """Enable ANSI escape code processing on Windows 10+."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

_enable_win_ansi()

# ─── Custom Windows Input (no arrow-key history) ─────────────────────────────

def _read_input(prompt: str) -> str:
    """
    Custom input function.
    On Windows: uses msvcrt to capture keystrokes, ignoring arrow keys
    to prevent accidental history recall. Supports paste via Ctrl+V.
    On other OS: falls back to standard input().
    """
    if sys.platform != "win32":
        return input(prompt)

    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()

    chars = []
    while True:
        ch = msvcrt.getwch()

        if ch == '\r':          # Enter
            sys.stdout.write('\n')
            sys.stdout.flush()
            return ''.join(chars)

        elif ch == '\x03':      # Ctrl+C
            sys.stdout.write('\n')
            raise KeyboardInterrupt

        elif ch == '\x1a':      # Ctrl+Z (EOF on Windows)
            sys.stdout.write('\n')
            raise EOFError

        elif ch == '\x08':      # Backspace
            if chars:
                chars.pop()
                sys.stdout.write('\b \b')
                sys.stdout.flush()

        elif ch == '\x16':      # Ctrl+V (paste)
            try:
                import ctypes
                ctypes.windll.user32.OpenClipboard(0)
                handle = ctypes.windll.user32.GetClipboardData(13)  # CF_UNICODETEXT
                if handle:
                    text = ctypes.c_wchar_p(handle).value or ""
                    text = text.split('\n')[0].split('\r')[0]
                    chars.extend(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                ctypes.windll.user32.CloseClipboard()
            except Exception:
                pass

        elif ch in ('\x00', '\xe0'):  # Special key prefix (arrow, F-keys, etc.)
            msvcrt.getwch()           # Read & discard the second byte

        elif ch == '\x15':      # Ctrl+U: clear line
            while chars:
                chars.pop()
                sys.stdout.write('\b \b')
            sys.stdout.flush()

        elif ord(ch) >= 32:     # Normal printable character
            chars.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()

# ─── Display ──────────────────────────────────────────────────────────────────

def print_responses(responses: dict):
    print()
    for name in ["claude", "codex", "gemini"]:
        resp = responses.get(name, "(no response)")
        color = AI_COLORS[name]
        icon  = AI_ICONS[name]
        label = f"{color}{C.BOLD}  {icon} {name.upper()}{C.RESET}"
        line  = f"{C.LINE}{'\u2500' * 60}{C.RESET}"
        print(line)
        print(label)
        print(line)
        print(resp)
        print()


def print_round_summary(diagnostics: dict, round_num: int):
    now_iso = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    print(f"{C.MUTED}{'\u2500' * 60}")
    print(f"  Round {round_num}  \u2502  {now_iso}{C.RESET}")
    for name in ["claude", "codex", "gemini"]:
        info = diagnostics.get(name, {})
        status = info.get("status", "unknown")
        elapsed_ms = info.get("elapsed_ms", 0)
        stdout_chars = info.get("stdout_chars", 0)

        color = AI_COLORS[name]
        if status == "ok":
            badge = f"{C.OK}\u2713{C.RESET}"
        elif status == "timeout":
            badge = f"{C.WARN}\u29d6{C.RESET}"
        else:
            badge = f"{C.ERR}\u2717{C.RESET}"

        secs = elapsed_ms / 1000
        print(f"  {badge} {color}{name:<6}{C.RESET}  "
              f"{C.MUTED}{secs:>6.1f}s  {stdout_chars:>5} chars{C.RESET}")
    print(f"{C.MUTED}{'\u2500' * 60}{C.RESET}")
    print()


def _spinner(stop_event: threading.Event, msg: str):
    """Animated spinner shown while waiting for AI responses."""
    frames = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r{C.BANNER}  {frames[i % len(frames)]} {msg}{C.RESET}  ")
        sys.stdout.flush()
        i += 1
        stop_event.wait(0.1)
    sys.stdout.write(f"\r{' ' * (len(msg) + 10)}\r")
    sys.stdout.flush()

# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    global RELAY_MODE
    history = []

    if RELAY_MODE == "readonly":
        mode_badge = f"{C.OK}readonly \U0001f512{C.RESET}"
    else:
        mode_badge = f"{C.WARN}full \u26a0\ufe0f{C.RESET}"

    print()
    print(f"{C.BANNER}{C.BOLD}  {'═' * 50}{C.RESET}")
    print(f"{C.BANNER}{C.BOLD}    Multi-AI Code Discussion Panel{C.RESET}")
    print(f"{C.BANNER}{C.BOLD}    {C.CLAUDE}◈ Claude{C.BANNER}  {C.CODEX}■ Codex{C.BANNER}  {C.GEMINI}▲ Gemini{C.RESET}")
    print(f"{C.BANNER}{C.BOLD}  {'═' * 50}{C.RESET}")
    print(f"  {C.DIM}Mode: {C.RESET}{mode_badge}")
    print(f"  {C.DIM}Type message \u2192 all three AIs respond in parallel{C.RESET}")
    print(f"  {C.DIM}Empty Enter  \u2192 AIs react to each other's replies{C.RESET}")
    print(f"  {C.DIM}exit / quit  \u2192 leave     Ctrl+U \u2192 clear line{C.RESET}")
    print()

    round_num = 0
    while True:
        try:
            prompt = f"{C.HUMAN}{C.BOLD}YOU\u25b8{C.RESET} "
            line = _read_input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.MUTED}Bye.{C.RESET}")
            break

        if line.lower() in ("exit", "quit", "bye"):
            print(f"{C.MUTED}Bye.{C.RESET}")
            break

        # ─── Runtime commands ─────────────────────────────────────
        if line.startswith("/"):
            cmd = line.lower().split()
            if cmd[0] == "/mode":
                if len(cmd) >= 2 and cmd[1] in ("readonly", "full"):
                    RELAY_MODE = cmd[1]
                    if RELAY_MODE == "readonly":
                        badge = f"{C.OK}readonly \U0001f512{C.RESET}"
                    else:
                        badge = f"{C.WARN}full \u26a0\ufe0f{C.RESET}"
                    print(f"  {C.DIM}Mode switched to:{C.RESET} {badge}")
                else:
                    cur = f"{C.OK}readonly{C.RESET}" if RELAY_MODE == "readonly" else f"{C.WARN}full{C.RESET}"
                    print(f"  {C.DIM}Current mode:{C.RESET} {cur}")
                    print(f"  {C.DIM}Usage: /mode readonly  or  /mode full{C.RESET}")
                continue
            elif cmd[0] == "/help":
                print(f"  {C.DIM}/mode [readonly|full]  \u2192 switch permission mode{C.RESET}")
                print(f"  {C.DIM}/help                 \u2192 show this help{C.RESET}")
                print(f"  {C.DIM}Empty Enter           \u2192 AIs react to each other{C.RESET}")
                print(f"  {C.DIM}exit / quit            \u2192 leave{C.RESET}")
                continue
            # Unknown slash command — treat as normal message

        if not line:
            if not history:
                continue
            human_msg = (
                "Please review what the other AIs said in the previous round. "
                "Add any corrections, disagreements, or additional insights if you have them. "
                "If you agree or have nothing to add, just say so briefly."
            )
        else:
            human_msg = line

        round_num += 1

        # Spinner while waiting
        stop_spin = threading.Event()
        spin_thread = threading.Thread(
            target=_spinner, args=(stop_spin, "Asking all three AIs..."), daemon=True
        )
        spin_thread.start()

        responses, diagnostics = ask_all(human_msg, history)

        stop_spin.set()
        spin_thread.join()

        print_responses(responses)
        print_round_summary(diagnostics, round_num)

        history.append({"human": human_msg, "responses": responses})


if __name__ == "__main__":
    main()

