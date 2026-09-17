from dataclasses import dataclass
from hashlib import pbkdf2_hmac
from hmac import compare_digest
import hashlib
import hmac
import secrets
from threading import Lock

@dataclass(frozen=True)
class StaffUser:
    user_id: str
    name: str
    email: str
    password_hash: str

class AuthStore:
    def __init__(self, secret: str = "parking-garage-dev-secret") -> None:
        self.secret = secret.encode(); self.users: dict[str, StaffUser] = {}; self.sessions: dict[str, str] = {}; self._lock = Lock()
    def create_user(self, name: str, email: str, password: str) -> StaffUser:
        if len(password) < 8: raise ValueError("password must be at least 8 characters")
        email = email.strip().lower()
        with self._lock:
            if any(user.email == email for user in self.users.values()): raise ValueError("email is already registered")
            user = StaffUser(secrets.token_urlsafe(9), name.strip(), email, self._hash(password)); self.users[user.user_id] = user; return user
    def authenticate(self, email: str, password: str) -> StaffUser | None:
        email = email.strip().lower(); user = next((u for u in self.users.values() if u.email == email), None)
        return user if user and compare_digest(user.password_hash, self._hash(password, user.password_hash.split('$',1)[0])) else None
    def sign_in(self, user: StaffUser) -> str:
        payload = secrets.token_urlsafe(24); signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest(); self.sessions[payload] = user.user_id; return f"{payload}.{signature}"
    def user_from_cookie(self, cookie: str | None) -> StaffUser | None:
        if not cookie or '.' not in cookie: return None
        payload, signature = cookie.rsplit('.', 1); expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        if not compare_digest(signature, expected): return None
        user_id = self.sessions.get(payload); return self.users.get(user_id) if user_id else None
    def sign_out(self, cookie: str | None) -> None:
        if cookie and '.' in cookie: self.sessions.pop(cookie.rsplit('.', 1)[0], None)
    @staticmethod
    def _hash(password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(16); return f"{salt}${pbkdf2_hmac('sha256', password.encode(), salt.encode(), 120000).hex()}"
