"""
API設定 ルーター
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, Any

import tweepy
import requests

from ..database import get_db, Settings, User
from ..auth import get_current_user

router = APIRouter()


class SettingsUpdateBody(BaseModel):
    config: Dict[str, Any]


@router.get("/")
def get_all_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings_list = db.query(Settings).filter(Settings.user_id == current_user.id).all()

    result = {}
    platforms = ["x", "facebook", "threads"]

    for s in settings_list:
        result[s.platform] = {
            "config": s.config or {},
            "is_connected": s.is_connected or False,
        }

    for platform in platforms:
        if platform not in result:
            result[platform] = {"config": {}, "is_connected": False}

    return result


@router.get("/{platform}")
def get_setting(
    platform: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id,
    ).first()

    return {
        "config": setting.config if setting else {},
        "is_connected": setting.is_connected if setting else False,
    }


@router.post("/{platform}")
def update_setting(
    platform: str,
    body: SettingsUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        setting = db.query(Settings).filter(
            Settings.platform == platform.lower(),
            Settings.user_id == current_user.id,
        ).first()

        has_keys = any(body.config.values())

        if setting:
            setting.config = body.config
            setting.is_connected = has_keys
        else:
            setting = Settings(
                platform=platform.lower(),
                user_id=current_user.id,
                config=body.config,
                is_connected=has_keys,
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
        return {"status": "success", "message": "保存完了"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"保存に失敗しました: {str(e)}")


@router.post("/test/{platform}")
def test_connection(
    platform: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id,
    ).first()

    if not setting or not setting.config:
        raise HTTPException(status_code=400, detail="設定が見つかりません")

    config = setting.config
    success = False
    error = None

    try:
        if platform == "x":
            client = tweepy.Client(
                consumer_key=config.get("api_key"),
                consumer_secret=config.get("api_secret"),
                access_token=config.get("access_token"),
                access_token_secret=config.get("access_token_secret"),
            )
            response = client.get_me()
            success = response.data is not None

        elif platform == "facebook":
            # 【修正4】パラメータ名を access_token_secret → access_token に修正
            resp = requests.get(
                "https://graph.facebook.com/v18.0/me",
                params={"access_token": config.get("page_access_token")},
            )
            success = resp.status_code == 200
            if not success:
                error = resp.text

        elif platform == "threads":
            # アクセストークンはクエリパラメータで渡す（Threads API の要件）
            access_token = config.get("access_token_secret") or config.get("access_token")
            resp = requests.get(
                "https://graph.threads.net/v1.0/me",
                params={"access_token": access_token, "fields": "id,username"},
            )
            body = resp.json() if resp.content else {}
            if "error" in body:
                err = body["error"]
                error = f"Threads API エラー (code={err.get('code')}): {err.get('message', str(err))}"
                success = False
            else:
                success = resp.status_code == 200
                if not success:
                    error = resp.text

        else:
            error = "未対応のプラットフォームです"

    except Exception as e:
        error = str(e)
        success = False

    setting.is_connected = success
    db.commit()
    return {"success": success, "message": "接続テスト完了" if success else error}