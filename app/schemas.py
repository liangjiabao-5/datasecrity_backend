from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_name: str = Field(alias="projectName")
    project_code: str = Field(alias="projectCode")
    assessment_org: str = Field(alias="assessmentOrg")
    risk_matrix_id: str | None = Field(default=None, alias="riskMatrixId")
    assessment_template_id: str | None = Field(default=None, alias="assessmentTemplateId")
    system_type: str | None = Field(default=None, alias="systemType")
    harm_model_id: str | None = Field(default=None, alias="harmModelId")
    score_model_id: str | None = Field(default=None, alias="scoreModelId")
    description: str | None = None
