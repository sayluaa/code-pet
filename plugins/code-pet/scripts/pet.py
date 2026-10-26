# -*- coding: utf-8 -*-
"""코드 펫 훅

작업하는 동안 펫이 표정과 한마디로 상태를 알려준다.
  prompt  (UserPromptSubmit)    하루 첫 작업이면 날씨 인사, 이번 턴의 도구 호출 수를 0으로 초기화
  tool    (PostToolUse)         화이팅 문구 갱신, 직전 에러와 같은 명령이 성공하면 '해결' 처리
  fail    (PostToolUseFailure)  Bash 에러 -> 시무룩
  stop    (notify-done.ps1 이 호출)  토큰 집계 + 마무리 표정을 stop.json 에 기록
  greet-worker                  날씨를 받아 인사 팝업을 띄우는 내부용 (prompt 가 백그라운드로 실행)

상태는 데이터 폴더(~/.claude/code-pet/)의 JSON 파일에 남기고, 어떤 단계가 실패해도 Claude 의 작업을 막지 않도록
항상 exit 0 으로 끝난다. 오류는 error.log 에 기록한다.
"""

import datetime
import glob
import json
import os
import random
import subprocess
import sys
import time
import traceback
import urllib.request

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
PET_DIR = os.environ.get("CODE_PET_DATA") or os.path.join(CONFIG_HOME, "code-pet")
GIF_DIR = os.path.join(PET_DIR, "gifs")
PROJECTS_DIR = os.path.join(CONFIG_HOME, "projects")
POPUP_SCRIPT = os.path.join(HOOK_DIR, "pet-popup.ps1")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

DEFAULT_CONFIG = {
    "cheer_every": 5,  # 도구를 이 횟수만큼 쓸 때마다 화이팅 문구를 바꾼다
    "sad_popup_gap_sec": 60,  # 에러 팝업 사이의 최소 간격 (에러가 연달아 나도 조용히)
    "unresolved_expire_sec": 1800,  # 이 시간이 지나도 해결 못 하면 시무룩을 풀어준다
    "count_cache_read": False,  # True 면 캐시에서 읽은 토큰도 사용량에 합친다
    "location_override": None,  # 예: {"city": "서울", "lat": 37.57, "lon": 126.98}
}

# 폰트 호환을 위해 한글·ASCII 위주의 텍스트 이모티콘만 쓴다 (GIF 가 없을 때의 대체 표정)
FACES = {
    "hello": "(^o^)/",
    "cheer": "(^_^)b",
    "sad": "(ㅠ_ㅠ)",
    "happy": "\\(^▽^)/",
    "proud": "(*^o^*)v",
    "worry": "(;_;)",
    "chat": "(^_^)",
}

# 표정별 GIF 를 못 찾을 때 대신 찾아볼 이름 순서
GIF_FALLBACK = {
    "worry": ["worry", "sad"],
    "chat": ["chat", "cheer"],
    "proud": ["proud", "happy"],
}

LINES = {
    "hello": ["오늘도 같이 힘내봐요!", "좋은 하루 시작해봐요!"],
    "cheer": ["잘하고 있어요!", "조금만 더 힘내요!", "척척 진행 중이에요!", "멋져요, 계속 가요!"],
    "sad": ["앗, 에러가 났어요…", "괜찮아요, 같이 고쳐봐요", "빨간 글씨가 떴어요…"],
    "happy": ["해결했어요!!", "해냈다! 대단해요!", "에러가 사라졌어요!"],
    "proud": ["오늘도 해냈어요!", "다 끝났어요, 뿌듯해요!", "수고했어요!"],
    "worry": ["아직 에러가 남아 있어요…", "에러가 안 고쳐졌어요…"],
    "chat": ["궁금한 건 언제든 물어봐요", "또 알려드릴게요!"],
}

# 실패해도 에러로 치지 않을 명령 (검색 결과 없음 등으로 exit 1 이 흔하다)
IGNORED_COMMANDS = {
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "diff",
    "cmp",
    "test",
    "[",
    "[[",
    "findstr",
    "where",
    "which",
}

WEATHER_CODES = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    56: "어는 이슬비",
    57: "어는 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    66: "어는 비",
    67: "어는 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    77: "싸락눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    85: "눈 소나기",
    86: "강한 눈 소나기",
    95: "뇌우",
    96: "우박 동반 뇌우",
    99: "우박 동반 뇌우",
}
RAIN_CODES = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}


# ── 파일 입출력 ──────────────────────────────────────────────


def pet_file(name):
    return os.path.join(PET_DIR, name)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default() if callable(default) else default


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def save_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False))


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(pet_file("config.json"), {}))
    if not os.path.exists(pet_file("config.json")):
        write_text(
            pet_file("config.json"),
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
        )
    return cfg


def default_state():
    return {
        "date_greeted": "",
        "tool_count": 0,
        "turn_tools": 0,
        "cheer_text": "",
        "unresolved": None,  # {"key": 명령 앞부분, "ts": 시각}
        "last_sad_popup": 0,
    }


def load_state():
    state = default_state()
    state.update(load_json(pet_file("state.json"), {}))
    return state


def save_state(state):
    save_json(pet_file("state.json"), state)


# ── 펫 표현 (문구 · 상태줄 · 팝업) ───────────────────────────


def say(cfg, mood):
    return random.choice(LINES[mood])


def find_gif(cfg, mood):
    for name in GIF_FALLBACK.get(mood, [mood]) + ["default"]:
        path = os.path.join(GIF_DIR, name + ".gif")
        if os.path.isfile(path):
            return path
    return ""


def set_status(mood, text, ttl):
    """상태줄(statusline-command.sh)이 읽는 한 줄. '만료시각<TAB>문구' 형식."""
    write_text(
        pet_file("status.txt"),
        "{}\t{} {}\n".format(int(time.time() + ttl), FACES[mood], text),
    )


def show_popup(cfg, mood, title, message, seconds=5):
    save_json(
        pet_file("popup.json"),
        {
            "id": time.time_ns(),
            "face": FACES[mood],
            "gif": find_gif(cfg, mood),
            "title": title,
            "message": message,
            "seconds": seconds,
        },
    )
    spawn(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            POPUP_SCRIPT,
            "-Data",
            pet_file("popup.json"),
        ]
    )


def spawn(cmd):
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


# ── 날씨 인사 ────────────────────────────────────────────────


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "claude-pet-hook"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_location(cfg, today):
    """현재 위치(IP 기준). 하루 한 번만 조회하고, 실패하면 지난 값을 쓴다."""
    if cfg.get("location_override"):
        return cfg["location_override"]
    cached = load_json(pet_file("location.json"), {})
    if cached.get("date") == today:
        return cached
    try:
        info = http_json("https://ipwho.is/")
        if info.get("success"):
            loc = {
                "date": today,
                "city": info.get("city", ""),
                "lat": info["latitude"],
                "lon": info["longitude"],
            }
            save_json(pet_file("location.json"), loc)
            return loc
    except Exception:
        pass
    return cached or None


def get_weather(loc):
    url = (
        "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=1"
    ).format(lat=loc["lat"], lon=loc["lon"])
    try:
        return http_json(url)
    except Exception:
        return None


def time_greeting():
    hour = datetime.datetime.now().hour
    if hour < 5:
        return "늦은 밤이에요"
    if hour < 12:
        return "좋은 아침이에요"
    if hour < 18:
        return "좋은 오후예요"
    return "좋은 저녁이에요"


def weather_lines(loc, wx):
    place = " · " + loc["city"] if loc and loc.get("city") else ""
    lines = [time_greeting() + place]
    if not wx:
        return lines + ["날씨는 못 가져왔어요. 그래도 오늘도 화이팅!"]
    cur, day = wx["current"], wx["daily"]
    code = cur["weather_code"]
    tmax, tmin = (
        round(day["temperature_2m_max"][0]),
        round(day["temperature_2m_min"][0]),
    )
    prob = day["precipitation_probability_max"][0] or 0
    lines.append(
        "{} {}° (최고 {}° / 최저 {}°)".format(
            WEATHER_CODES.get(code, "날씨 정보 없음"),
            round(cur["temperature_2m"]),
            tmax,
            tmin,
        )
    )
    if code in RAIN_CODES or prob >= 50:
        tip = "비 올 수 있어요, 우산 챙기세요 (강수 {}%)".format(prob)
    elif code in SNOW_CODES:
        tip = "눈이 와요, 따뜻하게 입어요"
    elif tmax >= 30:
        tip = "더워요, 물 자주 마셔요"
    elif tmin <= 0:
        tip = "추워요, 따뜻하게 입어요"
    else:
        tip = "오늘도 차분하게 해봐요"
    return lines + [tip]


def greet_worker():
    cfg, today = load_config(), datetime.date.today().isoformat()
    loc = get_location(cfg, today)
    wx = get_weather(loc) if loc else None
    show_popup(
        cfg, "hello", say(cfg, "hello"), "\n".join(weather_lines(loc, wx)), seconds=9
    )


# ── 토큰 집계 ────────────────────────────────────────────────


def local_day(timestamp):
    dt = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return dt.astimezone().date().isoformat()


def update_ledger():
    """모든 대화 기록(jsonl)에서 새로 쌓인 부분만 읽어 날짜별 토큰 장부에 더한다."""
    ledger = load_json(pet_file("tokens.json"), {})
    files, days, seen = (
        ledger.setdefault("files", {}),
        ledger.setdefault("days", {}),
        ledger.setdefault("seen", {}),
    )
    for path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
        try:
            size = os.path.getsize(path)
            offset = files.get(path, 0)
            if size < offset:
                offset = 0  # 파일이 다시 쓰였다면 처음부터 (seen 이 중복 합산을 막는다)
            if size == offset:
                continue
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
        except OSError:
            continue
        end = chunk.rfind(b"\n")
        if end < 0:
            continue  # 아직 한 줄이 다 써지지 않음
        files[path] = offset + end + 1
        for raw in chunk[:end].split(b"\n"):
            if b'"type":"assistant"' not in raw:
                continue
            try:
                entry = json.loads(raw)
                msg = entry["message"]
                usage, mid = msg["usage"], msg["id"]
                day = local_day(entry["timestamp"])
            except (KeyError, ValueError, TypeError):
                continue
            now = [
                usage.get("input_tokens") or 0,
                usage.get("cache_creation_input_tokens") or 0,
                usage.get("cache_read_input_tokens") or 0,
                usage.get("output_tokens") or 0,
            ]
            # 같은 응답이 여러 줄로 기록되므로 응답 id 별로 이미 센 만큼은 빼고 더한다
            prev_day, prev = (
                (seen[mid][0], seen[mid][1:]) if mid in seen else (day, [0, 0, 0, 0])
            )
            delta = [max(n - p, 0) for n, p in zip(now, prev)]
            bucket = days.setdefault(prev_day, [0, 0, 0, 0])
            for i, d in enumerate(delta):
                bucket[i] += d
            seen[mid] = [prev_day] + [max(n, p) for n, p in zip(now, prev)]
    if len(seen) > 60000:  # 장부가 끝없이 커지지 않게 오래된 id 부터 버린다
        for key in list(seen)[:20000]:
            del seen[key]
    save_json(pet_file("tokens.json"), ledger)
    return days


def used_tokens(bucket, cfg):
    """[입력, 캐시생성, 캐시읽기, 출력] -> 사용량. 캐시 읽기는 설정에 따라 포함."""
    return (
        bucket[0]
        + bucket[1]
        + bucket[3]
        + (bucket[2] if cfg["count_cache_read"] else 0)
    )


def fmt_tokens(n):
    if n >= 1_000_000:
        return "{:.2f}M".format(n / 1_000_000)
    if n >= 1000:
        return "{:.1f}K".format(n / 1000)
    return str(n)


# ── 이벤트 처리 ──────────────────────────────────────────────


def command_keys(command):
    """&&, ;, || 로 이어 붙인 명령을 조각내 각 조각의 앞 두 단어를 키로 만든다."""
    parts, buf, quote, i = [], [], None, 0
    command = command or ""
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                buf.append(command[i])
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ";" or command.startswith(("&&", "||"), i):
            parts.append("".join(buf))  # 따옴표 밖의 구분자에서만 자른다
            buf = []
            i += 1 if ch != ";" else 0
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [" ".join(p.split()[:2]).lower() for p in parts if p.strip()]


def command_key(command):
    """에러가 난 명령의 키. exit code 를 정하는 건 마지막 조각이라 그것을 기억한다."""
    keys = command_keys(command)
    return keys[-1] if keys else ""


def expire_unresolved(state, cfg):
    bad = state["unresolved"]
    if bad and time.time() - bad["ts"] > cfg["unresolved_expire_sec"]:
        state["unresolved"] = None


def on_prompt(cfg, state):
    today = datetime.date.today().isoformat()
    state["turn_tools"] = 0
    expire_unresolved(state, cfg)
    if state["date_greeted"] != today:
        state["date_greeted"] = today  # 먼저 기록해서 인사가 두 번 뜨지 않게 한다
        spawn([sys.executable, os.path.abspath(__file__), "greet-worker"])
    if not state["unresolved"]:
        set_status("hello", say(cfg, "hello"), 90)


def on_fail(cfg, state, data):
    if data.get("is_interrupt"):
        return
    command = (data.get("tool_input") or {}).get("command", "")
    first = (command.split() or [""])[0].lower()
    if first in IGNORED_COMMANDS:
        return
    state["unresolved"] = {"key": command_key(command), "ts": time.time()}
    text = say(cfg, "sad")
    set_status("sad", text, 3600)
    if time.time() - state["last_sad_popup"] >= cfg["sad_popup_gap_sec"]:
        state["last_sad_popup"] = time.time()
        short = " ".join(command.split())[:40]
        show_popup(
            cfg, "sad", text, "명령을 실행하다 에러가 났어요\n" + short, seconds=5
        )


def on_tool(cfg, state, data):
    state["tool_count"] += 1
    state["turn_tools"] += 1
    expire_unresolved(state, cfg)
    bad = state["unresolved"]
    if bad:
        command = (data.get("tool_input") or {}).get("command", "")
        if data.get("tool_name") == "Bash" and bad["key"] in command_keys(command):
            state["unresolved"] = None
            text = say(cfg, "happy")
            set_status("happy", text, 120)
            show_popup(
                cfg,
                "happy",
                text,
                "아까 에러가 났던 명령이 이번엔 성공했어요",
                seconds=5,
            )
        return  # 아직 해결 전이면 시무룩한 상태줄을 그대로 둔다
    if not state["cheer_text"] or state["tool_count"] % max(cfg["cheer_every"], 1) == 1:
        state["cheer_text"] = say(cfg, "cheer")
    set_status("cheer", state["cheer_text"], 90)


def on_stop(cfg, state, payload_path):
    expire_unresolved(state, cfg)
    days = update_ledger()
    today = datetime.date.today().isoformat()
    today_used = used_tokens(days.get(today, [0, 0, 0, 0]), cfg)
    total_used = sum(used_tokens(b, cfg) for b in days.values())
    if state["unresolved"]:
        mood = "worry"
    elif state["turn_tools"] > 0:
        mood = "proud"
    else:
        mood = "chat"
    text = say(cfg, mood)
    set_status(mood, text, 300)
    save_json(
        pet_file("stop.json"),
        {
            "mood": mood,
            "face": FACES[mood],
            "speech": text,
            "gif": find_gif(cfg, mood),
            "today": fmt_tokens(today_used),
            "total": fmt_tokens(total_used),
        },
    )


def read_payload():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = load_config()
    if event == "greet-worker":
        return greet_worker()
    state = load_state()
    if event == "prompt":
        on_prompt(cfg, state)
    elif event == "fail":
        on_fail(cfg, state, read_payload())
    elif event == "tool":
        on_tool(cfg, state, read_payload())
    elif event == "stop":
        on_stop(cfg, state, sys.argv[2] if len(sys.argv) > 2 else "")
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            with open(pet_file("error.log"), "a", encoding="utf-8") as f:
                f.write(
                    time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n"
                )
        except Exception:
            pass
    sys.exit(0)
