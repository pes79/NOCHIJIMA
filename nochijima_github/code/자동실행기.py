# -*- coding: utf-8 -*-
"""
자동실행기.py  ── 감시 프로그램을 '자동으로' 돌리고 결과를 로그 파일에 쌓는 도우미

작업 스케줄러가 정해진 시간마다 이 파일을 부릅니다. 예:
    pythonw.exe 자동실행기.py 환율감시.py

하는 일:
  1) 지정한 프로그램(예: 환율감시.py)을 실행하고,
  2) 그 결과(화면 출력)를 logs 폴더의 날짜별 로그 파일에 '이어붙여' 저장하고,
  3) 인터넷 끊김·오류가 나도 절대 멈추지 않고 오류 내용을 로그에 남깁니다.
     (다음 주기에 스케줄러가 다시 실행하므로 자동 재시도됨)

※ 이 구조의 장점: 원본 감시 프로그램은 전혀 손대지 않음. 나중에 24시간 서버로
   옮길 때도 이 폴더째 옮기고, 스케줄러(윈도우) 대신 cron(리눅스)만 걸면 됩니다.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

# 이 파일이 있는 폴더 = 프로젝트 폴더 (어디서 실행하든 여기 기준)
BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")

# 프로그램이 너무 오래 걸리면 중단할 시간(초). 다음 주기에 다시 시도됨.
TIMEOUT_SEC = 600  # 10분


def main():
    if len(sys.argv) < 2:
        print("사용법: python 자동실행기.py <프로그램파일명>")
        print("예:   python 자동실행기.py 환율감시.py")
        return

    target = sys.argv[1]                       # 예: 환율감시.py
    extra = sys.argv[2:]                        # 대상에 넘길 추가 옵션 (예: 저녁)
    target_path = os.path.join(BASE, target)
    os.makedirs(LOG_DIR, exist_ok=True)

    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    base_name = os.path.splitext(target)[0]    # 환율감시
    log_path = os.path.join(LOG_DIR, f"{base_name}_{day}.log")

    header = ("\n" + "=" * 64 + "\n"
              f"[실행 시작] {stamp}   ({target})\n" + "=" * 64 + "\n")

    # 콘솔 창이 뜨지 않게(윈도우) 하는 옵션
    no_window = 0
    if os.name == "nt":
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    healthy = True     # 정상 실행 + 데이터 수신 여부 (심장박동용)
    note = ""
    try:
        result = subprocess.run(
            [sys.executable, target_path] + extra,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SEC,
            creationflags=no_window,
        )
        body = result.stdout or ""
        if result.stderr:
            body += "\n----- [오류/경고 출력] -----\n" + result.stderr
        footer = f"\n[실행 끝] 정상 종료 (코드 {result.returncode})\n"
        # 종료코드가 0이 아니거나, 데이터 수신 실패/발송 실패 표시가 있으면 '비정상'
        BAD = ["가져오지 못", "토큰 문제", "❌"]
        if result.returncode != 0:
            healthy, note = False, f"종료코드 {result.returncode}"
        elif any(m in body for m in BAD):
            healthy, note = False, "데이터/발송 실패 표시 감지"
    except subprocess.TimeoutExpired:
        body = ("⏱️ 시간 초과로 중단했습니다. 인터넷이 느리거나 응답이 없었을 수 있어요.\n"
                "   걱정 마세요 — 다음 주기에 자동으로 다시 시도합니다.\n")
        footer = "\n[실행 끝] 시간 초과(TIMEOUT)\n"
        healthy, note = False, "시간 초과"
    except Exception as e:
        body = (f"❌ 실행 중 문제가 생겼습니다: {e}\n"
                "   다음 주기에 자동으로 다시 시도합니다.\n")
        footer = "\n[실행 끝] 오류(ERROR)\n"
        healthy, note = False, f"실행 오류: {e}"

    # 무슨 일이 있어도 로그는 남긴다
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(header + body + footer)
    except Exception:
        pass  # 로그조차 실패해도 스케줄러가 멈추지 않도록 조용히 넘어감

    # ── 심장박동 기록 (자가감시.py가 읽음) ──
    #   각 감시가 '마지막으로 언제 정상 돌았는지'를 heartbeat.json에 남긴다.
    try:
        hb_path = os.path.join(BASE, "heartbeat.json")
        try:
            with open(hb_path, "r", encoding="utf-8") as f:
                hb = json.load(f)
        except Exception:
            hb = {}
        rec = hb.get(base_name, {})
        rec["last_run"] = stamp                 # 실행을 시도한 시각(성공/실패 무관)
        if healthy:
            rec["last_ok"] = stamp              # 정상(데이터 수신)한 시각
        rec["ok"] = healthy
        rec["note"] = note
        hb[base_name] = rec
        with open(hb_path, "w", encoding="utf-8") as f:
            json.dump(hb, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 심장박동 기록 실패해도 본 작업은 멈추지 않게


if __name__ == "__main__":
    main()
