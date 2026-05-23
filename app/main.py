"""
SNS自動投稿ツール - メインアプリケーション
起動: uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List
import uvicorn
import os
import shutil
import uuid

from .database import init_db, get_db
from .scheduler import init_scheduler, shutdown_scheduler
from .routers import posts, settings, history, auth

app = FastAPI(title="SNS自動投稿ツール", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(history.router, prefix="/api/history", tags=["history"])

# 必要なディレクトリを自動作成
os.makedirs("uploads", exist_ok=True)
os.makedirs("data", exist_ok=True)

# 静的ファイル
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.on_event("startup")
async def startup():
    init_db()
    init_scheduler()
    print("✅ サーバー起動完了: http://localhost:8000")


@app.on_event("shutdown")
async def shutdown():
    shutdown_scheduler()


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    """画像アップロード"""
    allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(400, "対応形式: JPEG, PNG, GIF, WEBP")

    ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = f"uploads/{filename}"

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"url": f"/uploads/{filename}", "filename": filename}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)