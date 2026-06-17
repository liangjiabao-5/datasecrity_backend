# 项目基本信息与评估团队 Excel 导入导出功能修改说明

## 1. 修改背景

前端已将以下页面的导入导出从本地 JSON 兜底切换为后端 Excel 文件接口：

- 项目基本信息页：覆盖“项目基本情况”“被评估单位基本信息”和联系人信息明细。
- 评估方案页：只覆盖“评估团队”和“被评估方团队”两个团队标签页。

评估方案页中的“关注点”“差距项”不属于本次 Excel 导入导出范围，后端不要在本次团队导入中覆盖这两类数据。

## 2. 项目基本信息接口

### 2.1 导出模板

```http
GET /api/v1/projects/{projectId}/basic-info/export-template
```

返回：Excel 文件流。

建议文件名：`项目基本信息模板-{projectId}.xlsx`

Excel 包含以下 sheet：

| Sheet | 字段 |
| --- | --- |
| 项目基本情况 | 项目编号、项目名称、评估所依据的法律法规、评估所参考的标准规范、评估开始日期、评估结束日期 |
| 被评估单位基本信息 | 单位名称、邮政编码 |
| 联系人信息 | 姓名、所属部门、移动电话、职务/职称、办公电话、电子邮件 |

### 2.2 导入

```http
POST /api/v1/projects/{projectId}/basic-info/import
Content-Type: multipart/form-data

file=@basic-info.xlsx
```

后端解析 Excel 后返回结构化数据，前端会自动填充页面，但用户仍需点击“保存”正式生效。

返回示例：

```json
{
  "code": "SUCCESS",
  "data": {
    "projectNumber": "ENST-2024-001",
    "projectName": "XX银行数据安全评估项目",
    "laws": [{ "id": "law-1", "name": "网络安全法" }],
    "standards": [{ "id": "std-1", "name": "GB/T 35273" }],
    "assessmentPlan": {
      "startDate": "2026-06-01",
      "endDate": "2026-06-10"
    },
    "organization": {
      "name": "被评估单位",
      "postalCode": "310000"
    },
    "contacts": [
      {
        "id": "contact-1",
        "name": "张三",
        "department": "信息部",
        "mobile": "13800000000",
        "title": "经理",
        "phone": "0571-88888888",
        "email": "zhangsan@example.com"
      }
    ]
  }
}
```

字段说明：

| 前端字段 | Excel 列名 | 说明 |
| --- | --- | --- |
| `projectNumber` | 项目编号 | 文本，只读展示；如后端已有 `projectCode`，可映射为 `projectNumber` 返回 |
| `projectName` | 项目名称 | 文本 |
| `laws` | 评估所依据的法律法规 | 建议按名称匹配法规；多个值可用顿号、逗号、分号或换行分隔 |
| `standards` | 评估所参考的标准规范 | 建议按名称匹配标准；多个值可用顿号、逗号、分号或换行分隔 |
| `assessmentPlan.startDate` | 评估开始日期 | `YYYY-MM-DD` |
| `assessmentPlan.endDate` | 评估结束日期 | `YYYY-MM-DD` |
| `organization.name` | 单位名称 | 文本 |
| `organization.postalCode` | 邮政编码 | 文本 |
| `contacts[].name` | 姓名 | 文本 |
| `contacts[].department` | 所属部门 | 文本 |
| `contacts[].mobile` | 移动电话 | 文本 |
| `contacts[].title` | 职务/职称 | 文本 |
| `contacts[].phone` | 办公电话 | 文本 |
| `contacts[].email` | 电子邮件 | 文本 |

### 2.3 导出

```http
GET /api/v1/projects/{projectId}/basic-info/export
```

返回：Excel 文件流。

建议文件名：`项目基本信息-{projectId}.xlsx`

导出内容应来自后端当前持久化数据，sheet 和列顺序与模板一致。

## 3. 评估方案团队接口

### 3.1 导出模板

```http
GET /api/v1/projects/{projectId}/plan/team-export-template
```

返回：Excel 文件流。

建议文件名：`评估方案团队模板-{projectId}.xlsx`

Excel 只包含以下 sheet：

| Sheet | 字段 |
| --- | --- |
| 评估团队 | 姓名、单位、角色 |
| 被评估方团队 | 公司/部门、姓名、职位、联系方式 |

### 3.2 导入

```http
POST /api/v1/projects/{projectId}/plan/team-import
Content-Type: multipart/form-data

file=@plan-team.xlsx
```

后端解析 Excel 后写入或更新当前项目的两个团队列表，并返回导入后的团队数据。前端收到成功响应后会刷新页面列表。

返回示例：

```json
{
  "code": "SUCCESS",
  "data": {
    "assessmentTeam": [
      {
        "id": "assessment-member-1",
        "name": "李四",
        "organization": "评估机构",
        "role": "组长"
      }
    ],
    "clientTeam": [
      {
        "id": "client-member-1",
        "department": "信息部",
        "name": "王五",
        "position": "经理",
        "contact": "13900000000"
      }
    ]
  }
}
```

字段说明：

| 前端字段 | Excel 列名 | 说明 |
| --- | --- | --- |
| `assessmentTeam[].name` | 姓名 | 评估团队成员姓名 |
| `assessmentTeam[].organization` | 单位 | 评估团队成员所属单位 |
| `assessmentTeam[].role` | 角色 | 评估团队成员角色 |
| `clientTeam[].department` | 公司/部门 | 被评估方团队成员所属公司或部门 |
| `clientTeam[].name` | 姓名 | 被评估方团队成员姓名 |
| `clientTeam[].position` | 职位 | 被评估方团队成员职位 |
| `clientTeam[].contact` | 联系方式 | 电话、手机或其他联系方式 |

导入范围要求：

- 只处理 `assessmentTeam` 和 `clientTeam`。
- 不读取、不新增、不删除、不覆盖 `focusPoints`。
- 不读取、不新增、不删除、不覆盖 `gapItems`。

### 3.3 导出

```http
GET /api/v1/projects/{projectId}/plan/team-export
```

返回：Excel 文件流。

建议文件名：`评估方案团队-{projectId}.xlsx`

导出内容应来自后端当前持久化的评估团队和被评估方团队数据，sheet 和列顺序与模板一致。

## 4. 校验与错误返回建议

建议后端至少校验：

- 文件类型为 `.xlsx` 或 `.xls`。
- 必需 sheet 名存在。
- 表头名称与模板一致，避免用户上传旧模板或错文件。
- 日期字段可解析为 `YYYY-MM-DD`。
- 邮箱、电话、手机号可做宽松格式校验。
- 团队导入中空行应忽略；非空行建议至少要求姓名或部门等核心字段存在。

错误返回建议沿用通用接口结构：

```json
{
  "code": "IMPORT_VALIDATION_FAILED",
  "message": "导入文件存在格式错误",
  "data": {
    "errors": [
      {
        "sheetName": "评估团队",
        "rowNo": 2,
        "field": "姓名",
        "reason": "姓名不能为空"
      }
    ]
  }
}
```

前端当前会展示 `message`。如后端后续需要行级错误弹窗，可复用现场测评导入异常明细的展示方式扩展。

## 5. 前端已调整文件

- `src/views/workflow/BasicInfoView.vue`
- `src/views/workflow/AssessmentPlanView.vue`
- `src/api/basicInfoApi.ts`
- `src/api/planApi.ts`
- `src/utils/fileActions.ts`
- `src/utils/basicInfoExcelImportResult.ts`
- `src/utils/planTeamExcelImportResult.ts`

## 6. 联调检查点

1. 点击项目基本信息页“导出模板”，能下载包含 3 个 sheet 的 Excel。
2. 上传项目基本信息模板，页面能自动填充表单和联系人表格。
3. 点击项目基本信息页“导出”，能下载当前项目基本信息 Excel。
4. 点击评估方案页“导出模板”，能下载只包含 2 个团队 sheet 的 Excel。
5. 上传评估方案团队模板后，评估团队和被评估方团队列表刷新。
6. 上传评估方案团队模板不会改变关注点和差距项数据。
7. 导入错误时接口返回清晰 `message`，前端能展示失败原因。
