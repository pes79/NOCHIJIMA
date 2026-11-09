# -*- coding: utf-8 -*-
"""
kakao_token.py  ── 토큰 자동 갱신 도우미 (발송 스크립트가 가져다 씁니다)

- get_valid_token() 을 부르면:
  · 저장된 access 출입증이 아직 살아있으면 그대로 돌려주고,
  · 만료됐으면 refresh(재발급권)로 새 access 를 받아 저장한 뒤 돌려줍니다.
- 사장님은 이 파일을 직접 실행할 필요 없습니다. (발송 스크립트가 내부에서 사용)
"""

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "kakao_config.json")
TOKEN_FILE = os.path.join(BASE, "kakao_token.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_token(token):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, ensure_ascii=False, indent=2)


def _refresh(cfg, token):
    """refresh_token 으로 새 access_token 을 받아 저장합니다."""
    params = {
        "grant_type": "refresh_token",
        "client_id": cfg["rest_api_key"],
        "refresh_token": token["refresh_token"],
    }
    if cfg.get("client_secret"):
        params["client_secret"] = cfg["client_secret"]

    req = Request(
        "https://kauth.kakao.com/oauth/token",
        data=urlencode(params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
    )
    with urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    now = int(time.time())
    token["access_token"] = data["access_token"]
    token["access_expire_at"] = now + int(data.get("expires_in", 21600)) - 300
    # 카카오는 가끔 새 refresh_token 도 함께 줍니다 → 있으면 갱신
    if data.get("refresh_token"):
        token["refresh_token"] = data["refresh_token"]
        token["refresh_expire_at"] = now + int(data.get("refresh_token_expires_in", 5184000)) - 300
    token["saved_at"] = now
    _save_token(token)
    return token


def get_valid_token():
    """항상 '살아있는' access_token 을 돌려줍니다. 필요하면 자동 갱신."""
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError("아직 토큰이 없습니다. 먼저 '카카오_토큰받기.py' 를 실행하세요.")
    cfg = _load(CONFIG_FILE)
    token = _load(TOKEN_FILE)

    now = int(time.time())
    if now >= token.get("access_expire_at", 0):
        # 출입증 만료 → 재발급
        if now >= token.get("refresh_expire_at", 0):
            raise RuntimeError(
                "재발급권(refresh)까지 만료됐습니다. '카카오_토큰받기.py' 를 다시 한 번 실행해 주세요."
            )
        token = _refresh(cfg, token)
    return token["access_token"]
