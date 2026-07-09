"""
各SNSへの投稿処理
X (Twitter), Facebook, Threads の API 呼び出し
"""

import logging
import os
import time
from typing import List, Dict

import requests
import tweepy
from sqlalchemy.orm import Session

from .database import Settings

logger = logging.getLogger(__name__)

# 画像を外部公開するためのベースURL (ngrok等で公開している場合はそのURLに変更)
BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://zi-dong-tou-gao-tsuruapuri.onrender.com")


def get_platform_config(platform: str, db: Session, user_id: int) -> dict:
    """DBからAPIキー設定を取得（ユーザーごと）"""
    setting = db.query(Settings).filter(
        Settings.platform == platform,
        Settings.user_id == user_id,
    ).first()
    # 【修正】setting.config が空辞書 {} でも falsy になるため is not None で判定
    if setting and setting.config is not None:
        return setting.config
    return {}


# ────────────────────────────────────────────
# X (Twitter)
# ────────────────────────────────────────────

def post_to_x(text: str, image_urls: List[str], config: dict) -> dict:
    """X (Twitter) v2 API で投稿"""
    try:
        client = tweepy.Client(
            consumer_key=config["api_key"],
            consumer_secret=config["api_secret"],
            access_token=config["access_token"],
            access_token_secret=config["access_token_secret"],
        )

        media_ids = []
        if image_urls:
            auth = tweepy.OAuth1UserHandler(
                config["api_key"], config["api_secret"],
                config["access_token"], config["access_token_secret"],
            )
            api_v1 = tweepy.API(auth)
            for url in image_urls[:4]:
                local_path = url.lstrip("/")
                if os.path.exists(local_path):
                    media = api_v1.media_upload(local_path)
                    media_ids.append(media.media_id)

        kwargs = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids

        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        return {
            "success": True,
            "post_id": tweet_id,
            "url": f"https://twitter.com/i/web/status/{tweet_id}",
        }

    except Exception as e:
        logger.error(f"X投稿エラー: {e}")
        return {"success": False, "error": str(e)}


# ────────────────────────────────────────────
# Facebook
# ────────────────────────────────────────────

def post_to_facebook(text: str, image_urls: List[str], config: dict) -> dict:
    """Facebook Graph API でページ投稿"""
    try:
        page_id = config["page_id"]
        access_token = config["page_access_token"]
        base_url = "https://graph.facebook.com/v18.0"

        if image_urls:
            photo_ids = []
            for url in image_urls[:4]:
                local_path = url.lstrip("/")
                if os.path.exists(local_path):
                    with open(local_path, "rb") as f:
                        resp = requests.post(
                            f"{base_url}/{page_id}/photos",
                            data={"access_token": access_token, "published": "false"},
                            files={"source": f},
                        )
                    resp.raise_for_status()
                    photo_ids.append({"media_fbid": resp.json()["id"]})

            payload = {
                "message": text,
                "access_token": access_token,
                "attached_media": photo_ids,
            }
            resp = requests.post(f"{base_url}/{page_id}/feed", json=payload)
        else:
            resp = requests.post(
                f"{base_url}/{page_id}/feed",
                data={"message": text, "access_token": access_token},
            )

        resp.raise_for_status()
        post_id = resp.json().get("id", "")
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://facebook.com/{post_id}",
        }

    except Exception as e:
        logger.error(f"Facebook投稿エラー: {e}")
        return {"success": False, "error": str(e)}


# ────────────────────────────────────────────
# Threads
# ────────────────────────────────────────────

def _threads_check(resp: requests.Response) -> dict:
    """Threads APIのレスポンスを検証し、失敗時はMetaのエラーメッセージ付きで例外を送出する"""
    try:
        resp_json = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise Exception(f"予期しないレスポンス: {resp.text}")

    if not resp.ok or "error" in resp_json:
        err = resp_json.get("error", {})
        raise Exception(err.get("message", str(err) or resp.text))
    return resp_json


def _threads_wait_until_ready(
    container_id: str, access_token: str, base_url: str, timeout: int = 30, interval: int = 2
) -> None:
    """画像を含むコンテナはMeta側の処理完了まで数十秒かかるため、公開前にステータスをポーリングする
    (公式ドキュメント: publish前に平均30秒程度の処理時間を見込む必要がある)"""
    elapsed = 0
    while elapsed < timeout:
        resp = requests.get(
            f"{base_url}/{container_id}",
            params={"fields": "status", "access_token": access_token},
        )
        status = _threads_check(resp).get("status")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise Exception("メディアの処理に失敗しました")
        time.sleep(interval)
        elapsed += interval


def post_to_threads(text: str, image_urls: List[str], config: dict) -> dict:
    """Threads API (Meta) で投稿"""
    try:
        # 【修正】フロントのキー名に合わせる: User ID→api_key, Access Token→access_token
        user_id = config.get("api_key")
        access_token = config.get("access_token")
        base_url = "https://graph.threads.net/v1.0"

        if not image_urls:
            payload = {
                "access_token": access_token,
                "media_type": "TEXT",
                "text": text,
            }
            resp = requests.post(f"{base_url}/{user_id}/threads", data=payload)

        elif len(image_urls) == 1:
            image_url = (
                image_urls[0]
                if image_urls[0].startswith("http")
                else BASE_URL + image_urls[0]
            )
            payload = {
                "access_token": access_token,
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": text,
            }
            resp = requests.post(f"{base_url}/{user_id}/threads", data=payload)

        else:
            item_ids = []
            for url in image_urls[:10]:
                image_url = url if url.startswith("http") else BASE_URL + url
                r = requests.post(
                    f"{base_url}/{user_id}/threads",
                    data={
                        "access_token": access_token,
                        "media_type": "IMAGE",
                        "image_url": image_url,
                        "is_carousel_item": "true",
                    },
                )
                item_id = _threads_check(r)["id"]
                _threads_wait_until_ready(item_id, access_token, base_url)
                item_ids.append(item_id)

            resp = requests.post(
                f"{base_url}/{user_id}/threads",
                data={
                    "access_token": access_token,
                    "media_type": "CAROUSEL",
                    "children": ",".join(item_ids),
                    "text": text,
                },
            )

        container_id = _threads_check(resp)["id"]
        if image_urls:
            # 画像を含む場合のみMeta側の処理完了を待つ（TEXTのみの投稿は即時公開可能）
            _threads_wait_until_ready(container_id, access_token, base_url)

        pub_resp = requests.post(
            f"{base_url}/{user_id}/threads_publish",
            data={
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        post_id = _threads_check(pub_resp)["id"]
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://www.threads.net/t/{post_id}",
        }

    except Exception as e:
        logger.error(f"Threads投稿エラー: {e}")
        return {"success": False, "error": str(e)}


# ────────────────────────────────────────────
# ディスパッチャー
# ────────────────────────────────────────────

def post_to_platforms(
    text: str,
    platforms: List[str],
    image_urls: List[str],
    db: Session,
    user_id: int,
) -> Dict[str, dict]:
    """指定プラットフォームに一括投稿"""
    results = {}

    for platform in platforms:
        config = get_platform_config(platform, db, user_id)
        if not config:
            results[platform] = {"success": False, "error": "APIキーが設定されていません"}
            continue

        if platform == "x":
            results["x"] = post_to_x(text, image_urls, config)
        elif platform == "facebook":
            results["facebook"] = post_to_facebook(text, image_urls, config)
        elif platform == "threads":
            results["threads"] = post_to_threads(text, image_urls, config)
        else:
            results[platform] = {"success": False, "error": f"未対応のプラットフォーム: {platform}"}

    return results