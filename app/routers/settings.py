"""
API設定 ルーター (フォーム非表示バグ完全解消版)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional

from ..database import get_db, Settings, User
from ..auth import get_current_user

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
def get_setting(platform: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """特定のプラットフォームの設定を取得"""
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id
    ).first()
    
    if not setting:
        return {"platform": platform, "config": {}, "is_connected": False}
        
    return {
        "platform": setting.platform,
        "config": setting.config or {},
        "is_connected": setting.is_connected or False
    }

@router.post("/{platform}")
def update_setting(
    platform: str, 
    body: SettingsUpdateBody, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """APIキー設定の保存・更新"""
    # 既存の設定があるか確認
    setting = db.query(Settings).filter(
        Settings.platform == platform.lower(),
        Settings.user_id == current_user.id
    ).first()

    # 必須のキーが空でなければ接続済み(is_connected=True)とみなす簡易ロジック
    # (xの場合は api_key や bearer_token など)
    has_keys = any(v for v in body.config.values() if v)

    if setting:
        # 更新
        setting.config = body.config
        setting.is_connected = has_keys
    else:
        # 新規作成（念のためのフォールバック）
        setting = Settings(
            platform=platform.lower(),
            user_id=current_user.id,
            config=body.config,
            is_connected=has_keys
        )
        db.add(setting)

    db.commit()
    db.refresh(setting)
    
    return {
        "message": f"{platform} の設定を保存しました", 
        "is_connected": setting.is_connected,
        "config": setting.config
    }