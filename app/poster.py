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

# アップロードディレクトリの絶対パス
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))


def _resolve_local_path(url: str) -> str:
    """
    '/uploads/uuid.jpg' → '/absolute/path/to/uploads/uuid.jpg'
    URLの先頭スラッシュをもとに絶対パスへ変換する。
    """
    # '/uploads/xxx.jpg' → 'uploads/xxx.jpg' → absolute path
    relative = url.lstrip("/")
    # 'uploads/xxx.jpg' → UPLOAD_DIR/xxx.jpg
    filename = os.path.basename(relative)
    return os.path.join(UPLOAD_DIR, filename)


def get_platform_config(platform: str, db: Session, user_id: int) -> dict:
    """DBからAPIキー設定を取得（ユーザーごと）"""
    setting = db.query(Settings).filter(
        Settings.platform == platform,
        Settings.user_id == user_id,
    ).first()
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
            auth = tweepy.OAuth1UserHandler(
                config["api_key"], config["api_secret"],
                config["access_token"], config["access_token_secret"],
            )
            api_v1 = tweepy.API(auth)
            for url in image_urls[:4]:
                local_path = _resolve_local_path(url)
                if not os.path.exists(local_path):
                    logger.warning(f"X: 画像ファイルが見つかりません: {local_path}")
                    continue
                media = api_v1.media_upload(local_path)
                media_ids.append(media.media_id)
                logger.info(f"X: 画像アップロード完了: {local_path} → media_id={media.media_id}")

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
        # UIから保存される形式に合わせて両方のキー名に対応
        page_id = config.get("page_id") or config.get("api_key")
        access_token = config.get("page_access_token") or config.get("access_token_secret")
        base_url = "https://graph.facebook.com/v18.0"

        photo_ids = []
        for url in (image_urls or [])[:4]:
            local_path = _resolve_local_path(url)
            if not os.path.exists(local_path):
                logger.warning(f"Facebook: 画像ファイルが見つかりません: {local_path}")
                continue
            with open(local_path, "rb") as f:
                r = requests.post(
                    f"{base_url}/{page_id}/photos",
                    data={"access_token": access_token, "published": "false"},
                    files={"source": f},
                )
            r.raise_for_status()
            photo_ids.append({"media_fbid": r.json()["id"]})
            logger.info(f"Facebook: 画像アップロード完了: {local_path}")

        if photo_ids:
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

def _threads_request(method: str, url: str, access_token: str, **kwargs) -> requests.Response:
    """
    Threads Graph API へのリクエストを送信する。
    アクセストークンはクエリパラメータで渡す（Threads API の要件）。
    エラー時は JSON レスポンスのメッセージを含む例外を送出する。
    """
    params = kwargs.pop("params", {})
    params["access_token"] = access_token
    resp = getattr(requests, method)(url, params=params, **kwargs)

    # HTTP エラーより先に JSON を確認して詳細メッセージを取得
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {}

    if "error" in resp_json:
        err = resp_json["error"]
        msg = err.get("message", str(err))
        code = err.get("code", "")
        subcode = err.get("error_subcode", "")
        raise Exception(f"Threads API エラー (code={code}, subcode={subcode}): {msg}")

    resp.raise_for_status()
    return resp


def _wait_for_threads_container(base_url: str, container_id: str, access_token: str, max_wait: int = 30) -> None:
    """
    Threads のメディアコンテナが FINISHED になるまでポーリングして待機する。
    画像/動画の処理には数秒かかるため、この待機なしに publish すると失敗する。
    """
    for _ in range(max_wait):
        r = _threads_request(
            "get", f"{base_url}/{container_id}", access_token,
            params={"fields": "status,error_message"},
        )
        body = r.json()
        status = body.get("status")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise Exception(f"Threads メディア処理エラー: {body.get('error_message', '不明')}")
        logger.debug(f"Threads コンテナ待機中: id={container_id}, status={status}")
        time.sleep(1)
    raise Exception("Threads メディアコンテナのタイムアウト（30秒）")


def post_to_threads(text: str, image_urls: List[str], config: dict) -> dict:
    """Threads API (Meta) で投稿"""
    try:
        user_id      = config.get("user_id") or config.get("api_key")
        access_token = config.get("access_token_secret") or config.get("access_token")
        base_url     = "https://graph.threads.net/v1.0"

        if not user_id or not access_token:
            raise Exception("User ID または Access Token が設定されていません")

        def _to_public_url(url: str) -> str:
            return url if url.startswith("http") else BASE_URL + url

        if not image_urls:
            # ── テキスト投稿 ──
            resp = _threads_request(
                "post", f"{base_url}/{user_id}/threads", access_token,
                data={"media_type": "TEXT", "text": text},
            )
            container_id = resp.json()["id"]

        elif len(image_urls) == 1:
            # ── 画像1枚 ──
            resp = _threads_request(
                "post", f"{base_url}/{user_id}/threads", access_token,
                data={"media_type": "IMAGE", "image_url": _to_public_url(image_urls[0]), "text": text},
            )
            container_id = resp.json()["id"]
            # 画像コンテナの処理完了を待つ（必須）
            _wait_for_threads_container(base_url, container_id, access_token)

        else:
            # ── カルーセル（複数画像）──
            item_ids = []
            for url in image_urls[:10]:
                r = _threads_request(
                    "post", f"{base_url}/{user_id}/threads", access_token,
                    data={"media_type": "IMAGE", "image_url": _to_public_url(url), "is_carousel_item": "true"},
                )
                cid = r.json()["id"]
                _wait_for_threads_container(base_url, cid, access_token)
                item_ids.append(cid)

            resp = _threads_request(
                "post", f"{base_url}/{user_id}/threads", access_token,
                data={"media_type": "CAROUSEL", "children": ",".join(item_ids), "text": text},
            )
            container_id = resp.json()["id"]
            _wait_for_threads_container(base_url, container_id, access_token)

        # ── 公開 ──
        pub_resp = _threads_request(
            "post", f"{base_url}/{user_id}/threads_publish", access_token,
            data={"creation_id": container_id},
        )
        post_id = pub_resp.json()["id"]
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