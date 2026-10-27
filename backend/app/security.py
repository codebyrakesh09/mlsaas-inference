import os
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt

SECRET_KEY = os.environ['SECRET_KEY']

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=8)
    return jwt.encode({'sub': str(user_id), 'exp': expires},
                      SECRET_KEY, algorithm='HS256')


def decode_token(token: str) -> int:
    payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
    subject = payload.get('sub')
    if not isinstance(subject, str):
        raise jwt.InvalidTokenError('Token subject is invalid')

    try:
        return int(subject)
    except ValueError as exc:
        raise jwt.InvalidTokenError('Token subject is invalid') from exc