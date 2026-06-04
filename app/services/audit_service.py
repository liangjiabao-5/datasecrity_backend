from flask import g, has_request_context, request

from app.common.utils import new_id
from app.extensions import SessionLocal
from app.models import AuditLog


def audit(action: str, object_type: str, object_id: str, before=None, after=None) -> None:
    session = SessionLocal()
    session.add(
        AuditLog(
            id=new_id("audit"),
            operator_id=getattr(g, "current_user_id", "system"),
            action=action,
            object_type=object_type,
            object_id=object_id,
            before_snapshot=before,
            after_snapshot=after,
            ip_address=request.remote_addr if has_request_context() else None,
        )
    )
