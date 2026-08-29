from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_visible_console() -> None:
    if os.name != "nt":
        raise RuntimeError("visible evidence adapter requires Windows")
    os.system("mode con cols=118 lines=42 >nul")
    ctypes.windll.kernel32.SetConsoleTitleW(
        "Skill Magnet — 実Codex実行証拠 — codex-cli 0.148.0"
    )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def _purpose(prompt: str) -> str:
    return next(
        line.split("=", 1)[1]
        for line in prompt.splitlines()
        if line.startswith("PURPOSE=")
    )


def _safe_argv(command: list[str]) -> list[str]:
    safe: list[str] = []
    for value in command:
        if value.startswith("mcp_servers."):
            safe.append(value)
        elif value.endswith("schema.json") or value.endswith("output.json"):
            safe.append(f"<{Path(value).name}>")
        else:
            safe.append(value)
    return safe


def _installed_codex_0148_executable() -> str:
    wrapper = shutil.which("codex.cmd")
    if not wrapper:
        raise RuntimeError("installed codex.cmd 0.148.0 was not found")
    executable = (
        Path(wrapper).parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    if not executable.is_file():
        raise RuntimeError("codex-cli 0.148.0 vendor codex.exe was not found")
    return str(executable)


def _inner_main(metadata_path: Path, prompt_path: Path, runtime_args: list[str]) -> int:
    prompt = prompt_path.read_bytes().decode("utf-8", errors="strict")
    actual_request = _purpose(prompt)
    codex_exe = _installed_codex_0148_executable()
    version = subprocess.run(
        [codex_exe, "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
    ).stdout.strip()
    if version != "codex-cli 0.148.0":
        raise RuntimeError(f"unexpected installed Codex version: {version}")
    command = [codex_exe, *runtime_args]
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_visible_console()

    metadata_path.write_text(
        json.dumps(
            {"stage": "visible_console_attached", "runtime": version},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("SKILL MAGNET — 実CODEX実行（同一contract/schema/acceptance）")
    print("=" * 90)
    print(f"開始時刻 (UTC): {_timestamp()}")
    print(f"Runtime: {version}")
    print("選択スキル: codex-sandbox-approval-boundary")
    print(f"依頼: {actual_request}")
    print("Sandbox: read-only / Approval: never / Session: ephemeral")
    print("MCP disable (このprocessのみ): cloudflare-builds, cloudflare-observability, unreal-mcp")
    print("-" * 90)
    print("実Codexを起動します。以下は実codex.exeのevent streamです。")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True, encoding="utf-8")
    metadata = {
        "started_at": _timestamp(),
        "runtime": version,
        "codex_executable": codex_exe,
        "codex_pid": process.pid,
        "codex_argv": _safe_argv(command),
        "actual_request": actual_request,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    process.communicate(prompt)
    metadata["codex_exit_code"] = process.returncode
    metadata["codex_finished_at"] = _timestamp()

    output_path = Path(runtime_args[runtime_args.index("--output-last-message") + 1])
    task_output = ""
    if process.returncode == 0 and output_path.is_file():
        envelope = json.loads(output_path.read_text(encoding="utf-8"))
        task_output = str(envelope["result"]["task_output"])
    os.system("cls")
    print("SKILL MAGNET — 実CODEX実行結果")
    print("=" * 90)
    print(f"Runtime: {version}")
    print(f"実codex.exe PID: {process.pid}（終了済み）")
    print(f"実codex.exe exit code: {process.returncode}")
    print("ARGV: codex.exe -c <3 MCP process限定disable> --ask-for-approval never exec")
    print("      --ephemeral --ignore-rules --json --sandbox read-only --cd C:\\Projects\\skill-magnet")
    print("選択スキル: codex-sandbox-approval-boundary")
    print(f"依頼: {actual_request}")
    print("-" * 90)
    print("実Codexの最終回答（全文）")
    print(task_output if task_output else "<実Codexは最終回答を生成しませんでした>")
    print("-" * 90)
    print("Skill Magnetによるcontract/schema/acceptance検証の完了を待っています。")

    viewer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
    )
    metadata["viewer_pid"] = viewer.pid
    metadata["task_output"] = task_output
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return process.returncode


def main() -> int:
    if len(sys.argv) >= 5 and sys.argv[1] == "--inner":
        return _inner_main(
            Path(sys.argv[2]).resolve(),
            Path(sys.argv[3]).resolve(),
            sys.argv[4:],
        )
    if len(sys.argv) < 3:
        raise SystemExit("usage: visible_codex_runtime_adapter.py METADATA RUNTIME_ARGS...")
    metadata_path = Path(sys.argv[1]).resolve()
    runtime_args = sys.argv[2:]
    prompt_path = metadata_path.with_name("visible-runtime-prompt.tmp")
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.unlink(missing_ok=True)
    prompt_path.write_bytes(sys.stdin.buffer.read())
    try:
        conhost = Path(os.environ["SystemRoot"]) / "System32" / "conhost.exe"
        host = subprocess.Popen(
            [
                str(conhost),
                sys.executable,
                str(Path(__file__).resolve()),
                "--inner",
                str(metadata_path),
                str(prompt_path),
                *runtime_args,
            ]
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if "codex_exit_code" in metadata and "viewer_pid" in metadata:
                    return int(metadata["codex_exit_code"])
                if metadata.get("stage") == "adapter_failed":
                    return 1
            if host.poll() is not None:
                return host.returncode or 1
            time.sleep(0.1)
        raise TimeoutError("visible Codex evidence runner did not finish in 180 seconds")
    finally:
        prompt_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        if len(sys.argv) >= 2:
            metadata_arg = sys.argv[2] if sys.argv[1] == "--inner" else sys.argv[1]
            error_path = Path(metadata_arg).resolve()
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(
                json.dumps(
                    {
                        "stage": "adapter_failed",
                        "error_type": type(exc).__name__,
                        "safe_error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        raise
