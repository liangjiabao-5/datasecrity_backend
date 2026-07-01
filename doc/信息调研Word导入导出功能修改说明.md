# 信息调研 Word 导入导出功能修改说明

更新时间：2026-06-29

## 修改目标

信息调研页面需要使用 `附录A（资料性）调研表格.docx` 作为导入、导出模板。后端已新增 Word 文件导入、导出和模板下载接口，前端需要在信息调研页面增加对应按钮，并在导入成功后刷新页面中五个标签页的数据。

## 新增接口

### 1. 下载导入模板

```http
GET /api/v1/projects/{projectId}/survey/export-template
```

响应：直接下载 `.docx` 文件。

用途：前端“下载模板”按钮使用。文件内容为后端 `doc/附录A（资料性）调研表格.docx`。

### 2. 导入信息调研 Word

```http
POST /api/v1/projects/{projectId}/survey/import
Content-Type: multipart/form-data
```

请求字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file | File | 用户上传的 `.docx` 文件 |

成功响应示例：

```json
{
  "code": "SUCCESS",
  "message": "Operation succeeded.",
  "data": {
    "dataProcessorBasic": {},
    "businessSystem": {},
    "processingActivity": {},
    "securityProtection": {},
    "counts": {
      "dataAssets": 1,
      "personalInfo": 1,
      "importantData": 1,
      "coreData": 1
    }
  }
}
```

导入覆盖规则：

- 数据处理者基本情况：覆盖当前项目的单表问卷。
- 业务和信息系统：覆盖当前项目第一条业务系统记录；如果不存在则创建。
- 数据资产、个人信息、重要数据、核心数据：覆盖当前页面已有列表，后端会软删除旧记录并创建上传文档中的新记录；如果导入模板对应表格为空，或首条数据行第一列填写“无”“不涉及”，则清空平台已有列表。
- 数据处理活动：按模板 A.7 全量覆盖对应问卷字段；导入模板中为空的字段会清空平台旧值。
- 安全防护措施：按模板 A.8 全量覆盖对应问卷字段；导入模板中为空的字段会清空平台旧值。

### 2026-06-29 导入规则补充

本次调整不改变接口地址、请求方式和响应结构，但导入后的页面状态需要以前端重新拉取的后端数据为准。

1. 空表格覆盖旧数据
   - 导入 Word 中 A.3、A.4、A.5、A.6 表格没有有效记录时，平台中原有数据资产、个人信息、重要数据、核心数据列表会被清空。
   - A.7 数据处理活动、A.8 安全防护措施也改为全量覆盖。Word 里为空的字段会覆盖为空，不再保留平台旧值。

2. 数据资产“无/不涉及”写法
   - 当 A.3 至 A.6 任一表格没有数据时，可以在第一条数据行的第一列填写“无”或“不涉及”。
   - 后端判断前会先去掉表单符号、标点和空格，因此“【无】”“（ 不 涉 及 。）”“□ 无”都会按无数据处理。
   - 前端导入成功后应按 `counts` 和列表接口结果刷新页面，不要把“无”“不涉及”显示成一条数据资产记录。

3. 数据处理活动勾选规则
   - `processingActivity.involvedActivities` 是导入后的唯一可信勾选结果。
   - 后端会按每类处理活动的全部属性判断：某类活动所有已填写属性都只是“无”或“不涉及”时，不返回该活动类型；只要该类任一属性存在真实业务内容，才返回对应活动类型。
   - 判断前同样会去掉表单符号、标点和空格，避免因为“（无）”“不 涉 及”等写法误勾选。
   - 前端刷新页面时应直接使用 `GET /survey/processing-activity-survey` 返回的 `involvedActivities` 控制勾选项和禁用态，不要再根据文本框是否为空自行推断。

4. 安全防护措施第 9 项拆解
   - A.8 安全防护措施导入按序号映射到 `securityProtection` 字段保存，不再依赖“上传文件内容是否与后端模板不同”判断。
   - 如果题目和填写内容在同一个单元格内，后端会先剥离题目提示文本，再把剩余内容保存到 `security_protection_survey` 对应字段。
   - A.8 “9. 电力监控系统防护措施要点”已支持按子项拆解导入。
   - 第 9 项首行“是否为电力监控系统”会导入为 `isPowerMonitoringSystem`，支持 `YES` / `NO`。
   - 当首行填写或勾选为“否”时，后端只回填 `isPowerMonitoringSystem: "NO"`，其余 11 个电力监控系统子项字段全部置空；即使 Word 中其余子行存在内容，也不会导入到页面。
   - 当首行填写或勾选为“是”时，后端才会回填其余 11 个子项字段。
   - 第 9 项之外的其他安全防护措施行不受该判断影响，仍按既有导入规则覆盖。
   - 前端应继续使用 `GET /survey/security-protection` 返回的结构化字段回填页面：

```json
{
  "isPowerMonitoringSystem": "YES",
  "productionControlAreaProtection": "",
  "securityAccessAreaSetup": "",
  "powerMonitoringDedicatedNetwork": "",
  "zoneIsolationDeviceUsage": "",
  "wideAreaNetworkConnectionSecurity": "",
  "powerDispatchAuthentication": "",
  "networkServiceSecurityControl": "",
  "securityAccessAreaSecurityControl": "",
  "zoneBoundaryProtection": "",
  "productSecurityReliability": "",
  "operatorSecurityMonitoringWarning": ""
}
```

   - 前端应以 `isPowerMonitoringSystem` 控制第 9 项其余 11 个输入项的启用和展示：值为 `NO` 时清空或不展示子项内容；值为 `YES` 时按接口返回文本回显。其余拆解字段按普通文本展示和保存。

导入成功后，前端建议重新请求以下现有接口刷新页面：

```http
GET /api/v1/projects/{projectId}/survey/data-processor-basic
GET /api/v1/projects/{projectId}/survey/business-systems?pageNo=1&pageSize=10
GET /api/v1/projects/{projectId}/survey/data-assets?pageNo=1&pageSize=10
GET /api/v1/projects/{projectId}/survey/personal-info?pageNo=1&pageSize=10
GET /api/v1/projects/{projectId}/survey/important-data?pageNo=1&pageSize=10
GET /api/v1/projects/{projectId}/survey/core-data?pageNo=1&pageSize=10
GET /api/v1/projects/{projectId}/survey/processing-activity-survey
GET /api/v1/projects/{projectId}/survey/security-protection
```

错误响应示例：

```json
{
  "code": "IMPORT_VALIDATION_FAILED",
  "message": "导入文件存在格式错误",
  "data": {
    "errors": [
      {
        "tableName": null,
        "rowNo": 0,
        "field": "file",
        "reason": "导入文件必须是 .docx 格式。"
      }
    ]
  }
}
```

前端展示建议：如果 `data.errors` 存在，将 `tableName`、`rowNo`、`field`、`reason` 拼成错误列表展示；没有明细时展示 `message`。

### 3. 导出信息调研 Word

```http
GET /api/v1/projects/{projectId}/survey/export
```

响应：直接下载 `.docx` 文件。

用途：前端“导出”按钮使用。后端会以 `附录A（资料性）调研表格.docx` 为底稿，把当前页面内容回填后返回。

## 前端页面调整建议

1. 在信息调研页面操作区增加三个按钮：
   - 下载模板
   - 导入
   - 导出

2. 导入按钮交互：
   - 只允许选择 `.docx` 文件。
   - 上传时使用 `multipart/form-data`，字段名固定为 `file`。
   - 导入成功后提示“导入成功”，并刷新五个标签页相关接口。
   - 导入失败时展示后端返回的错误明细。

3. 导出按钮交互：
   - 请求 `GET /survey/export`。
   - 按响应头下载文件即可，不需要前端拼装 Word 内容。

4. 下载模板按钮交互：
   - 请求 `GET /survey/export-template`。
   - 文件名以后端响应为准。

## 模板字段对应关系

| Word 模板表格 | 页面标签页 | 后端数据 |
| --- | --- | --- |
| 表A.1 数据处理者基本情况调研表 | 数据处理者基本情况调研 | `data-processor-basic` |
| 表A.2 业务和信息系统调研 | 业务和信息系统调研 | `business-systems` 第一条 |
| 表A.3 数据资产调研表 | 数据资产调研 | `data-assets` |
| 表A.4 个人信息清单表 | 数据资产调研 | `personal-info` |
| 表A.5 重要数据清单表 | 数据资产调研 | `important-data` |
| 表A.6 核心数据清单表 | 数据资产调研 | `core-data` |
| 表A.7 数据处理活动调研内容说明 | 数据处理活动调研 | `processing-activity-survey` |
| 表A.8 安全防护措施调研表 | 安全防护措施调研 | `security-protection` |

## 注意事项

- 导入会覆盖数据资产相关四个列表，前端不需要再自行删除旧记录。
- 导入接口只接受 `.docx`，不接受 `.doc`、`.xlsx`、`.pdf`。
- 拓扑图、数据流转图仍沿用现有单独上传接口，本次 Word 模板导入导出不处理图片或 Visio 附件。
