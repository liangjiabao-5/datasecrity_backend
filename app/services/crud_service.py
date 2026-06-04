from app.common.exceptions import NotFoundError
from app.common.pagination import paginate_query
from app.extensions import SessionLocal
from app.services.audit_service import audit
from app.services.project_service import get_project


def list_records(project_id: str, model) -> dict:
    get_project(project_id)
    session = SessionLocal()
    query = (
        session.query(model)
        .filter(model.project_id == project_id, model.deleted.is_(False))
        .order_by(model.created_at.asc())
    )
    return paginate_query(query, lambda row: row.to_dict())


def create_record(project_id: str, model, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    record = model(project_id=project_id, **_pick(payload, model))
    session.add(record)
    audit(f"{model.__name__.upper()}_CREATE", model.__name__, record.id, after=record.to_dict())
    session.commit()
    return record.to_dict()


def update_record(project_id: str, model, record_id: str, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    record = _get_record(session, project_id, model, record_id)
    before = record.to_dict()
    for field, value in _pick(payload, model).items():
        setattr(record, field, value)
    audit(f"{model.__name__.upper()}_UPDATE", model.__name__, record.id, before=before, after=record.to_dict())
    session.commit()
    return record.to_dict()


def delete_record(project_id: str, model, record_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    record = _get_record(session, project_id, model, record_id)
    before = record.to_dict()
    record.deleted = True
    audit(f"{model.__name__.upper()}_DELETE", model.__name__, record.id, before=before, after={"deleted": True})
    session.commit()
    return {"id": record.id}


def _get_record(session, project_id: str, model, record_id: str):
    record = (
        session.query(model)
        .filter(model.id == record_id, model.project_id == project_id, model.deleted.is_(False))
        .first()
    )
    if not record:
        raise NotFoundError(f"{model.__name__} not found.")
    return record


def _pick(payload: dict, model) -> dict:
    columns = {column.key for column in model.__mapper__.columns}
    ignored = {"id", "project_id", "created_at", "created_by", "updated_at", "updated_by", "deleted", "tenant_id"}
    return {key: value for key, value in payload.items() if key in columns and key not in ignored}
