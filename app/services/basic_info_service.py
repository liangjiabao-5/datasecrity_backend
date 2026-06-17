from app.common.utils import require_fields
from app.extensions import SessionLocal
from app.models import AssessedOrganization, ProjectBasicInfo, ProjectContact, ProjectReference
from app.services.audit_service import audit
from app.services.project_service import get_project


def get_basic_info(project_id: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    basic = session.query(ProjectBasicInfo).filter_by(project_id=project_id, deleted=False).first()
    org = session.query(AssessedOrganization).filter_by(project_id=project_id, deleted=False).first()
    contacts = (
        session.query(ProjectContact)
        .filter_by(project_id=project_id, deleted=False)
        .order_by(ProjectContact.created_at.asc())
        .all()
    )
    return {
        "projectId": project_id,
        "projectNumber": project.project_code,
        "projectName": project.project_name,
        "laws": basic.laws if basic else [],
        "standards": basic.standards if basic else [],
        "assessmentPlan": {
            "startDate": basic.plan_start_date if basic else None,
            "endDate": basic.plan_end_date if basic else None,
        },
        "organization": {
            "name": org.name if org else None,
            "postalCode": org.postal_code if org else None,
        },
        "contacts": [contact.to_dict() for contact in contacts],
    }


def save_basic_info(project_id: str, payload: dict) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    if payload.get("project_name") not in (None, ""):
        project.project_name = payload["project_name"]

    basic = session.query(ProjectBasicInfo).filter_by(project_id=project_id, deleted=False).first()
    if not basic:
        basic = ProjectBasicInfo(project_id=project_id)
        session.add(basic)

    plan = payload.get("assessment_plan") or {}
    basic.laws = payload.get("laws") or []
    basic.standards = payload.get("standards") or []
    basic.plan_start_date = plan.get("start_date")
    basic.plan_end_date = plan.get("end_date")

    org_payload = payload.get("organization") or {}
    org = session.query(AssessedOrganization).filter_by(project_id=project_id, deleted=False).first()
    if not org:
        org = AssessedOrganization(project_id=project_id)
        session.add(org)
    for field in ["name", "postal_code"]:
        if field in org_payload:
            setattr(org, field, org_payload.get(field))

    session.query(ProjectContact).filter_by(project_id=project_id, deleted=False).update({"deleted": True})
    for item in payload.get("contacts") or []:
        session.add(ProjectContact(project_id=project_id, **_pick(item, ProjectContact)))

    audit("BASIC_INFO_SAVE", "Project", project_id, after={"projectId": project_id})
    session.commit()
    return get_basic_info(project_id)


def create_reference(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    require_fields(payload, ["type", "name"])
    session = SessionLocal()
    ref = ProjectReference(
        project_id=project_id,
        type=payload["type"],
        name=payload["name"],
        save_to_knowledge=bool(payload.get("save_to_knowledge", False)),
    )
    session.add(ref)
    audit("PROJECT_REFERENCE_CREATE", "ProjectReference", ref.id, after=ref.to_dict())
    session.commit()
    return ref.to_dict()


def _pick(payload: dict, model) -> dict:
    columns = {column.key for column in model.__mapper__.columns}
    ignored = {"id", "project_id", "created_at", "created_by", "updated_at", "updated_by", "deleted", "tenant_id"}
    return {key: value for key, value in payload.items() if key in columns and key not in ignored}
