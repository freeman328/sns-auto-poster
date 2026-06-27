"""
認証 API ルーター
"""

from fastapi import APIRouter, Depends, HTTPException, status, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db, User, Settings
from ..auth import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter()


class RegisterBody(BaseModel):
    username: str
    email: str
    password: str


# 【修正9】重複していた import を整理（ファイル先頭に1回だけ宣言）

@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    """新規ユーザー登録（最初の1人は自動で管理者）"""
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "このユーザー名はすでに使われています")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "このメールアドレスはすでに使われています")
    if len(body.password) < 6:
        raise HTTPException(400, "パスワードは6文字以上にしてください")

    is_first = db.query(User).count() == 0
    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    for platform in ["x", "facebook", "threads"]:
        db.add(Settings(platform=platform, config={}, is_connected=False, user_id=user.id))
    db.commit()

    token = create_access_token(user.id, user.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "is_admin": user.is_admin},
    }


@router.post("/login")
def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """ログイン"""
    user = db.query(User).filter(
        (User.username == username) | (User.email == username)
    ).first()

    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが違います",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="このアカウントは無効です",
        )

    token = create_access_token(user.id, user.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "is_admin": getattr(user, "is_admin", False),
        },
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
    }


@router.get("/users")
def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(403, "管理者のみアクセスできます")
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
        }
        for u in users
    ]


@router.post("/users")
def create_user(
    body: RegisterBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(403, "管理者のみアクセスできます")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "このユーザー名はすでに使われています")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    for platform in ["x", "facebook", "threads"]:
        db.add(Settings(platform=platform, config={}, is_connected=False, user_id=user.id))
    db.commit()

    return {"message": f"ユーザー {body.username} を作成しました", "id": user.id}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(403, "管理者のみアクセスできます")
    if user_id == current_user.id:
        raise HTTPException(400, "自分自身は削除できません")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "ユーザーが見つかりません")
    user.is_active = False
    db.commit()
    return {"message": "削除しました"}