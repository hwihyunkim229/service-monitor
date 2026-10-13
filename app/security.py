from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

TEAM_LABELS = {
    "service_ops": "서비스운영",
    "quality": "품질",
    "logistics": "물류",
    "rnd": "연구개발",
    "cs": "CS",
}
ROLE_LABELS = {
    "admin": "시스템 관리자",
    "user": "사용자",
    "viewer": "뷰어",
}