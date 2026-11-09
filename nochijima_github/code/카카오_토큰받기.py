# -*- coding: utf-8 -*-
"""
카카오_토큰받기.py  ── (한 번만 실행) 인증코드(code)를 토큰으로 바꿔 저장합니다.

■ 미리 할 일: 브라우저에서 '인증 주소'를 열고 → 동의 → 주소창의 code=... 를 복사.
■ 실행하면: code 를 붙여넣으라고 물어봅니다. 붙여넣고 Enter.
■ 결과: kakao_token.json 파일에 access(출입증)·refresh(재발급권) 토큰이 저장됩니다.
   (REST API 키는 kakao_config.json 파일에서 읽습니다 — 코드에 직접 안 넣음)
"""

import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "kakao_config.json")
TOKEN_FILE = os.path.join(BASE, "kakao_token.json")


def main():
    # 1) 설정 읽기 (REST API 키 등)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    print("=" * 52)
    print("   카카오 토큰 받기 (한 번만 하면 됩니다)")
    print("=" * 52)
    print("브라우저에서 인증 주소를 열고 동의한 뒤,")
    print("주소창의 code=... 뒷부분을 복사해 아래에 붙여넣으세요.\n")

    code = input("인증코드(code)를 붙여넣고 Enter: ").strip()
    if not code:
        print("❌ code 가 비어 있습니다. 다시 실행해 주세요.")
        return

    # 2) code -> 토큰 교환
    params = {
        "grant_type": "authorization_code",
        "client_id": cfg["rest_api_key"],
        "redirect_uri": cfg["redirect_uri"],
        "code": code,
    }
    if cfg.get("client_secret"):
        params["client_secret"] = cfg["client_secret"]

    try:
        req = Request(
            "https://kauth.kakao.com/oauth/token",
            data=urlencode(params).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print("\n❌ 토큰 발급 실패. 카카오 응답:")
        print("   " + body)
        print("\n[자주 있는 원인]")
        print(" - code 는 한 번만 쓸 수 있고 몇 분 뒤 만료됩니다 → 인증 주소를 다시 열어 새 code 로.")
        print(" - Redirect URI 가 앱에 등록한 것과 정확히 같아야 합니다.")
        return
    except Exception as e:
        print(f"\n❌ 오류: {e}")
        return

    # 3) 만료시각 계산해서 저장
    now = int(time.time())
    token = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        # access 는 보통 6시간(21600초). 5분 여유 두고 만료시각 기록.
        "access_expire_at": now + int(data.get("expires_in", 21600)) - 300,
        "refresh_expire_at": now + int(data.get("refresh_token_expires_in", 5184000)) - 300,
        "saved_at": now,
    }
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, ensure_ascii=False, indent=2)

    print("\n✅ 성공! 토큰을 kakao_token.json 에 저장했습니다.")
    print("   이제 카톡 발송 스크립트를 쓸 수 있어요.")
    print("   (access 는 6시간마다 자동 갱신됩니다 — 사장님이 다시 안 하셔도 됩니다.)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
