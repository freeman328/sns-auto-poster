"""
投稿 API ルーター (Pattern Mismatch エラー完全解消版)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from ..database import get_db, Post, PostStatus, User
from ..scheduler import schedule_post, cancel_schedule
from .auth import get_current_user
from ..poster import post_to_platforms

router = APIRouter()

class PostCreate(BaseModel):
    text: str
    platforms: List[str]
    image_urls: Optional[List[str]] = []
    scheduled_at: Optional[str] = None  # ISO8601 or None (即時)
    repeat: Optional[str] = None        # "daily" | "weekly" | None
    weekdays: Optional[List[int]] = None  # [0,1,4] 毎週の場合

class PostUpdate(BaseModel):
    text: Optional[str] = None
    platforms: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None
    scheduled_at: Optional[str] = None

def serialize_post(post: Post) -> dict:
    """フロントエンドの JavaScript が確実にパースできる日時形式に変換する"""
    # データベースから取り出した日時に UTC のタイムゾーンを明示的に付与する
    sched_str = None
    if post.scheduled_at:
        dt_with_tz = post.scheduled_at.replace(tzinfo=timezone.utc)
        sched_str = dt_with_tz.isoformat().replace("+00:00", "Z")

    posted_str = None
    if post.posted_at:
        dt_with_tz = post.posted_at.replace(tzinfo=timezone.utc)
        posted_str = dt_with_tz.isoformat().replace("+00:00", "Z")

    created_str = None
    if post.created_at:
        dt_with_tz = post.created_at.replace(tzinfo=timezone.utc)
        created_str = dt_with_tz.isoformat().replace("+00:00", "Z")

    return {
        "id": post.id,
        "text": post.text,
        "platforms": post.platforms,
        "image_urls": post.image_urls or [],
        "scheduled_at": sched_str,
        "posted_at": posted_str,
        "status": post.status,
        "error_message": post.error_message,
        "created_at": created_str,
        "repeat": post.repeat,
        "weekdays": post.weekdays,
    }

@router.get("/scheduled")
def get_scheduled(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """スケジュール済み投稿一覧（pending かつ scheduled_at あり）"""
    posts = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.scheduled_at.isnot(None),
        Post.user_id == current_user.id
    ).order_by(Post.scheduled_at.asc()).all()
    return [serialize_post(p) for p in posts]

@router.get("/")
def get_posts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 1. ユーザーIDの確認
    print(f"DEBUG: Current User ID is {current_user.id}")
    
    # 2. 全件取得してステータスを確認
    all_user_posts = db.query(Post).filter(Post.user_id == current_user.id).all()
    print(f"DEBUG: User has {len(all_user_posts)} posts in total.")
    for p in all_user_posts:
        print(f"DEBUG: Post ID {p.id}, Status: {p.status}, ScheduledAt: {p.scheduled_at}")
        
    # 3. 本来のフィルタリング
    posts = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.user_id == current_user.id
    ).order_by(Post.scheduled_at.asc()).all()
    
    return [serialize_post(p) for p in posts]

@router.post("/")
def create_post(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """新規投稿作成"""
    sched_dt = None
    if body.scheduled_at:
        try:
            # フロントから送られる Z 付き文字列を安全にパース
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        scheduled_at=sched_dt,
        status=PostStatus.PENDING if sched_dt else PostStatus.POSTED,
        user_id=current_user.id,
        repeat=body.repeat,
        weekdays=body.weekdays
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    if sched_dt and post.status == PostStatus.PENDING:
        schedule_post(post.id, sched_dt)
    else:
        # 実際にSNSに投げるロジックを呼び出す
        results = post_to_platforms(post.text, post.platforms, post.image_urls, db)
        # 結果をDBに反映
        post.status = PostStatus.POSTED
        post.platform_post_ids = results # 投稿IDなどを保存
        db.commit()
    
    return serialize_post(post)

@router.post("/draft")
def save_draft(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """下書き保存 (途切れを修復)"""
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
    """投稿削除 (認証を追加)"""
    post = db.query(Post).filter(Post.id == post_id, Post.user_id == current_user.id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    cancel_schedule(post_id)
    db.delete(post)
    db.commit()
    return {"message": "削除完了"}


class RepostBody(BaseModel):
    scheduled_at: Optional[str] = None
    repeat: Optional[str] = None
    weekdays: Optional[List[int]] = None


@router.post("/{post_id}/repost")
def repost(post_id: int, body: RepostBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """既存の投稿を再投稿（即時 or 予約、繰り返し対応）"""
    original = db.query(Post).filter(Post.id == post_id, Post.user_id == current_user.id).first()
    if not original:
        raise HTTPException(404, "投稿が見つかりません")

    sched_dt = None
    if body.scheduled_at:
        try:
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    new_post = Post(
        text=original.text,
        platforms=original.platforms,
        image_urls=original.image_urls,
        scheduled_at=sched_dt,
        status=PostStatus.PENDING if sched_dt else PostStatus.POSTED,
        user_id=current_user.id,
        repeat=body.repeat,
        weekdays=body.weekdays,
    )
    db.add(new_post)
    db.commit()
    db.refresh(new_post)

    if sched_dt:
        schedule_post(new_post.id, sched_dt)
    else:
        results = post_to_platforms(new_post.text, new_post.platforms, new_post.image_urls, db)
        new_post.status = PostStatus.POSTED
        new_post.platform_post_ids = results
        db.commit()

    return serialize_post(new_post)