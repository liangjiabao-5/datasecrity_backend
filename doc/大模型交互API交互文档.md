# 大模型交互 API 交互文档

## 1. 使用定位

大模型能力在本项目中定位为“评估辅助”，不作为最终判定引擎。系统的评分计算、风险发生可能性、风险危害程度和风险等级仍由评分模型、风险危害程度分析模型和风险评价矩阵完成。大模型输出只能作为建议，必须由用户确认后才能写入评估记录、处置建议或报告内容。

## 2. 适用场景

| 场景 | 输入 | 输出 | 是否可直接入库 |
| --- | --- | --- | --- |
| 参考案例匹配 | 当前检查项、业务场景、评估结果、符合情况 | 相似案例、匹配原因、建议 | 否，用户选择后应用 |
| 评估记录建议 | 检查要点、符合情况、现场描述、调研上下文 | 评估结果建议 | 否，用户确认后保存 |
| 整改建议生成 | 风险源描述、风险等级、涉及数据、处理活动 | 整改建议和处置措施 | 否，用户确认后保存 |
| 报告段落草稿 | 项目基础信息、风险清单、章节类型 | 报告段落草稿 | 否，用户确认后进入报告 |
| 文本润色 | 用户已写内容 | 更规范的表述 | 否，用户确认后替换 |

## 3. 系统架构

前端不直接调用外部大模型。调用链如下：

```text
前端页面
  -> 业务后端 /api/v1/llm/*
  -> 大模型网关 LlmGatewayService
  -> 脱敏与审计
  -> RAG 检索或案例库检索
  -> 模型供应商或私有模型
  -> 输出校验
  -> 返回前端供人工确认
```

## 4. 模型接入方案

### 4.1 方案 A：OpenAI 兼容 API

适用：快速验证、模型效果要求较高、允许在合规前提下调用外部模型。

请求形态：

```http
POST /v1/chat/completions
```

优点：

- 接入快。
- 可选模型多。
- 支持结构化输出。

风险：

- 需要严格脱敏。
- 需要考虑网络、费用和数据合规。

### 4.2 方案 B：企业内网模型

适用：数据敏感、必须内网部署的场景。

优点：

- 数据可控。
- 可结合企业知识库。

风险：

- 模型效果和推理成本需要评估。
- 运维复杂度高。

### 4.3 推荐

系统设计上采用供应商无关的大模型网关。业务接口固定，模型供应商可配置。是否启用外部模型由部署环境决定。

## 5. 数据安全要求

### 5.1 脱敏规则

发送模型前默认脱敏：

- 姓名。
- 手机号。
- 固话。
- 邮箱。
- 单位具体地址。
- 统一社会信用代码。
- 账号、证书、密钥、IP 地址。
- 其他用户标记为敏感的字段。

示例：

```json
{
  "name": "张三"
}
```

脱敏后：

```json
{
  "name": "<联系人>"
}
```

### 5.2 禁止发送

- 原始报告全文。
- 未脱敏的联系人信息。
- 用户上传的敏感文件原文。
- 与当前任务无关的数据。

### 5.3 审计要求

每次调用记录：

- 调用人。
- 项目 ID。
- 场景。
- 输入摘要。
- 脱敏策略版本。
- 模型名称。
- 输出摘要。
- 用户是否应用建议。
- 调用时间、耗时、token 用量。

## 6. 通用业务接口

所有大模型能力由业务后端提供以下统一接口。前端只调用业务后端。

### 6.1 通用响应

```json
{
  "code": "SUCCESS",
  "message": "操作成功",
  "data": {
    "requestId": "llm-001",
    "result": {}
  },
  "traceId": "..."
}
```

### 6.2 失败响应

```json
{
  "code": "LLM_SERVICE_UNAVAILABLE",
  "message": "大模型服务暂不可用，请稍后重试",
  "data": null,
  "traceId": "..."
}
```

## 7. 案例匹配接口

### 7.1 接口

```http
POST /api/v1/llm/case-match
```

### 7.2 请求

```json
{
  "projectId": "p001",
  "businessSystemId": "sys-001",
  "assessmentItemId": "item-001",
  "businessScenario": "文档管理",
  "assessmentItem": "访问控制",
  "evaluationResult": "NON_COMPLIANT",
  "evaluationRecord": "部分账号权限过大，可访问非职责范围内的敏感文档",
  "topK": 3
}
```

### 7.3 返回

```json
{
  "requestId": "llm-001",
  "matches": [
    {
      "caseId": "case-001",
      "caseSystem": "OA办公系统",
      "businessScenario": "文档管理",
      "assessmentItem": "访问控制",
      "foundProblem": "部分员工账号权限过大，可访问非职责范围内的敏感文档",
      "caseSuggestion": "实施最小权限原则，定期审计权限；建立角色权限矩阵；启用多因素认证",
      "riskLevel": "HIGH",
      "matchScore": 0.95,
      "matchReason": "检查项和评估结果均与权限过大相关"
    }
  ]
}
```

### 7.4 处理规则

- 先从参考案例库检索，再由大模型生成匹配原因和综合建议。
- 匹配结果只展示，不直接写入评估记录。
- 用户点击“应用建议”后，调用业务保存接口写入当前记录。

## 8. 评估记录建议接口

### 8.1 接口

```http
POST /api/v1/llm/evaluation-record/suggest
```

### 8.2 请求

```json
{
  "projectId": "p001",
  "assessmentItemId": "item-001",
  "checkContent": "针对数据安全制度体系建设情况，应重点评估总体策略、方针、目标和原则制定情况。",
  "evaluationResult": "NON_COMPLIANT",
  "fieldNotes": "未提供正式发布的数据安全总体策略文件，仅有部门级管理要求。",
  "surveyContext": {
    "systemType": "管理信息系统",
    "dataTypes": ["个人信息", "业务数据"],
    "processingActivities": ["STORE", "TRANSFER"]
  }
}
```

### 8.3 返回

```json
{
  "requestId": "llm-002",
  "suggestion": {
    "evaluationRecord": "经核查，被评估单位未能提供正式发布的数据安全总体策略、方针、目标和原则文件，现有材料主要为部门级管理要求，未覆盖数据分类分级、访问权限控制、传输加密和审计监督等关键内容。",
    "confidence": 0.82,
    "notes": "建议由评估人员结合现场证据复核。"
  }
}
```

### 8.4 输出约束

- 不得虚构现场证据。
- 不得输出“已经整改”“已完成”等未经用户提供的信息。
- 输出只作为 `evaluationRecord` 建议，不写入问题描述、整改建议或风险类型。

## 9. 整改建议生成接口

### 9.1 接口

```http
POST /api/v1/llm/remediation/suggest
```

### 9.2 请求

```json
{
  "projectId": "p001",
  "riskRecordId": "riskrec-001",
  "riskDescription": "由于数据窃取、爬取、脱库、撞库等攻击行为导致数据泄露的风险",
  "riskSourceDescription": "缺乏完善的数据分类分级管理制度和操作规范",
  "relatedData": "用户敏感数据（个人信息）",
  "relatedActivities": ["STORE", "TRANSFER"],
  "harmLevel": "HIGH",
  "possibilityLevel": "LOW",
  "riskLevel": "LOW"
}
```

### 9.3 返回

```json
{
  "requestId": "llm-003",
  "suggestions": [
    {
      "title": "完善数据分类分级制度",
      "content": "建议被评估单位依据适用标准建立数据分类分级制度，明确个人信息、重要数据和一般业务数据的分类规则、标识规则、审批流程和责任部门。",
      "priority": "HIGH"
    },
    {
      "title": "加强存储和传输保护",
      "content": "建议对涉及个人信息的数据存储和传输环节落实访问控制、加密传输、日志审计和异常访问监测措施。",
      "priority": "MEDIUM"
    }
  ],
  "confidence": 0.86
}
```

## 10. 报告段落草稿接口

### 10.1 接口

```http
POST /api/v1/llm/report-section/draft
```

### 10.2 请求

```json
{
  "projectId": "p001",
  "sectionType": "RISK_SUMMARY",
  "style": "formal",
  "facts": {
    "projectName": "XX银行数据安全评估项目",
    "riskCount": 12,
    "highRiskCount": 2,
    "mainRiskTypes": ["DATA_LEAKAGE", "DATA_TAMPERING"]
  }
}
```

### 10.3 返回

```json
{
  "requestId": "llm-004",
  "draft": "本次评估共识别数据安全风险 12 项，其中高安全风险 2 项。主要风险类型包括数据泄露风险和数据篡改风险，风险来源集中在访问控制、数据分类分级和日志审计等方面。",
  "usedFacts": ["riskCount", "highRiskCount", "mainRiskTypes"]
}
```

### 10.4 输出约束

- 只能基于 `facts` 中提供的事实写作。
- 不得新增未经提供的统计数据。
- 返回 `usedFacts`，便于用户核对。

## 11. 文本润色接口

```http
POST /api/v1/llm/text/polish
```

请求：

```json
{
  "projectId": "p001",
  "text": "原始文本",
  "style": "formal",
  "maxLength": 500
}
```

返回：

```json
{
  "requestId": "llm-005",
  "polishedText": "润色后的文本"
}
```

## 12. 提示词模板原则

系统提示词应包含：

- 当前角色：数据安全风险评估辅助编写助手。
- 任务边界：只给建议，不做最终判定。
- 输出格式：必须按 JSON schema 输出。
- 事实约束：只能使用输入事实和检索案例。
- 风险约束：不得虚构现场证据，不得输出绝对化结论。

示例：

```text
你是数据安全风险评估辅助助手。请基于输入的检查项、评估结果、现场记录和调研上下文，生成评估记录建议。
要求：
1. 不得虚构输入中没有出现的事实。
2. 只生成评估结果文本建议，不生成问题描述、整改建议或风险类型。
3. 输出必须是 JSON，不要输出解释性前言。
4. 生成内容供评估人员确认，不代表最终结论。
```

## 13. 输出校验

后端接收模型输出后必须校验：

- JSON 是否可解析。
- 字段是否完整。
- 建议长度是否超过限制。
- 是否包含敏感信息。
- 是否出现禁用表达，如“无需复核”“百分百确定”等。

校验失败时：

- 记录失败日志。
- 不写入业务数据。
- 返回前端“建议生成失败，请重试或手工填写”。

## 14. 人工确认机制

大模型输出进入正式数据前必须经过用户动作：

- 应用建议。
- 保存评估记录。
- 保存整改建议。
- 替换文本。

系统应记录：

- 原模型输出。
- 用户最终保存内容。
- 是否修改过模型输出。
- 操作人和时间。

## 15. 开关与降级

配置项：

```env
LLM_ENABLED=true
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus
DASHSCOPE_API_KEY=replace-with-your-dashscope-api-key
DASHSCOPE_TIMEOUT_MS=30000
```

降级策略：

- 大模型不可用时，页面隐藏或禁用辅助按钮。
- 不影响项目、测评、汇总和报告主流程。
- 知识库处置建议仍可作为规则化建议来源。
