"""
設定 API ルーター (APIキー管理)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
import requests
import tweepy

from ..database import get_db, Settings

router = APIRouter()


class PlatformConfig(BaseModel):
    platform: str
    config: dict


def mask_secret(value: str) -> str:
    """APIキーをマスク表示"""
    if not value or len(value) < 8:
        return "****"
    return value[:4] + "****" + value[-4:]


@router.get("/")
def get_all_settings(db: Session = Depends(get_db)):
    """全プラットフォームの設定取得（マスク済み）"""
    settings = db.query(Settings).all()
    result = {}
    for s in settings:
        masked = {}
        for k, v in (s.config or {}).items():
            masked[k] = mask_secret(v) if v else ""
        result[s.platform] = {
            "platform": s.platform,
            "config": masked,
            "is_connected": s.is_connected,
        }
    return result


@router.post("/")
def save_settings(body: PlatformConfig, db: Session = Depends(get_db)):
    """APIキー設定を保存"""
    setting = db.query(Settings).filter(Settings.platform == body.platform).first()
    if not setting:
        setting = Settings(platform=body.platform)
        db.add(setting)

    # 空文字はスキップ（既存値を保持）
    existing = setting.config or {}
    for k, v in body.config.items():
        if v and not v.endswith("****"):  # マスク値でなければ更新
            existing[k] = v
    setting.config = existing
    flag_modified(setting, "config")
    db.commit()
    return {"message": f"{body.platform} の設定を保存しました"}


@router.post("/test/{platform}")
def test_connection(platform: str, db: Session = Depends(get_db)):
    """APIキーの接続テスト"""
    setting = db.query(Settings).filter(Settings.platform == platform).first()
    if not setting or not setting.config:
        raise HTTPException(400, "APIキーが設定されていません")

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
            me = client.get_me()
            success = me.data is not None

        elif platform == "facebook":
            resp = requests.get(
                f"https://graph.facebook.com/v18.0/me",
                params={"access_token": config.get("page_access_token")}
            )
            success = resp.status_code == 200

        elif platform == "threads":
            resp = requests.get(
                f"https://graph.threads.net/v1.0/me",
                params={"access_token": config.get("access_token")}
            )
            success = resp.status_code == 200

    except Exception as e:
        error = str(e)

    setting.is_connected = success
    db.commit()

    if success:
        return {"success": True, "message": f"{platform} への接続確認OK"}
    else:
        raise HTTPException(400, f"接続失敗: {error or '認証エラー'}")


@router.delete("/{platform}")
def delete_settings(platform: str, db: Session = Depends(get_db)):
    """APIキー設定を削除"""
    setting = db.query(Settings).filter(Settings.platform == platform).first()
    if setting:
        setting.config = {}
        setting.is_connected = False
        db.commit()
    return {"message": f"{platform} の設定を削除しました"}