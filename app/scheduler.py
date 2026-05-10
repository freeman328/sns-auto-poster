"""
スケジューラー設定 (APScheduler)
予約投稿の自動実行を管理
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import logging

from .database import SessionLocal, Post, PostStatus
from .poster import post_to_platforms

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="Asia/Tokyo")


def init_scheduler():
    """スケジューラー起動 & 未実行の予約投稿を復元"""
    scheduler.start()
    # 起動時に未実行スケジュールを再登録
    scheduler.add_job(
        check_pending_posts,
        IntervalTrigger(minutes=1),
        id="check_pending",
        replace_existing=True
    )
    logger.info("✅ スケジューラー起動")


def shutdown_scheduler():
    scheduler.shutdown(wait=False)


def check_pending_posts():
    """1分ごとに期限到来した予約投稿を実行"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        pending = db.query(Post).filter(
            Post.status == PostStatus.PENDING,
            Post.scheduled_at <= now,
            Post.scheduled_at.isnot(None)
        ).all()

        for post in pending:
            logger.info(f"📤 予約投稿実行: post_id={post.id}")
            execute_post(post.id)
    finally:
        db.close()


def schedule_post(post_id: int, scheduled_at: datetime):
    """特定日時に投稿をスケジュール"""
    scheduler.add_job(
        execute_post,
        trigger=DateTrigger(run_date=scheduled_at, timezone="Asia/Tokyo"),
        args=[post_id],
        id=f"post_{post_id}",
        replace_existing=True
    )
    logger.info(f"📅 スケジュール登録: post_id={post_id}, at={scheduled_at}")


def cancel_schedule(post_id: int):
    """スケジュールをキャンセル"""
    job_id = f"post_{post_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"🗑 スケジュールキャンセル: post_id={post_id}")


def execute_post(post_id: int):
    """投稿を実行してDBを更新。繰り返し設定があれば次回分を自動登録"""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post or post.status == PostStatus.POSTED:
            return

        results = post_to_platforms(
            text=post.text,
            platforms=post.platforms,
            image_urls=post.image_urls,
            db=db
        )

        all_success = all(r.get("success") for r in results.values())
        post.status = PostStatus.POSTED if all_success else PostStatus.FAILED
        post.posted_at = datetime.utcnow()
        post.platform_post_ids = {
            p: r.get("post_id") for p, r in results.items() if r.get("success")
        }
        if not all_success:
            errors = {p: r.get("error") for p, r in results.items() if not r.get("success")}
            post.error_message = str(errors)

        db.commit()
        logger.info(f"{'✅' if all_success else '❌'} 投稿完了: post_id={post_id}, status={post.status}")

        # ── 繰り返し設定があれば次回分を新規登録 ──
        if all_success and post.repeat in ("daily", "weekly"):
            schedule_next_repeat(post, db)

    except Exception as e:
        logger.error(f"投稿エラー: post_id={post_id}, error={e}")
        if post:
            post.status = PostStatus.FAILED
            post.error_message = str(e)
            db.commit()
    finally:
        db.close()


def schedule_next_repeat(original: Post, db):
    """繰り返し投稿の次回スケジュールを作成"""
    from .database import Post as PostModel

    base_time = original.scheduled_at
    if not base_time:
        return

    if original.repeat == "daily":
        next_dt = base_time + timedelta(days=1)

    elif original.repeat == "weekly":
        weekdays = sorted(original.weekdays or [])
        if not weekdays:
            return
        # 次の該当曜日を探す
        for i in range(1, 8):
            candidate = base_time + timedelta(days=i)
            if candidate.weekday() in [w % 7 for w in weekdays]:
                next_dt = candidate
                break
        else:
            return
    else:
        return

    new_post = PostModel(
        text=original.text,
        platforms=original.platforms,
        image_urls=original.image_urls,
        scheduled_at=next_dt,
        status="pending",
        repeat=original.repeat,
        weekdays=original.weekdays,
    )
    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    schedule_post(new_post.id, next_dt)
    logger.info(f"🔁 次回繰り返し登録: post_id={new_post.id}, at={next_dt}")
