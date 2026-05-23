"""
認証モジュール (JWT + bcrypt)
"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from fastapi import Form
from app.database import get_db, User

router = APIRouter()

# ── 設定 ──
SECRET_KEY = "your-secret-key-change-this-in-production-please"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7日間

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ログインが必要です",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not credentials:
        raise exc
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise exc

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise exc
    return user


# ───────────────────────────────
# ここからルーター
# ───────────────────────────────

@router.post("/register")
def signup(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "ユーザー名は既に存在します")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "メールアドレスは既に登録されています")

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": "ユーザー登録完了", "user_id": user.id}


@router.post("/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(400, "ユーザー名またはパスワードが違います")

    token = create_access_token(user.id, user.username)
    return {"access_token": token, "token_type": "bearer"}