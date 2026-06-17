from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from app.common.utils import json_value, new_id, to_camel, utcnow
from app.extensions import Base


class ModelMixin:
    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), default="default", nullable=False)
    created_by = Column(String(64), default="system", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_by = Column(String(64), default="system", nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        if not getattr(self, "id", None):
            self.id = new_id(getattr(self, "id_prefix", "id"))

    def to_dict(self, exclude: tuple[str, ...] = ("tenant_id", "deleted")) -> dict:
        data = {}
        for column in self.__mapper__.columns:
            if column.key in exclude:
                continue
            data[to_camel(column.key)] = json_value(getattr(self, column.key))
        return data


class Project(ModelMixin, Base):
    __tablename__ = "project"

    id = Column(String(64), primary_key=True, default=lambda: new_id("p"))
    project_code = Column(String(100), unique=True, nullable=False, index=True)
    project_name = Column(String(200), nullable=False)
    assessment_org = Column(String(200), nullable=False)
    status = Column(String(40), default="NOT_STARTED", nullable=False, index=True)
    risk_matrix_id = Column(String(64))
    assessment_template_id = Column(String(64))
    system_type = Column(String(60))
    harm_model_id = Column(String(64))
    score_model_id = Column(String(64))
    description = Column(Text)


class AssessmentTemplate(ModelMixin, Base):
    __tablename__ = "assessment_template"

    id = Column(String(64), primary_key=True, default=lambda: new_id("tpl"))
    template_name = Column(String(200), nullable=False)
    template_type = Column(String(60), default="NATIONAL")
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(40), default="ENABLED", nullable=False, index=True)
    item_count = Column(Integer, default=0, nullable=False)


class AssessmentTemplateItem(ModelMixin, Base):
    __tablename__ = "assessment_template_item"

    id = Column(String(64), primary_key=True, default=lambda: new_id("tpli"))
    template_id = Column(String(64), ForeignKey("assessment_template.id"), nullable=False, index=True)
    sheet_name = Column(String(100))
    category = Column(String(200))
    subcategory = Column(String(200))
    category_id = Column(String(500), index=True)
    item_code = Column(String(100))
    check_point = Column(Text)
    standard_item_id = Column(String(100))
    sort_order = Column(Integer, default=0)


class RemediationSuggestionTemplate(ModelMixin, Base):
    __tablename__ = "remediation_suggestion_template"

    id = Column(String(64), primary_key=True, default=lambda: new_id("remtpl"))
    suggestion_title = Column(String(200), nullable=False)
    risk_level = Column(String(40))
    risk_type = Column(String(80))
    suggestion_content = Column(Text)
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(40), default="ENABLED", nullable=False, index=True)


class ScoreModel(ModelMixin, Base):
    __tablename__ = "score_model"

    id = Column(String(64), primary_key=True, default=lambda: new_id("score"))
    model_name = Column(String(200), nullable=False)
    model_type = Column(String(60), default="PRESET")
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(40), default="ENABLED", nullable=False)
    result_scores = Column(JSON)


class ScoreModelRange(ModelMixin, Base):
    __tablename__ = "score_model_range"

    id = Column(String(64), primary_key=True, default=lambda: new_id("range"))
    score_model_id = Column(String(64), ForeignKey("score_model.id"), nullable=False, index=True)
    level = Column(String(40), nullable=False)
    min_score = Column(Float, nullable=False)
    max_score = Column(Float, nullable=False)
    include_min = Column(Boolean, default=True, nullable=False)
    include_max = Column(Boolean, default=False, nullable=False)


class HarmModel(ModelMixin, Base):
    __tablename__ = "harm_model"

    id = Column(String(64), primary_key=True, default=lambda: new_id("harm"))
    model_name = Column(String(200), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(40), default="ENABLED", nullable=False)
    description = Column(Text)
    rule_config = Column(JSON)


class HarmModelRule(ModelMixin, Base):
    __tablename__ = "harm_model_rule"

    id = Column(String(64), primary_key=True, default=lambda: new_id("harmrule"))
    harm_model_id = Column(String(64), ForeignKey("harm_model.id"), nullable=False, index=True)
    level = Column(String(40), nullable=False)
    description = Column(Text)
    impact_object = Column(String(200))
    example = Column(Text)
    judgement_steps = Column(JSON)
    sort_order = Column(Integer, default=0)


class RiskMatrix(ModelMixin, Base):
    __tablename__ = "risk_matrix"

    id = Column(String(64), primary_key=True, default=lambda: new_id("matrix"))
    matrix_name = Column(String(200), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(40), default="ENABLED", nullable=False)
    remark = Column(Text)
    matrix_json = Column(JSON)


class RiskSourceTemplate(ModelMixin, Base):
    __tablename__ = "risk_source_template"

    id = Column(String(64), primary_key=True, default=lambda: new_id("rstpl"))
    sheet_name = Column(String(100), nullable=False, index=True)
    category = Column(String(200), index=True)
    subcategory = Column(String(200), index=True)
    assessment_item = Column(Text)
    evaluation_record = Column(Text)
    evaluation_result = Column(String(40))
    risk_description = Column(Text)
    remediation_suggestion = Column(Text)
    risk_source_description = Column(Text)
    risk_source_type = Column(Text)
    risk_types = Column(JSON)
    sort_order = Column(Integer, default=0)


class ProjectAssessmentItem(ModelMixin, Base):
    __tablename__ = "project_assessment_item"

    id = Column(String(64), primary_key=True, default=lambda: new_id("pai"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    template_item_id = Column(String(64), ForeignKey("assessment_template_item.id"), nullable=False)
    item_code = Column(String(100))
    sheet_name = Column(String(100))
    category = Column(String(200))
    subcategory = Column(String(200))
    category_id = Column(String(500), index=True)
    category_path = Column(String(500))
    check_point = Column(Text)
    check_content = Column(Text)
    sort_order = Column(Integer, default=0)


class EvaluationRecord(ModelMixin, Base):
    __tablename__ = "evaluation_record"

    id = Column(String(64), primary_key=True, default=lambda: new_id("eval"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    item_id = Column(String(64), ForeignKey("project_assessment_item.id"), nullable=False, index=True)
    evaluation_result = Column(String(40))
    evaluation_record = Column(Text)
    manual_updated = Column(Boolean, default=False, nullable=False)


class ScoreCalculationRecord(ModelMixin, Base):
    __tablename__ = "score_calculation_record"

    id = Column(String(64), primary_key=True, default=lambda: new_id("scalc"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    score = Column(Float, nullable=False)
    possibility_level = Column(String(40), nullable=False)
    score_model_id = Column(String(64))
    score_model_version = Column(Integer)
    calculation_detail = Column(JSON)


class ProjectBasicInfo(ModelMixin, Base):
    __tablename__ = "project_basic_info"

    id = Column(String(64), primary_key=True, default=lambda: new_id("basic"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, unique=True)
    laws = Column(JSON)
    standards = Column(JSON)
    plan_start_date = Column(String(20))
    plan_end_date = Column(String(20))


class AssessedOrganization(ModelMixin, Base):
    __tablename__ = "assessed_organization"

    id = Column(String(64), primary_key=True, default=lambda: new_id("org"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, unique=True)
    name = Column(String(200))
    postal_code = Column(String(40))


class ProjectContact(ModelMixin, Base):
    __tablename__ = "project_contact"

    id = Column(String(64), primary_key=True, default=lambda: new_id("contact"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    name = Column(String(100))
    department = Column(String(100))
    mobile = Column(String(50))
    title = Column(String(100))
    phone = Column(String(50))
    email = Column(String(100))


class ProjectReference(ModelMixin, Base):
    __tablename__ = "project_reference"

    id = Column(String(64), primary_key=True, default=lambda: new_id("ref"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False)
    name = Column(String(500), nullable=False)
    save_to_knowledge = Column(Boolean, default=False, nullable=False)


class AssessmentTeamMember(ModelMixin, Base):
    __tablename__ = "assessment_team_member"

    id = Column(String(64), primary_key=True, default=lambda: new_id("atm"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    name = Column(String(100))
    organization = Column(String(200))
    role = Column(String(100))


class ClientTeamMember(ModelMixin, Base):
    __tablename__ = "client_team_member"

    id = Column(String(64), primary_key=True, default=lambda: new_id("ctm"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    department = Column(String(200))
    name = Column(String(100))
    position = Column(String(100))
    contact = Column(String(100))


class FocusPoint(ModelMixin, Base):
    __tablename__ = "focus_point"

    id = Column(String(64), primary_key=True, default=lambda: new_id("focus"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    name = Column(String(200))
    domain = Column(String(100))
    risk_level = Column(String(40))
    description = Column(Text)
    source = Column(String(40), default="MANUAL")


class GapItem(ModelMixin, Base):
    __tablename__ = "gap_item"

    id = Column(String(64), primary_key=True, default=lambda: new_id("gap"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    gap_item = Column(String(500))
    dimension = Column(String(100))
    last_year_score = Column(Float)
    current_year_score = Column(Float)
    improvement_suggestion = Column(Text)


class BusinessSystem(ModelMixin, Base):
    __tablename__ = "business_system"

    id = Column(String(64), primary_key=True, default=lambda: new_id("sys"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    system_name = Column(String(200))
    business_function = Column(Text)
    service_object = Column(String(200))
    user_scale = Column(String(100))
    coverage_area = Column(String(200))
    classified_protection_level = Column(String(40))
    related_departments = Column(Text)
    data_scopes = Column(Text)
    topology_file_id = Column(String(64))
    business_flow_file_id = Column(String(64))


class DataProcessorBasicSurvey(ModelMixin, Base):
    __tablename__ = "data_processor_basic_survey"

    id = Column(String(64), primary_key=True, default=lambda: new_id("dpb"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, unique=True)
    unit_name = Column(String(200))
    unified_social_credit_code = Column(String(100))
    office_address = Column(String(500))
    legal_representative = Column(String(100))
    staff_size = Column(String(100))
    business_scope = Column(Text)
    data_security_officer = Column(String(100))
    contact_info = Column(String(200))
    unit_nature = Column(String(100))
    specific_processor_type = Column(String(200))
    power_industry_category = Column(String(200))
    business_operation_area = Column(String(200))
    data_processing_location = Column(String(500))
    main_business_scope = Column(Text)
    business_scale = Column(String(200))
    administrative_license = Column(Text)


class DataAsset(ModelMixin, Base):
    __tablename__ = "data_asset"

    id = Column(String(64), primary_key=True, default=lambda: new_id("asset"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    business_system_id = Column(String(64))
    data_name = Column(String(200))
    data_form = Column(String(100))
    data_scope = Column(String(100))
    data_scale = Column(String(100))
    data_source = Column(String(200))
    storage_location = Column(Text)
    flow_description = Column(Text)
    classified = Column(Boolean)
    data_category = Column(String(200))
    data_level = Column(String(100))
    personal_info = Column(Boolean)


class PersonalInfoAsset(ModelMixin, Base):
    __tablename__ = "personal_info_asset"

    id = Column(String(64), primary_key=True, default=lambda: new_id("pi"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    data_name = Column(String(200))
    data_category = Column(String(200))
    category = Column(String(200))
    scale = Column(String(100))
    data_source = Column(String(200))
    business_flow = Column(Text)
    sensitivity = Column(String(100))
    flow_description = Column(Text)


class ImportantDataAsset(ModelMixin, Base):
    __tablename__ = "important_data_asset"

    id = Column(String(64), primary_key=True, default=lambda: new_id("important"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    data_name = Column(String(200))
    data_category = Column(String(200))
    category = Column(String(200))
    scale = Column(String(100))
    data_source = Column(String(200))
    business_flow = Column(Text)
    processing_activity_types = Column(Text)


class CoreDataAsset(ModelMixin, Base):
    __tablename__ = "core_data_asset"

    id = Column(String(64), primary_key=True, default=lambda: new_id("core"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    data_name = Column(String(200))
    data_category = Column(String(200))
    category = Column(String(200))
    scale = Column(String(100))
    data_source = Column(String(200))
    business_flow = Column(Text)
    processing_activity_types = Column(Text)


class DataProcessingActivity(ModelMixin, Base):
    __tablename__ = "data_processing_activity"

    id = Column(String(64), primary_key=True, default=lambda: new_id("activity"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    business_system_id = Column(String(64))
    activity_type = Column(String(40))
    scenario = Column(Text)
    method = Column(String(200))
    scale = Column(String(100))
    involves_personal_info = Column(Boolean)
    protection_measures = Column(Text)
    description = Column(Text)


class SecurityProtectionSurvey(ModelMixin, Base):
    __tablename__ = "security_protection_survey"

    id = Column(String(64), primary_key=True, default=lambda: new_id("sec"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, unique=True)
    compliance_assessment_status = Column(Text)
    data_security_management = Column(Text)
    network_security_devices_and_policies = Column(Text)
    identity_authentication_and_access_control = Column(Text)
    vulnerability_management = Column(Text)
    remote_management_software = Column(Text)
    account_password_management = Column(Text)
    security_technology_application = Column(Text)
    is_power_monitoring_system = Column(String(20))
    production_control_area_protection = Column(Text)
    security_access_area_setup = Column(Text)
    power_monitoring_dedicated_network = Column(Text)
    zone_isolation_device_usage = Column(Text)
    wide_area_network_connection_security = Column(Text)
    power_dispatch_authentication = Column(Text)
    network_service_security_control = Column(Text)
    security_access_area_security_control = Column(Text)
    zone_boundary_protection = Column(Text)
    product_security_reliability = Column(Text)
    operator_security_monitoring_warning = Column(Text)
    security_incidents_and_threats = Column(Text)
    detected_threats = Column(Text)
    public_threat_alerts = Column(Text)
    other_security_threats = Column(Text)


class ProcessingActivitySurvey(ModelMixin, Base):
    __tablename__ = "processing_activity_survey"

    id = Column(String(64), primary_key=True, default=lambda: new_id("pas"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, unique=True)
    involved_activities = Column(Text)
    collection_channels = Column(Text)
    collection_method = Column(Text)
    collection_data_scope = Column(Text)
    collection_purpose = Column(Text)
    collection_frequency = Column(Text)
    collection_external_sources = Column(Text)
    collection_contracts = Column(Text)
    collection_related_systems = Column(Text)
    collection_public_device_usage = Column(Text)
    storage_method = Column(Text)
    data_center = Column(Text)
    storage_system = Column(Text)
    external_storage_provider = Column(Text)
    storage_location = Column(Text)
    storage_duration = Column(Text)
    backup_redundancy_strategy = Column(Text)
    online_channel = Column(Text)
    offline_transfer = Column(Text)
    transfer_protocol = Column(Text)
    data_interface = Column(Text)
    use_purpose = Column(Text)
    use_method = Column(Text)
    use_scope = Column(Text)
    use_scenario = Column(Text)
    algorithm_rules = Column(Text)
    processing_details = Column(Text)
    algorithm_recommendation_service = Column(Text)
    entrusted_or_joint_processing = Column(Text)
    provide_purpose = Column(Text)
    provide_method = Column(Text)
    provide_scope = Column(Text)
    data_recipients = Column(Text)
    provide_contracts = Column(Text)
    provided_personal_info_and_important_data = Column(Text)
    public_purpose = Column(Text)
    public_method = Column(Text)
    public_scope = Column(Text)
    public_audience_size = Column(Text)
    public_data_types = Column(Text)
    public_data_scale = Column(Text)
    deletion_scenarios = Column(Text)
    deletion_method = Column(Text)
    data_archive = Column(Text)
    media_destruction = Column(Text)
    cross_border_presence = Column(Text)
    cross_border_description = Column(Text)


class ProjectRiskSummaryRecord(ModelMixin, Base):
    __tablename__ = "project_risk_summary_record"

    id = Column(String(64), primary_key=True, default=lambda: new_id("riskrec"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    evaluation_item_id = Column(String(64), ForeignKey("project_assessment_item.id"), nullable=False, index=True)
    source_item_code = Column(String(100))
    assessment_category = Column(String(200))
    assessment_subcategory = Column(String(200))
    check_point = Column(Text)
    evaluation_result = Column(String(40))
    evaluation_record = Column(Text)
    risk_types = Column(JSON)
    risk_description = Column(Text)
    risk_source_description = Column(Text)
    related_data = Column(Text)
    related_activities = Column(JSON)
    harm_level = Column(String(40))
    harm_description = Column(Text)
    harm_impact_object = Column(String(200))
    harm_example = Column(Text)
    harm_analysis_trace = Column(JSON)
    harm_analysis_confidence = Column(Float)
    harm_analysis_input_hash = Column(String(64))
    possibility_level = Column(String(40))
    risk_level = Column(String(40))
    remediation_suggestion = Column(Text)
    manual_adjusted = Column(Boolean, default=False, nullable=False)
    current = Column(Boolean, default=True, nullable=False, index=True)


class FileObject(ModelMixin, Base):
    __tablename__ = "file_object"

    id = Column(String(64), primary_key=True, default=lambda: new_id("file"))
    project_id = Column(String(64), ForeignKey("project.id"), index=True)
    file_name = Column(String(300), nullable=False)
    object_key = Column(String(500), nullable=False)
    storage_provider = Column(String(40), default="LOCAL", nullable=False)
    bucket_name = Column(String(200))
    content_type = Column(String(100))
    file_size = Column(BigInteger, default=0, nullable=False)
    biz_type = Column(String(80))


class ReportRecord(ModelMixin, Base):
    __tablename__ = "report_record"

    id = Column(String(64), primary_key=True, default=lambda: new_id("report"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    report_name = Column(String(200), nullable=False)
    report_type = Column(String(80), default="DATA_SECURITY_RISK_ASSESSMENT", nullable=False)
    status = Column(String(40), default="PENDING", nullable=False, index=True)
    file_id = Column(String(64), ForeignKey("file_object.id"))
    generated_at = Column(DateTime)
    error_message = Column(Text)
    selected_sections = Column(JSON)


class ReportTask(ModelMixin, Base):
    __tablename__ = "report_task"

    id = Column(String(64), primary_key=True, default=lambda: new_id("task"))
    project_id = Column(String(64), ForeignKey("project.id"), nullable=False, index=True)
    report_id = Column(String(64), ForeignKey("report_record.id"), index=True)
    task_type = Column(String(80), default="REPORT_GENERATE", nullable=False)
    status = Column(String(40), default="PENDING", nullable=False, index=True)
    error_message = Column(Text)
    result = Column(JSON)


class AuditLog(ModelMixin, Base):
    __tablename__ = "audit_log"

    id = Column(String(64), primary_key=True, default=lambda: new_id("audit"))
    operator_id = Column(String(64))
    action = Column(String(100))
    object_type = Column(String(100))
    object_id = Column(String(64))
    before_snapshot = Column(JSON)
    after_snapshot = Column(JSON)
    ip_address = Column(String(100))
