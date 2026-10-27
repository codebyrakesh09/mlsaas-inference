import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Form
from fastapi.security import OAuth2PasswordBearer
import jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import OrganizationInvitation, OrganizationMembership, User
from ..security import hash_password, verify_password, create_token, decode_token


router = APIRouter(prefix='/auth', tags=['auth'])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/login')


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=401,
        detail='Invalid or expired access token',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        user_id = decode_token(token)
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user

class RegisterIn(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    invitation_token: str | None = Field(default=None, min_length=30, max_length=128)


@router.post('/register', status_code=201)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    email = str(data.email).lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(400, 'Email already registered')

    invitation = None
    if data.invitation_token:
        token_hash = hashlib.sha256(data.invitation_token.encode()).hexdigest()
        invitation = db.scalar(
            select(OrganizationInvitation).where(
                OrganizationInvitation.token_hash == token_hash,
                OrganizationInvitation.email == email,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.expires_at > datetime.now(timezone.utc),
            )
        )
        if invitation is None:
            raise HTTPException(status_code=400, detail='Invalid or expired invitation')
    
    first_user = db.scalar(select(func.count(User.id))) == 0
    user = User(name=data.name, 
                email=email,
                password_hash=hash_password(data.password),
                role='admin' if first_user else 'employee')
    
    db.add(user)
    if invitation is not None:
        db.flush()
        db.add(
            OrganizationMembership(
                organization_id=invitation.organization_id,
                user_id=user.id,
                role=invitation.role,
            )
        )
        invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return {
        'id': user.id,
        'email': user.email,
        'role': user.role,
        'organization_id': invitation.organization_id if invitation else None,
    }


@router.post("/login")
def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == username))

    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password")

    return {
        "access_token": create_token(user.id),
        "token_type": "bearer",
    }


@router.get('/me')
def read_current_user(user: User = Depends(get_current_user)):
    return {'id': user.id, 'name': user.name, 'email': user.email, 'role': user.role}

