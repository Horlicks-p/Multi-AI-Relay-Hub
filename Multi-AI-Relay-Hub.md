# Multi-AI Relay Hub

![Multi-AI Relay Hub](test.png)

A terminal-based Python tool that allows you to orchestrate and converse with three powerful AI models (Claude, Codex, and Gemini) simultaneously in a shared "conference room" environment.

Instead of asking the same question in three different chat interfaces, you can ask once and get diverse, cross-validated responses. The models can even read each other's answers and collaborate to find the optimal solution!

## 🚀 Features

- **Multi-Model Support**: Interfaces seamlessly with `claude-code`, `codex`, and `gemini` CLIs.
- **Cross-Validation**: Let the AIs review and critique each other's code suggestions.
- **Context-Aware**: The bots share the same conversation history context and know who is speaking via `[speaker]` tags.
- **Cross-Platform**: Handles Windows CMD truncation, TTY buffering issues, and encoding safely (works flawlessly on Windows, macOS, and Linux).
- **Project-Aware**: AI models execute in your current working directory, granting them access to analyze and suggest edits to your local codebase.

## 🏗️ Architecture

This module provides a turn-based execution architecture:

### The Turn-Based Panel (`relay.py`)

- **How it works**: Spawns a new CLI process for each AI _every time_ you send a message, injecting the entire conversation history into the prompt.
- **Pros**: Highly stable, zero zombie processes.
- **Cons**: Slower response times due to CLI startup overhead on every turn.
- **Best for**: Deep, deliberate code reviews and architectural planning where you need time to digest long answers.

### File Structure

```
cli/
├── relay.py              # Main entry point
├── cli_common.py         # Shared CLI resolution utilities
├── run_claude_cli.py     # Claude Code wrapper
├── run_codex_cli.py      # Codex wrapper
├── run_gemini_cli.py     # Gemini wrapper
├── requirements.txt      # Python dependencies
└── .env.example          # Environment variable template
```

## 🛠️ Prerequisites

1. **Python 3.10+**
2. **Node.js** (Required for all three CLI tools)
3. **The AI CLIs**: You must install and authenticate the respective CLI tools before using the hub:
   - [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) (`npm install -g @anthropic-ai/claude-code`)
   - [Gemini CLI](https://github.com/google/gemini-cli) (or whatever binary provides your `gemini` command)
   - [Codex CLI](https://github.com/codex-cli/codex) (or whatever binary provides your `codex` command)

4. **Python dependencies**: `pip install -r requirements.txt` (only `python-dotenv` for `.env` auto-loading; optional but recommended)

## ⚙️ Initial Setup & Authentication

Before running the hub, you **must run each CLI manually at least once** in your target directory to accept any interactive prompts, EULAs, or login requests. The hub runs the CLIs in headless/non-interactive mode, so they will freeze if they wait for user input.

```bash
# 1. Start Claude, login, and approve the project directory
claude

# 2. Start Gemini and authenticate
gemini

# 3. Start Codex, ensure it trusts the directory
codex
```

## 🎮 Usage

Navigate to the codebase you want the AIs to analyze, and run the relay script from there. (The script will dynamically locate its wrapper dependencies).

```bash
# Start the turn-based, stable panel:
python /path/to/cli/relay.py
```

### Configuration

You can configure the hub with environment variables or a `.env` file (copy `.env.example` to `.env`):

| Variable            | Default    | Description                                                            |
| ------------------- | ---------- | ---------------------------------------------------------------------- |
| `RELAY_TIMEOUT_SEC` | `600`      | Timeout (seconds) for each AI subprocess                               |
| `MAX_CONTEXT_CHARS` | `32000`    | Maximum context characters sent to each AI                             |
| `RELAY_MODE`        | `readonly` | `readonly` = AIs cannot write files; `full` = AIs can use tools freely |

### The "Empty Enter" Trick

In `relay.py`, if you simply hit `Enter` without typing a message, the hub will automatically prompt the AIs to:

> _"Please review what the other AIs said in the previous round. Add any corrections, disagreements, or additional insights if you have them..."_

This is the ultimate way to trigger an autonomous code review!

## 🧩 How It Works Under the Hood

To bypass complex TTY handling and interactive CLI constraints, the hub uses **Wrapper Scripts** (`run_claude_cli.py`, `run_gemini_cli.py`, `run_codex_cli.py`), with shared utilities in `cli_common.py`.

1. `relay.py` wraps your message in a JSON payload.
2. The payload is piped into the wrappers.
3. The wrapper scripts parse the JSON, prepend the `[human]` or `[ai]` tags, inject a strict `SYSTEM_PROMPT` emphasizing their role in a multi-AI room, and execute the actual CLI binaries using `subprocess.run`.
4. The outputs are captured, buffered, and returned to the hub for display.

### Windows-Specific Handling

On Windows, `cli_common.py` resolves CLI paths by directly locating the npm global directory (`%APPDATA%\npm`) instead of relying on `shutil.which()`. This avoids two common pitfalls:

- **Stray `.bat`/`.cmd` files in the current directory** being picked up before the real CLI (Windows searches cwd first).
- **`.cmd` wrapper files spawning unwanted `cmd.exe` windows.** The resolver tries to find the underlying `.js` entry point and execute it directly via `node`, which runs silently.

> ⚠️ **Important**: Do not place files named `claude.bat`, `codex.bat`, or `gemini.bat` in your working directory. While the resolver is designed to avoid them, removing potential conflicts is the safest approach.

_Note: Special care has been taken in the wrappers to bypass Windows `cmd.exe` string truncation by escaping horizontal newlines (`\n`)._
