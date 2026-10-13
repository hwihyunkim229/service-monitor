import os

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_serializer = URLSafeTimedSerializer(
    os.getenv("SESSION_SECRET", "service-monitor-local-development-only"),
    salt="cx-slack-import",
)


def create_slack_token(username: str) -> str:
    return _serializer.dumps({"user": username})


def verify_slack_token(token: str, max_age: int = 3600) -> bool:
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("user"))
