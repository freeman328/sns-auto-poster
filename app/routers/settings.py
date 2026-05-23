"""
API設定 ルーター (フォーム非表示バグ完全解消版)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import tweepy
import requests

from ..database import get_db, Settings, User, get_current_user
from fastapi import Depends

router = APIRouter()

class SettingsUpdateBody(BaseModel):
    config: Dict[str, Any]  # {"api_key": "...", "api_secret": "..."} などを辞書型で受け取る

@router.get("/")
def get_all_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """現在ログインしているユーザーの全プラットフォームの設定を取得"""
    settings_list = db.query(Settings).filter(Settings.user_id == current_user.id).all()
    
    # フロントエンドが処理しやすいようにプラットフォーム名をキーにした辞書に整形
    result = {}
    platforms = ["x", "facebook", "threads"]
    
    # 既存の設定をマッピング
    for s in settings_list:
        result[s.platform] = {
            "config": s.config or {},
            "is_connected": s.is_connected or False
        }
        
    # データベースにまだ存在しないプラットフォームがあれば、空の設定を作って返す（フロントのクラッシュ防止）
    for platform in platforms:
        if platform not in result:
            result[platform] = {
                "config": {},
                "is_connected": False
            }
            
    return result

@router.get("/{platform}")
def get_setting(
    platform: str, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """特定のプラットフォームの設定を取得"""
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id
    ).first()
    
    return {"config": setting.config if setting else {}}

@router.post("/{platform}")
def update_setting(
    platform: str, 
    body: SettingsUpdateBody, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    try:
        setting = db.query(Settings).filter(
            Settings.platform == platform.lower(),
            Settings.user_id == current_user.id
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
                is_connected=has_keys
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
        
        print("受信データ:", body.config)
        return {"status": "success", "message": "保存完了"}
    except Exception as e:
        db.rollback()  # エラー時は元に戻す
        raise HTTPException(status_code=500, detail=f"保存に失敗しました: {str(e)}")

@router.post("/test/{platform}")
def test_connection(
    platform: str, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id
    ).first()

    if not setting or not setting.config:
        raise HTTPException(status_code=400, detail="設定が見つかりません")

    config = setting.config
    success = False
    error = None

    try:
        if platform == "x":
            # ここでクライアントを生成して、即座にテスト
            client = tweepy.Client(
                consumer_key=config.get("api_key"),
                consumer_secret=config.get("api_secret"),
                access_token=config.get("access_token"),
                access_token_secret=config.get("access_token_secret"),
            )
            response = client.get_me()
            success = response.data is not None
            
        elif platform == "facebook":
            # Facebookのテスト（ページトークン検証）
            resp = requests.get("https://graph.facebook.com/v18.0/me", 
                               params={"access_token_secret": config.get("access_token_secret")})
            success = resp.status_code == 200
            if not success: error = resp.text
            
        elif platform == "threads":
            headers = {
                "Authorization": f"Bearer {config.get('access_token_secret')}"
            }
            resp = requests.get("https://graph.threads.net/v1.0/me", headers=headers)
            print(f"DEBUG: Threads Response Code: {resp.status_code}")
            print(f"DEBUG: Threads Response Body: {resp.text}")
            success = resp.status_code == 200
            if not success: error = resp.text
            
        else:
            error = "未対応のプラットフォームです"

    except Exception as e:
        error = str(e)
        success = False

    # DB更新
    setting.is_connected = success
    db.commit()
    return {"success": success, "message": "接続テスト完了" if success else error}