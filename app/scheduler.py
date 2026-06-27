"""
スケジューラー設定 (APScheduler)
予約投稿の自動実行を管理
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .database import SessionLocal, Post, PostStatus
from .poster import post_to_platforms

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="Asia/Tokyo")


def init_scheduler():
    """スケジューラー起動 & 未実行の予約投稿を復元"""
    scheduler.start()
    scheduler.add_job(
        check_pending_posts,
        IntervalTrigger(minutes=1),
        id="check_pending",
        replace_existing=True,
    )
    logger.info("✅ スケジューラー起動")


def shutdown_scheduler():
    scheduler.shutdown(wait=False)


def check_pending_posts():
    """1分ごとに期限到来した予約投稿を実行"""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        pending = (
            db.query(Post)
            .filter(
                Post.status == PostStatus.PENDING,
                Post.scheduled_at <= now,
                Post.scheduled_at.isnot(None),
            )
            .all()
        )
        for post in pending:
            logger.info(f"📤 予約投稿実行: post_id={post.id}")
            execute_post(post.id)
    finally:
        db.close()


def schedule_post(post_id: int, scheduled_at: datetime):
    """特定日時に投稿をスケジュール"""
    # 【修正7】デバッグ用 print を削除し、logger に統一
    scheduler.add_job(
        execute_post,
        trigger=DateTrigger(run_date=scheduled_at, timezone="Asia/Tokyo"),
        args=[post_id],
        id=f"post_{post_id}",
        replace_existing=True,
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
    # 【修正6】execute_post は独立したセッションを持ち、schedule_next_repeat には
    #          セッションを渡さず post_id のみ渡すことでセッション2重使用を解消
    db = SessionLocal()
    post = None
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post or post.status == PostStatus.POSTED:
            return

        results = post_to_platforms(
            text=post.text,
            platforms=post.platforms,
            image_urls=post.image_urls,
            db=db,
            user_id=post.user_id,
        )

        all_success = all(r.get("success") for r in results.values())
        post.status = PostStatus.POSTED if all_success else PostStatus.FAILED
        post.posted_at = datetime.now(timezone.utc)
        post.platform_post_ids = {
            p: r.get("post_id") for p, r in results.items() if r.get("success")
        }
        if not all_success:
            errors = {p: r.get("error") for p, r in results.items() if not r.get("success")}
            post.error_message = str(errors)

        # 繰り返し設定を読み取っておく（セッションクローズ前）
        should_repeat = all_success and post.repeat in ("daily", "weekly")
        repeat_type = post.repeat
        weekdays = post.weekdays
        base_time = post.scheduled_at
        original_text = post.text
        original_platforms = post.platforms
        original_image_urls = post.image_urls
        original_user_id = post.user_id

        db.commit()
        logger.info(
            f"{'✅' if all_success else '❌'} 投稿完了: post_id={post_id}, status={post.status}"
        )

    except Exception as e:
        logger.error(f"投稿エラー: post_id={post_id}, error={e}")
        if post:
            post.status = PostStatus.FAILED
            post.error_message = str(e)
            db.commit()
        return
    finally:
        db.close()

    # 【修正6】セッションを閉じた後、別セッションで次回分を登録
    if should_repeat:
        _schedule_next_repeat(
            repeat_type=repeat_type,
            weekdays=weekdays,
            base_time=base_time,
            text=original_text,
            platforms=original_platforms,
            image_urls=original_image_urls,
            user_id=original_user_id,
        )


def _schedule_next_repeat(
    repeat_type: str,
    weekdays,
    base_time: datetime,
    text: str,
    platforms,
    image_urls,
    user_id: int,
):
    """繰り返し投稿の次回スケジュールを独立したセッションで作成"""
    from .database import Post as PostModel

    if not base_time:
        return

    next_dt = None
    if repeat_type == "daily":
        next_dt = base_time + timedelta(days=1)

    elif repeat_type == "weekly":
        sorted_days = sorted(weekdays or [])
        if not sorted_days:
            return
        for i in range(1, 8):
            candidate = base_time + timedelta(days=i)
            if candidate.weekday() in [w % 7 for w in sorted_days]:
                next_dt = candidate
                break

    if next_dt is None:
        return

    db = SessionLocal()
    try:
        new_post = PostModel(
            text=text,
            platforms=platforms,
            image_urls=image_urls,
            scheduled_at=next_dt,
            status=PostStatus.PENDING,
            repeat=repeat_type,
            weekdays=weekdays,
            # 【修正12】user_id を必ず引き継ぐ
            user_id=user_id,
        )
        db.add(new_post)
        db.commit()
        db.refresh(new_post)
        schedule_post(new_post.id, next_dt)
        logger.info(f"🔁 次回繰り返し登録: post_id={new_post.id}, at={next_dt}")
    except Exception as e:
        logger.error(f"繰り返しスケジュール登録エラー: {e}")
        db.rollback()
    finally:
        db.close()