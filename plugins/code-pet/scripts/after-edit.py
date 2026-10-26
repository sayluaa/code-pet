# -*- coding: utf-8 -*-
"""PostToolUse(Write|Edit) 훅

파일이 저장된 직후 아래를 순서대로 처리한다.
  1. 변경 로그 기록 (edit-log.txt)
  2. .py 파일이면 ruff 로 자동 포맷
  3. .py 파일이면 ruff 로 린트 검사 -> 문제가 있으면 Claude 에게 다시 알려줌
  4. 화면 오른쪽 아래에 저장 알림 토스트 표시
어떤 단계가 실패해도 Claude 의 작업을 막지 않도록 항상 exit 0 으로 끝난다.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
DATA_DIR = os.environ.get("CODE_PET_DATA") or os.path.join(CONFIG_HOME, "code-pet")
LOG_FILE = os.path.join(DATA_DIR, "edit-log.txt")
TOAST_SCRIPT = os.path.join(HOOK_DIR, "save-toast.ps1")
SKIP_PARTS = ("site-packages", "node_modules", ".venv", "venv", ".git")
MAX_LINT_LINES = 10

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200  # 훅이 끝나도 토스트가 계속 살아 있게 한다


def read_payload():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def get_file_path(data):
    resp = data.get("tool_response")
    if isinstance(resp, dict) and resp.get("filePath"):
        return resp["filePath"]
    return (data.get("tool_input") or {}).get("file_path")


def ruff_command():
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    return [sys.executable, "-m", "ruff"]


def run(cmd):
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
    )
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    # 메시지가 길어지지 않도록 전체 경로는 파일명으로 줄인다
    for arg in cmd:
        if os.path.isabs(arg) and os.path.isfile(arg):
            out = out.replace(arg, os.path.basename(arg))
    return proc.returncode, out


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def format_and_lint(path):
    """(상태 문구 목록, Claude 에게 전달할 메시지 목록) 을 돌려준다."""
    status, notes = [], []
    ruff = ruff_command()

    before = read_bytes(path)
    code, out = run(ruff + ["format", path])
    if code == 0:
        changed = read_bytes(path) != before
        status.append("포맷 적용" if changed else "포맷 그대로")
        if changed:
            notes.append(
                "ruff format 이 이 파일을 자동 포맷했습니다. "
                "이후 수정하기 전에 파일을 다시 읽어서 최신 내용을 확인하세요."
            )
    else:
        status.append("포맷 실패")
        notes.append("ruff format 실패:\n" + out)

    code, out = run(ruff + ["check", "--output-format", "concise", "--no-fix", path])
    if code == 0:
        status.append("린트 통과")
    else:
        lines = [
            ln
            for ln in out.splitlines()
            if ln.strip()
            and not ln.startswith(("Found ", "All checks", "[*]", "No fixes"))
        ]
        status.append("린트 {}건".format(len(lines)) if lines else "린트 오류")
        shown = "\n".join(lines[:MAX_LINT_LINES])
        more = (
            "\n... 외 {}건".format(len(lines) - MAX_LINT_LINES)
            if len(lines) > MAX_LINT_LINES
            else ""
        )
        notes.append("ruff 린트 결과:\n" + shown + more)

    return status, notes


def write_log(tool, path, status):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "{} | {} | {} | {}\n".format(stamp, tool, path, ", ".join(status) or "-")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def show_toast(name, status):
    if not os.path.exists(TOAST_SCRIPT):
        return
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            TOAST_SCRIPT,
            "-Name",
            name,
            "-Status",
            " · ".join(status),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def main():
    data = read_payload()
    path = get_file_path(data)
    if not path or not os.path.isfile(path):
        return

    status, notes = [], []
    is_python = path.lower().endswith(".py")
    skip = any(part in path.replace("\\", "/").split("/") for part in SKIP_PARTS)

    if is_python and not skip:
        try:
            status, notes = format_and_lint(path)
        except Exception as exc:  # ruff 없음, 타임아웃 등
            status = ["포맷/린트 건너뜀"]
            notes = []
            write_log("error", path, [repr(exc)])
    if not status:
        status = ["저장됨"]

    write_log(data.get("tool_name", "?"), path, status)
    show_toast(os.path.basename(path), status)

    if notes:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "\n\n".join(notes),
                    }
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
