"""
投稿履歴 API ルーター (修正完全版)
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from ..database import get_db, Post, PostStatus, User
from ..auth import get_current_user

router = APIRouter()

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
        "platform_post_ids": post.platform_post_ids or {},
        "created_at": post.created_at.isoformat()
    }

@router.get("/")
def get_history(
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Post).filter(Post.user_id == current_user.id)
    if platform:
        query = query.filter(Post.platforms.contains(platform))
    if status:
        query = query.filter(Post.status == status)
    
    posts = query.order_by(Post.created_at.desc()).all()
    return [serialize_post(p) for p in posts]

@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ダッシュボード統計用"""
    total = db.query(Post).filter(Post.status == PostStatus.POSTED, Post.user_id == current_user.id).count()
    failed = db.query(Post).filter(Post.status == PostStatus.FAILED, Post.user_id == current_user.id).count()
    scheduled = db.query(Post).filter(Post.status == PostStatus.PENDING, Post.user_id == current_user.id).count()
    drafts = db.query(Post).filter(Post.status == PostStatus.DRAFT, Post.user_id == current_user.id).count()

    # 直近7日の自分の投稿数
    week_ago = datetime.utcnow() - timedelta(days=7)
    weekly = db.query(Post).filter(
        Post.status == PostStatus.POSTED,
        Post.posted_at >= week_ago,
        Post.user_id == current_user.id
    ).count()

    # プラットフォーム別の統計
    all_posted = db.query(Post).filter(Post.status == PostStatus.POSTED, Post.user_id == current_user.id).all()
    platform_counts = {"x": 0, "facebook": 0, "threads": 0}
    for post in all_posted:
        for p in (post.platforms or []):
            if p in platform_counts:
                platform_counts[p] += 1

    return {
        "total_posted": total,
        "total_failed": failed,
        "scheduled": scheduled,
        "drafts": drafts,
        "weekly_posts": weekly,
        "by_platform": platform_counts
    }