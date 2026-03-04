# relay_ready.py
# Dependencies: python 3.10+, pip install aioconsole
import asyncio
import json
import uuid
import shlex
import os
from typing import Dict
import aioconsole
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 取得 relay_ready.py 所在的絕對目錄路徑
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 拿掉字串裡的環境變數宣告和 stdbuf（改由 start_process_shell 的 env 參數統一處理）
MODELS = {
    "codex": f"python -u \"{os.path.join(SCRIPT_DIR, 'run_codex_cli.py')}\"",
    "claude": f"python -u \"{os.path.join(SCRIPT_DIR, 'run_claude_cli.py')}\"",
    "gemini": f"python -u \"{os.path.join(SCRIPT_DIR, 'run_gemini_cli.py')}\"",
}

# 可調參數
READLINE_TIMEOUT = 0.45   # 讀取 stdout 的短暫 idle timeout（秒）
MSG_TTL_MAX = 5           # TTL 避免訊息無限循環
HUB_QUEUE_SIZE = 1000

class ModelClient:
    def __init__(self, name, proc: asyncio.subprocess.Process):
        self.name = name
        self.proc = proc
        self.queue: asyncio.Queue = asyncio.Queue()
        self.alive = True

async def start_process_shell(cmd: str) -> asyncio.subprocess.Process:
    """Start a subprocess via shell (cross-platform env handling)."""

    # 複製當前系統的環境變數
    custom_env = os.environ.copy()

    # 強制 Python 取消緩衝
    custom_env["PYTHONUNBUFFERED"] = "1"

    # 強制 Node.js 取消緩衝 (若 CLI 工具是用內建的 console.log)
    # 另外，若 Claude CLI 有自訂互動介面，有時 FORCE_COLOR=0 可以避免終端機控制碼(ANSI)干擾解析
    custom_env["FORCE_COLOR"] = "0"

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=custom_env  # 將環境變數安全地注入
    )
    logging.info(f"Started process PID={proc.pid} CMD={cmd}")
    return proc

async def reader_task(client: ModelClient, hub_queue: asyncio.Queue):
    """累積 buffer，遇到 timeout 或特定 prompt 才送出完整段落（解決多行切碎）。"""
    r = client.proc.stdout
    buffer_lines = []
    while True:
        try:
            line = await asyncio.wait_for(r.readline(), timeout=READLINE_TIMEOUT)
            if not line:
                # process closed
                break
            s = line.decode(errors="ignore")
            buffer_lines.append(s)
            # 檢查特殊提示字元（像 >>> 或模型明確輸出 END_OF_RESPONSE）
            joined = "".join(buffer_lines)
            if ">>> " in s or "END_OF_RESPONSE" in joined:
                full_text = joined.strip()
                if full_text:
                    msg = {"id": str(uuid.uuid4()), "from": client.name, "to": "broadcast", "kind": "comment", "payload": full_text, "ttl": MSG_TTL_MAX}
                    await hub_queue.put(msg)
                buffer_lines = []
        except asyncio.TimeoutError:
            # idle => 把累積的 buffer 當作一段輸出
            if buffer_lines:
                full_text = "".join(buffer_lines).strip()
                if full_text:
                    msg = {"id": str(uuid.uuid4()), "from": client.name, "to": "broadcast", "kind": "comment", "payload": full_text, "ttl": MSG_TTL_MAX}
                    await hub_queue.put(msg)
                buffer_lines = []
            # 繼續等待新的輸出或終止
    logging.info(f"reader_task exiting for {client.name}")

async def writer_task(client: ModelClient):
    """如果 CLI 不支援 JSON，傳純文字給 stdin（加上來源提示）。"""
    w = client.proc.stdin
    while True:
        msg = await client.queue.get()
        if msg is None:
            break
        try:
            # 使用 JSON 序列化整個訊息物件並加上換行符號
            # 確保每個訊息在 stdin 中佔據一行，即使 payload 有多行
            json_str = json.dumps(msg, ensure_ascii=False) + "\n"
            w.write(json_str.encode("utf-8"))
            await w.drain()
        except Exception as e:
            logging.warning(f"Failed to write to {client.name}: {e}")
            break
    logging.info(f"writer_task exiting for {client.name}")

async def stderr_logger(client: ModelClient):
    r = client.proc.stderr
    while True:
        line = await r.readline()
        if not line:
            break
        logging.warning(f"[{client.name} STDERR] {line.decode(errors='ignore').rstrip()}")

async def hub_main():
    hub_q = asyncio.Queue(maxsize=HUB_QUEUE_SIZE)
    clients: Dict[str, ModelClient] = {}
    seen = set()  # 去重 message id

    # start processes
    for name, cmd in MODELS.items():
        proc = await start_process_shell(cmd)
        client = ModelClient(name, proc)
        clients[name] = client
        asyncio.create_task(reader_task(client, hub_q))
        asyncio.create_task(writer_task(client))
        asyncio.create_task(stderr_logger(client))

    # human input using aioconsole (non-blocking)
    async def human_input():
        while True:
            try:
                line = await aioconsole.ainput("YOU> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            msg = {"id": str(uuid.uuid4()), "from": "human", "to": "broadcast", "kind": "instruction", "payload": line, "ttl": MSG_TTL_MAX}
            print("\nAsking all three AIs in parallel…")
            await hub_q.put(msg)
    asyncio.create_task(human_input())

    async def route_message(msg):
        # 基本 TTL & 去重 機制
        mid = msg.get("id")
        if not mid:
            mid = str(uuid.uuid4()); msg["id"] = mid
        if mid in seen:
            return
        seen.add(mid)

        ttl = msg.get("ttl", MSG_TTL_MAX)
        if ttl <= 0:
            return
        msg["ttl"] = ttl - 1

        sender = msg.get("from")
        to = msg.get("to", "broadcast")
        logging.info(f"[HUB] {sender} -> {to} : kind={msg.get('kind')} payload_preview={str(msg.get('payload'))[:80].replace('\\n',' ')}")
        # broadcast to all models except sender
        if to == "broadcast":
            for name, c in clients.items():
                if name == sender:
                    continue
                await c.queue.put(msg)
        else:
            # direct to specific model (若找不到，丟回 hub)
            if to in clients:
                await clients[to].queue.put(msg)
            else:
                logging.warning(f"Unknown target {to}; ignoring")

    # hub loop
    try:
        while True:
            msg = await hub_q.get()
            # small safety: ensure payload is string
            if "payload" in msg and not isinstance(msg["payload"], str):
                msg["payload"] = json.dumps(msg["payload"])
            asyncio.create_task(route_message(msg))
    except asyncio.CancelledError:
        logging.info("hub_main cancelled, shutting down")
    finally:
        # graceful shutdown of child procs
        for name, c in clients.items():
            try:
                c.proc.terminate()
            except Exception:
                pass
            
        # Wait for all processes to close to avoid Windows Proactor unclosed transport warnings
        for name, c in clients.items():
            try:
                await asyncio.wait_for(c.proc.wait(), timeout=1.0)
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(hub_main())
    except KeyboardInterrupt:
        logging.info("Exiting by user")