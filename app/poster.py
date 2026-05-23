"""
各SNSへの投稿処理
X (Twitter), Facebook, Threads の API 呼び出し
"""

import tweepy
import requests
import logging
import os
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from .database import Settings

logger = logging.getLogger(__name__)

# 画像を外部公開するためのベースURL
# ngrok等で公開している場合はそのURLに変更してください
# 例: BASE_URL = "https://xxxx.ngrok.io"
BASE_URL = "https://zi-dong-tou-gao-tsuruapuri.onrender.com"


def get_platform_config(platform: str, db: Session) -> dict:
    """DBからAPIキー設定を取得"""
    setting = db.query(Settings).filter(Settings.platform == platform).first()
    if setting and setting.config:
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
            # v1.1 APIで画像アップロード
            auth = tweepy.OAuth1UserHandler(
                config["api_key"], config["api_secret"],
                config["access_token"], config["access_token_secret"]
            )
            api_v1 = tweepy.API(auth)
            for url in image_urls[:4]:
                local_path = url.lstrip("/")  # /uploads/xxx.jpg -> uploads/xxx.jpg
                if os.path.exists(local_path):
                    media = api_v1.media_upload(local_path)
                    media_ids.append(media.media_id)

        kwargs = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids

        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        return {"success": True, "post_id": tweet_id, "url": f"https://twitter.com/i/web/status/{tweet_id}"}

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
            # 画像付き投稿
            photo_ids = []
            for url in image_urls[:4]:
                local_path = url.lstrip("/")
                if os.path.exists(local_path):
                    with open(local_path, "rb") as f:
                        resp = requests.post(
                            f"{base_url}/{page_id}/photos",
                            data={"access_token": access_token, "published": "false"},
                            files={"source": f}
                        )
                    resp.raise_for_status()
                    photo_ids.append({"media_fbid": resp.json()["id"]})

            payload = {
                "message": text,
                "access_token": access_token,
                "attached_media": photo_ids
            }
            resp = requests.post(f"{base_url}/{page_id}/feed", json=payload)
        else:
            # テキストのみ
            resp = requests.post(f"{base_url}/{page_id}/feed", data={
                "message": text,
                "access_token": access_token
            })

        resp.raise_for_status()
        post_id = resp.json().get("id", "")
        return {"success": True, "post_id": post_id, "url": f"https://facebook.com/{post_id}"}

    except Exception as e:
        logger.error(f"Facebook投稿エラー: {e}")
        return {"success": False, "error": str(e)}


# ────────────────────────────────────────────
# Threads
# ────────────────────────────────────────────

def post_to_threads(text: str, image_urls: List[str], config: dict) -> dict:
    """Threads API (Meta) で投稿"""
    try:
        user_id = config["user_id"]
        access_token = config["access_token"]
        base_url = "https://graph.threads.net/v1.0"

        # ── STEP1: コンテナ作成 ──
        payload = {
            "access_token": access_token,
            "media_type": "TEXT",
            "text": text,
        }

        resp = requests.post(
            f"{base_url}/{user_id}/threads",
            data=payload
        )

        print("THREADS RESPONSE:", resp.status_code, resp.text)
        resp.raise_for_status()

        if not image_urls:
            # テキストのみ
            params["media_type"] = "TEXT"
            resp = requests.post(
                f"{base_url}/{user_id}/threads",
                params=params
            )
        elif len(image_urls) == 1:
            # シングル画像（公開URLが必要）
            params["media_type"] = "IMAGE"
            params["image_url"] = image_urls[0] if image_urls[0].startswith("http") else BASE_URL + image_urls[0]
            resp = requests.post(
                f"{base_url}/{user_id}/threads",
                params=params
            )
        else:
            # カルーセル
            item_ids = []
            for url in image_urls[:10]:
                r = requests.post(
                    f"{base_url}/{user_id}/threads",
                    params={
                        "access_token": access_token,
                        "media_type": "IMAGE",
                        "image_url": url if url.startswith("http") else BASE_URL + url,
                        "is_carousel_item": "true",
                    }
                )
                r.raise_for_status()
                item_ids.append(r.json()["id"])

            resp = requests.post(
                f"{base_url}/{user_id}/threads",
                params={
                    "access_token": access_token,
                    "media_type": "CAROUSEL",
                    "children": ",".join(item_ids),
                    "text": text,
                }
            )

        resp.raise_for_status()
        resp_json = resp.json()
        if "error" in resp_json:
            raise Exception(resp_json["error"].get("message", str(resp_json["error"])))

        container_id = resp_json["id"]

        # ── STEP2: 公開 ──
        pub_resp = requests.post(
            f"{base_url}/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token,
            }
        )
        pub_resp.raise_for_status()
        pub_json = pub_resp.json()
        if "error" in pub_json:
            raise Exception(pub_json["error"].get("message", str(pub_json["error"])))

        post_id = pub_json["id"]
        return {"success": True, "post_id": post_id,
                "url": f"https://www.threads.net/t/{post_id}"}

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
    db: Session
) -> Dict[str, dict]:
    """指定プラットフォームに一括投稿"""
    results = {}

    for platform in platforms:
        config = get_platform_config(platform, db)
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