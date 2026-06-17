from app.common.exceptions import NotFoundError
from app.common.pagination import paginate_query
from app.common.utils import to_camel
from app.extensions import SessionLocal
from app.services.audit_service import audit
from app.services.project_service import get_project


LIST_TEXT_FIELDS = {
    "BusinessSystem": {"data_scopes": "、"},
    "ImportantDataAsset": {"processing_activity_types": ","},
    "CoreDataAsset": {"processing_activity_types": ","},
    "DataProcessingActivity": {"protection_measures": "、"},
}


def list_records(project_id: str, model) -> dict:
    get_project(project_id)
    session = SessionLocal()
    query = (
        session.query(model)
        .filter(model.project_id == project_id, model.deleted.is_(False))
        .order_by(model.created_at.asc())
    )
    return paginate_query(query, _serialize_record)


def create_record(project_id: str, model, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    record = model(project_id=project_id, **_pick(payload, model))
    session.add(record)
    audit(f"{model.__name__.upper()}_CREATE", model.__name__, record.id, after=_serialize_record(record))
    session.commit()
    return _serialize_record(record)


def update_record(project_id: str, model, record_id: str, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    record = _get_record(session, project_id, model, record_id)
    before = record.to_dict()
    for field, value in _pick(payload, model).items():
        setattr(record, field, value)
    audit(f"{model.__name__.upper()}_UPDATE", model.__name__, record.id, before=before, after=_serialize_record(record))
    session.commit()
    return _serialize_record(record)


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
    result = {}
    list_fields = LIST_TEXT_FIELDS.get(model.__name__, {})
    for key, value in payload.items():
        if key not in columns or key in ignored:
            continue
        if key in list_fields:
            value = _list_to_text(value, list_fields[key])
        result[key] = value
    return result


def _serialize_record(row) -> dict:
    data = row.to_dict()
    list_fields = LIST_TEXT_FIELDS.get(row.__class__.__name__, {})
    for field, separator in list_fields.items():
        data[to_camel(field)] = _text_to_list(getattr(row, field), separator)
    return data


def _list_to_text(value, separator: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return separator.join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _text_to_list(value, separator: str) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [item.strip() for item in str(value).split(separator) if item.strip()]
