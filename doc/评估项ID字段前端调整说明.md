# 评估项ID字段前端调整说明

更新时间：2026-07-14

## 1. 调整背景

`doc/国标测评录入模版.xlsx` 已新增“评估项ID”列，并删除/新增了部分评估项。后端已同步新增字段 `assessmentItemId`，用于在现场测评和汇总分析列表中展示国标评估项编号，例如 `AQGL001`、`SJCL001`、`AQJS077`。

后端会把新版 Excel 中带数量后缀的工作表名规范为原业务名称，例如：

| Excel 工作表名 | 接口返回 `sheetName` |
| --- | --- |
| 数据安全管理113-3-20 | 数据安全管理 |
| 数据处理活动125 | 数据处理活动 |
| 数据安全技术76+13 | 数据安全技术 |
| 个人信息保护87 | 个人信息保护 |

## 2. 字段说明

| 字段 | 类型 | 含义 | 示例 |
| --- | --- | --- | --- |
| `assessmentItemId` | `string \| null` | 国标模板中的“评估项ID” | `AQGL001` |

兼容建议：如果接口返回为空或旧环境暂未返回该字段，前端展示 `-`。

## 3. 受影响接口

### 3.1 现场测评列表

```http
GET /api/v1/projects/{projectId}/evaluation/items?pageNo=1&pageSize=10
```

`data.list[]` 新增：

```json
{
  "id": "pai-xxx",
  "itemCode": "1",
  "assessmentItemId": "AQGL001",
  "sheetName": "数据安全管理",
  "category": "安全管理制度",
  "subcategory": "数据安全制度体系",
  "checkPoint": "针对数据安全制度体系建设情况..."
}
```

前端调整：现场测评表格新增“评估项ID”列，建议放在“检查项编号”后、“工作表/一级分类”前。

### 3.2 汇总分析：数据安全风险源清单

```http
GET /api/v1/projects/{projectId}/risk-sources?pageNo=1&pageSize=10
```

`data.list[]` 新增 `assessmentItemId`。风险源清单当前列顺序已调整为“评估项ID”后展示“评估子类”，并将“风险源类型”“风险类型”展示在“风险源描述”后。

### 3.3 汇总分析：数据安全风险清单

```http
GET /api/v1/projects/{projectId}/risk-items?pageNo=1&pageSize=10
```

`data.list[]` 新增 `assessmentItemId`。建议在风险清单表格中把“评估项ID”放在“序号”后、“风险类型”前。

### 3.4 汇总分析：数据安全风险处置建议

```http
GET /api/v1/projects/{projectId}/risk-suggestions?pageNo=1&pageSize=10
```

`data.list[]` 新增 `assessmentItemId`。建议在处置建议表格中把“评估项ID”放在“序号”后、“风险描述/风险源描述”前，具体位置可按当前页面列宽调整。

## 4. 导入导出影响

### 4.1 现场测评 Excel

以下接口导出的 Excel 新增“评估项ID”列：

```http
GET /api/v1/projects/{projectId}/evaluation/export-template
GET /api/v1/projects/{projectId}/evaluation/export
```

列顺序调整为：

| 顺序 | 列名 |
| --- | --- |
| 1 | 检查项ID |
| 2 | 检查项编号 |
| 3 | 评估项ID |
| 4 | 工作表 |
| 5 | 一级分类 |
| 6 | 二级分类 |
| 7 | 检查要点 |
| 8 | 评估结果 |
| 9 | 符合情况 |

导入接口已兼容该列：

```http
POST /api/v1/projects/{projectId}/evaluation/import
```

前端注意：“评估项ID”属于来源只读列，不需要提交到保存接口，也不建议允许用户编辑导入模板中的该列。

### 4.2 汇总分析 Excel

以下导出新增“评估项ID”列，位置在“序号”后：

```http
GET /api/v1/projects/{projectId}/risk-sources/export
GET /api/v1/projects/{projectId}/risk-items/export
```

导入接口会识别该列，但它不是可编辑字段：

```http
POST /api/v1/projects/{projectId}/risk-sources/import
POST /api/v1/projects/{projectId}/risk-items/import
```

前端注意：风险源导入会校验“评估项ID”等来源字段是否与系统记录一致；用户如修改该列，可能收到行级错误。

## 5. 前端类型建议

如果前端有类型定义，建议给以下数据模型补充字段：

```ts
assessmentItemId?: string | null
```

建议补充到：

- 现场测评列表项类型
- 风险源清单项类型
- 数据安全风险清单项类型
- 风险处置建议项类型
- Excel 导入结果预览中使用的行模型（如有）

## 6. 回归检查建议

1. 新建项目并开始评估后，现场测评第一行能展示 `AQGL001`。
2. 现场测评中将一条记录标记为“基本符合”或“不符合”，刷新汇总分析后三个列表都能展示同一个 `assessmentItemId`。
3. 汇总分析风险源清单、风险清单导出 Excel 后，第二列为“评估项ID”。
4. 旧项目进入现场测评和汇总分析页面时，表格列不报错；字段为空时展示 `-`。
5. 前端保存现场测评、风险源、风险清单、处置建议时，不需要把 `assessmentItemId` 作为可编辑字段提交。
