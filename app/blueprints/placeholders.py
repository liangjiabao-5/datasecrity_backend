from flask import Blueprint, request, send_file

from app.common.response import success
from app.common.utils import request_json
from app.services import file_service


bp = Blueprint("placeholders", __name__)


@bp.post("/files")
def upload_file():
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(file_service.save_upload(uploaded, biz_type=request.form.get("bizType") or "GENERAL"))


@bp.get("/files/<file_id>/download")
def download_file(file_id: str):
    return _send_file(file_service.get_file(file_id))


@bp.post("/llm/case-match")
@bp.post("/llm/evaluation-record/suggest")
@bp.post("/llm/remediation/suggest")
@bp.post("/llm/report-section/draft")
def llm_placeholder():
    return success({"status": "PLACEHOLDER", "message": "LLM integration is not implemented in phase one."})


def _send_file(file):
    return send_file(
        file_service.file_stream(file),
        as_attachment=True,
        download_name=file.file_name,
        mimetype=file.content_type or "application/octet-stream",
    )
