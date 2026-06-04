def unwrap(response):
    assert response.status_code < 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "SUCCESS"
    return payload["data"]


def test_assessment_template_item_crud(client):
    template = unwrap(
        client.post(
            "/api/v1/knowledge/assessment-templates",
            json={
                "templateName": "自定义模板",
                "templateType": "CUSTOM",
                "status": "ENABLED",
                "items": [
                    {
                        "sheetName": "数据安全管理",
                        "category": "制度",
                        "subcategory": "策略",
                        "itemCode": "1",
                        "checkPoint": "检查总体策略",
                        "standardItemId": "custom-1",
                    }
                ],
            },
        )
    )
    assert template["itemCount"] == 1
    template_id = template["id"]

    items = unwrap(client.get(f"/api/v1/knowledge/assessment-templates/{template_id}/items?pageNo=1&pageSize=10"))
    item_id = items["list"][0]["id"]

    updated = unwrap(
        client.put(
            f"/api/v1/knowledge/assessment-templates/{template_id}/items/{item_id}",
            json={"checkPoint": "更新后的检查点"},
        )
    )
    assert updated["checkPoint"] == "更新后的检查点"

    exported = unwrap(client.get(f"/api/v1/knowledge/assessment-templates/{template_id}/export"))
    assert exported["exportFormat"] == "JSON"
    assert len(exported["items"]) == 1


def test_score_model_validation_crud_and_invalid_range(client):
    valid_payload = {
        "modelName": "测试评分模型",
        "modelType": "CUSTOM",
        "resultScores": {
            "COMPLIANT": 1,
            "PARTIAL": 0.5,
            "NON_COMPLIANT": 0,
            "NOT_APPLICABLE": None,
        },
        "possibilityRanges": [
            {"level": "HIGH", "min": 0, "max": 60, "includeMin": True, "includeMax": False},
            {"level": "MEDIUM", "min": 60, "max": 80, "includeMin": True, "includeMax": False},
            {"level": "LOW", "min": 80, "max": 100, "includeMin": True, "includeMax": True},
        ],
    }
    created = unwrap(client.post("/api/v1/knowledge/score-models", json=valid_payload))
    assert created["modelName"] == "测试评分模型"
    assert len(created["possibilityRanges"]) == 3

    listed = unwrap(client.get("/api/v1/knowledge/score-models?pageNo=1&pageSize=10&keyword=测试"))
    assert listed["total"] == 1
    assert len(listed["list"][0]["possibilityRanges"]) == 3

    model_id = created["id"]
    assert unwrap(client.post(f"/api/v1/knowledge/score-models/{model_id}/validate", json={}))["valid"] is True

    bad = client.post(
        "/api/v1/knowledge/score-models/validate",
        json={
            "possibilityRanges": [
                {"level": "HIGH", "min": 0, "max": 50, "includeMin": True, "includeMax": False},
                {"level": "LOW", "min": 60, "max": 100, "includeMin": True, "includeMax": True},
            ]
        },
    )
    assert bad.status_code == 400
    assert bad.get_json()["code"] == "SCORE_RANGE_GAP"


def test_harm_model_rules_risk_matrix_and_generic_knowledge(client):
    harm_models = unwrap(client.get("/api/v1/knowledge/harm-models?pageNo=1&pageSize=20"))
    names = {item["modelName"] for item in harm_models["list"]}
    assert "默认分析模型" in names
    assert "电力行业分析模型" in names
    electric_model_id = next(item["id"] for item in harm_models["list"] if item["modelName"] == "电力行业分析模型")
    electric_model = unwrap(client.get(f"/api/v1/knowledge/harm-models/{electric_model_id}"))
    assert electric_model["ruleConfig"]["protection_level_matrix"]["PUBLIC_INTEREST"]["SERIOUS"] == 3
    assert electric_model["ruleConfig"]["harm_level_by_protection_level"]["3"] == "RELATIVELY_HIGH"

    harm = unwrap(
        client.post(
            "/api/v1/knowledge/harm-models",
            json={
                "modelName": "测试危害模型",
                "description": "harm",
                "ruleConfig": electric_model["ruleConfig"],
                "rules": [{"level": "HIGH", "description": "high", "impactObject": "组织"}],
            },
        )
    )
    assert harm["rules"][0]["level"] == "HIGH"
    assert harm["ruleConfig"]["system_categories"]["MANAGEMENT_INFO_SYSTEM"]
    rule = unwrap(
        client.post(
            f"/api/v1/knowledge/harm-models/{harm['id']}/rules",
            json={"level": "LOW", "description": "low", "sortOrder": 2},
        )
    )
    assert rule["level"] == "LOW"

    matrix = unwrap(
        client.post(
            "/api/v1/knowledge/risk-matrices",
            json={
                "matrixName": "测试矩阵",
                "remark": "列表备注",
                "matrixJson": {
                    "HIGH": {
                        "VERY_HIGH": "MAJOR",
                        "HIGH": "MAJOR",
                        "RELATIVELY_HIGH": "MEDIUM",
                        "MEDIUM": "LOW",
                        "LOW": "SLIGHT",
                    },
                    "MEDIUM": {
                        "VERY_HIGH": "MAJOR",
                        "HIGH": "HIGH",
                        "RELATIVELY_HIGH": "MEDIUM",
                        "MEDIUM": "LOW",
                        "LOW": "SLIGHT",
                    },
                    "LOW": {
                        "VERY_HIGH": "MEDIUM",
                        "HIGH": "MEDIUM",
                        "RELATIVELY_HIGH": "LOW",
                        "MEDIUM": "SLIGHT",
                        "LOW": "SLIGHT",
                    },
                },
            },
        )
    )
    assert matrix["remark"] == "列表备注"
    assert matrix["matrixJson"]["HIGH"]["VERY_HIGH"] == "MAJOR"
    assert unwrap(client.post(f"/api/v1/knowledge/risk-matrices/{matrix['id']}/validate", json={}))["valid"] is True

    suggestion = unwrap(
        client.post(
            "/api/v1/knowledge/remediation-suggestions",
            json={
                "suggestionTitle": "测试处置建议",
                "riskLevel": "HIGH",
                "riskType": "DATA_LEAKAGE",
                "suggestionContent": "最小权限",
            },
        )
    )
    assert suggestion["suggestionTitle"] == "测试处置建议"

    removed = client.get("/api/v1/knowledge/system-templates?pageNo=1&pageSize=10")
    assert removed.status_code == 404
