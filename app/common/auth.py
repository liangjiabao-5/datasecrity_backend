from flask import Flask, g, request

from app.common.exceptions import BusinessError


def init_auth(app: Flask) -> None:
    @app.before_request
    def attach_current_user() -> None:
        if request.method == "OPTIONS" or request.path == "/health":
            return

        authorization = request.headers.get("Authorization", "")
        if app.config.get("AUTH_REQUIRED") and not authorization.startswith("Bearer "):
            raise BusinessError("UNAUTHORIZED", "Authentication is required.", 401)

        token = authorization.removeprefix("Bearer ").strip()
        g.current_user_id = token or "dev-user"
