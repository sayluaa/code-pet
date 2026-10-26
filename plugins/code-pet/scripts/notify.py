"""Stop 훅: 답변이 끝나면 완료 알림창(notify-done.ps1)을 백그라운드로 띄운다.

훅 입력(JSON)을 임시 파일에 저장해서 알림창에 넘기고, 훅 자체는 바로 끝난다.
어떤 단계가 실패해도 Claude 의 작업을 막지 않도록 항상 exit 0 으로 끝난다.
"""

import os
import subprocess
import sys
import tempfile
import uuid

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
NOTIFY_SCRIPT = os.path.join(HOOK_DIR, "notify-done.ps1")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200  # 훅이 끝나도 알림창이 계속 살아 있게 한다


def main():
    if os.environ.get(
        "CLAUDE_NOTIFY_CHILD"
    ):  # 자식 Claude 세션에서는 알림을 띄우지 않는다
        return
    raw = sys.stdin.buffer.read()
    payload = os.path.join(
        tempfile.gettempdir(), "claude-stop-" + uuid.uuid4().hex + ".json"
    )
    with open(payload, "wb") as f:
        f.write(raw)
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            NOTIFY_SCRIPT,
            "-Payload",
            payload,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
