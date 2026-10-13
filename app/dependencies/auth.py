from fastapi import Request, HTTPException, status


def login_required(request: Request):
    if getattr(request.state, "slack_token_authenticated", False):
        return
    if "user" not in request.session:
        if request.method != "GET":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="로그인 세션이 만료되었습니다. 다시 로그인해 주세요.",
            )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"}
        )