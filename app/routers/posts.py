"""
投稿 API ルーター (500内部エラー完全解決版)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from ..database import get_db, Post, PostStatus, User
from ..scheduler import schedule_post, cancel_schedule
from ..poster import post_to_platforms  # SNSへの即時送信処理
from ..auth import get_current_user

router = APIRouter()

class PostCreate(BaseModel):
    text: str
    platforms: List[str]
    image_urls: Optional[List[str]] = []
    scheduled_at: Optional[str] = None  # ISO8601 or None (即時)
    repeat: Optional[str] = None        # "daily" | "weekly" | None
    weekdays: Optional[List[int]] = None

def serialize_post(post: Post) -> dict:
    return {
        "id": post.id,
        "text": post.text,
        "platforms": post.platforms,
        "image_urls": post.image_urls or [],
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
        "status": post.status,
        "error_message": post.error_message,
        "created_at": post.created_at.isoformat(),
    }

@router.get("/")
def get_posts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """スケジュール中（待機中）の投稿一覧"""
    posts = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.user_id == current_user.id
    ).order_by(Post.scheduled_at.asc()).all()
    return [serialize_post(p) for p in posts]

@router.post("/")
def create_post(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """新規投稿の作成（即時送信、または予約登録）"""
    sched_dt = None
    if body.scheduled_at:
        try:
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    # 1. 予約投稿（未来の時間）の場合
    if sched_dt:
        post = Post(
            text=body.text,
            platforms=body.platforms,
            image_urls=body.image_urls,
            scheduled_at=sched_dt,
            status=PostStatus.PENDING,
            user_id=current_user.id,
            repeat=body.repeat,
            weekdays=body.weekdays
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        
        # スケジューラー（APScheduler）にジョブを登録
        schedule_post(post.id, sched_dt)
        return serialize_post(post)

    # 2. 今すぐ投稿（即時送信）の場合
    else:
        # 【修正の肝】 引数に db=db を確実に渡し、型ミスマッチによるクラッシュを防止
        results = post_to_platforms(
            text=body.text,
            platforms=body.platforms,
            image_urls=body.image_urls or [],
            db=db
        )
        
        # 選択したすべてのSNSで送信が成功したかチェック
        all_success = all(res.get("success", False) for res in results.values()) if results else False
        
        # エラーメッセージの集約
        err_msg = None
        if not all_success and results:
            errors = [f"{p}: {res.get('error')}" for p, res in results.items() if not res.get("success")]
            err_msg = " | ".join(errors) if errors else "投稿に失敗しました"

        post = Post(
            text=body.text,
            platforms=body.platforms,
            image_urls=body.image_urls,
            scheduled_at=None,
            posted_at=datetime.utcnow() if all_success else None,
            status=PostStatus.POSTED if all_success else PostStatus.FAILED,
            error_message=err_msg,
            user_id=current_user.id,
            platform_post_ids={p: res.get("post_id") for p, res in results.items() if res.get("success")} if results else {}
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return serialize_post(post)

@router.post("/draft")
def save_draft(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """下書き保存"""
    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        status=PostStatus.DRAFT,
        user_id=current_user.id
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return serialize_post(post)

@router.delete("/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """投稿削除（自身のもののみ）"""
    post = db.query(Post).filter(Post.id == post_id, Post.user_id == current_user.id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    cancel_schedule(post_id)
    db.delete(post)
    db.commit()
    return {"message": "削除完了"}