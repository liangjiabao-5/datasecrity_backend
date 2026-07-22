from sqlalchemy import or_

from app.common.exceptions import NotFoundError
from app.common.pagination import paginate_query
from app.common.utils import require_fields
from app.extensions import SessionLocal
from app.models import AssessmentTemplateItem, Project, ProjectAssessmentItem
from app.services.audit_service import audit


STATUS_NAMES = {
    "NOT_STARTED": "待开始",
    "IN_PROGRESS": "进行中",
    "COMPLETED": "已完成",
    "ARCHIVED": "已归档",
}


PROJECT_FIELDS = [
    "project_name",
    "project_code",
    "assessment_org",
    "risk_matrix_id",
    "assessment_template_id",
    "system_type",
    "harm_model_id",
    "score_model_id",
    "description",
]


def serialize_project(project: Project) -> dict:
    data = project.to_dict()
    data["statusName"] = STATUS_NAMES.get(project.status, project.status)
    return data


def get_project(project_id: str) -> Project:
    session = SessionLocal()
    project = session.get(Project, project_id)
    if not project or project.deleted:
        raise NotFoundError("Project not found.")
    return project


def list_projects(args) -> dict:
    session = SessionLocal()
    query = session.query(Project).filter(Project.deleted.is_(False)).order_by(Project.created_at.desc())
    status = args.get("status")
    keyword = args.get("keyword")
    if status:
        query = query.filter(Project.status == status)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                Project.project_name.like(like),
                Project.project_code.like(like),
                Project.assessment_org.like(like),
            )
        )
    return paginate_query(query, serialize_project)


def statistics() -> dict:
    session = SessionLocal()
    base = session.query(Project).filter(Project.deleted.is_(False))
    return {
        "all": base.count(),
        "notStarted": base.filter(Project.status == "NOT_STARTED").count(),
        "inProgress": base.filter(Project.status == "IN_PROGRESS").count(),
        "completed": base.filter(Project.status == "COMPLETED").count(),
    }


def create_project(payload: dict) -> dict:
    require_fields(payload, ["project_name", "project_code", "assessment_org"])
    session = SessionLocal()

    project = Project(status="NOT_STARTED")
    for field in PROJECT_FIELDS:
        setattr(project, field, payload.get(field))
    session.add(project)
    audit("PROJECT_CREATE", "Project", project.id, after=serialize_project(project))
    session.commit()
    return {"projectId": project.id}


def update_project(project_id: str, payload: dict) -> dict:
    session = SessionLocal()
    project = get_project(project_id)
    before = serialize_project(project)

    for field in PROJECT_FIELDS:
        if field in payload:
            setattr(project, field, payload.get(field))
    audit("PROJECT_UPDATE", "Project", project.id, before=before, after=serialize_project(project))
    session.commit()
    return serialize_project(project)


def delete_project(project_id: str) -> dict:
    session = SessionLocal()
    project = get_project(project_id)
    before = serialize_project(project)
    project.deleted = True
    audit("PROJECT_DELETE", "Project", project.id, before=before, after={"deleted": True})
    session.commit()
    return {"projectId": project.id}


def start_project(project_id: str) -> dict:
    session = SessionLocal()
    project = get_project(project_id)
    project.status = "IN_PROGRESS"

    existing_count = (
        session.query(ProjectAssessmentItem)
        .filter(ProjectAssessmentItem.project_id == project.id, ProjectAssessmentItem.deleted.is_(False))
        .count()
    )
    generated_count = 0
    if existing_count == 0:
        template_items = (
            session.query(AssessmentTemplateItem)
            .filter(
                AssessmentTemplateItem.template_id == project.assessment_template_id,
                AssessmentTemplateItem.deleted.is_(False),
            )
            .order_by(AssessmentTemplateItem.sort_order.asc())
            .all()
        )
        for item in template_items:
            session.add(
                ProjectAssessmentItem(
                    project_id=project.id,
                    template_item_id=item.id,
                    item_code=item.item_code,
                    assessment_item_id=item.assessment_item_id,
                    sheet_name=item.sheet_name,
                    category=item.category,
                    subcategory=item.subcategory,
                    category_id=item.category_id,
                    category_path=item.category_id,
                    check_point=item.check_point,
                    check_content=item.check_point,
                    sort_order=item.sort_order,
                )
            )
            generated_count += 1

    audit(
        "PROJECT_START",
        "Project",
        project.id,
        after={"status": project.status, "generatedItemCount": generated_count},
    )
    session.commit()
    return {
        "projectId": project.id,
        "status": project.status,
        "generatedItemCount": generated_count,
        "existingItemCount": existing_count,
    }
