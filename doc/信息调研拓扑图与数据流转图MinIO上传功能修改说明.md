# 信息调研拓扑图与数据流转图 MinIO 上传功能修改说明

## 一、变更范围

本次变更针对“信息调研页面 - 业务和信息系统调研”标签页中的两个图片上传控件：

- 上传拓扑图
- 上传数据流转图

后端已支持将文件对象保存到 MinIO，并在上传成功后自动回填业务系统调研记录中的文件 ID。

## 二、删除功能

后端没有删除既有接口，以下接口仍保留兼容：

- `POST /api/v1/files`
- `GET /api/v1/files/{fileId}/download`
- `GET/POST/PUT /api/v1/projects/{projectId}/survey/business-systems`

前端建议删除或停用这两个控件上的旧两步逻辑：

- 先调用 `POST /api/v1/files` 上传图片。
- 再调用 `PUT /api/v1/projects/{projectId}/survey/business-systems/{businessSystemId}` 手动回填 `topologyFileId` 或 `businessFlowFileId`。

上述旧逻辑仍可兼容，但新接口已经把“上传文件 + 回填业务系统记录”合并为一次请求，前端不再需要自己维护两步提交。

## 三、修改功能

### 1. 文件存储改为支持 MinIO

后端文件服务已支持 MinIO 配置。运行环境配置如下：

```env
MINIO_ENDPOINT=your-minio-host:9000
MINIO_ACCESS_KEY=replace-with-your-minio-access-key
MINIO_SECRET_KEY=replace-with-your-minio-secret-key
MINIO_SECURE=false
MINIO_BUCKET_NAME=datasecrity
```

当以上 MinIO 配置完整时，拓扑图和数据流转图会保存到 MinIO bucket `datasecrity`。如未配置 MinIO，这两个图片仍会回退到本地文件目录，便于本地开发和测试。

当前写入 MinIO 的业务类型：

- `SURVEY_TOPOLOGY_DIAGRAM`
- `SURVEY_DATA_FLOW_DIAGRAM`
- `REPORT`

报告管理模块完成后，正式 Word 报告同样优先写入 MinIO；现场测评导入导出文件等其他文件业务类型仍保持原有本地存储行为。

前端不需要连接 MinIO，也不要保存或暴露 MinIO access key / secret key。

### 2. 新增上传拓扑图接口

```http
POST /api/v1/projects/{projectId}/survey/business-systems/{businessSystemId}/topology-diagram
Content-Type: multipart/form-data
```

请求参数：

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| file | form-data | 图片文件 |

上传成功后，后端会自动更新当前业务系统记录的 `topologyFileId`。

### 3. 新增上传数据流转图接口

```http
POST /api/v1/projects/{projectId}/survey/business-systems/{businessSystemId}/data-flow-diagram
Content-Type: multipart/form-data
```

请求参数：

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| file | form-data | 图片文件 |

上传成功后，后端会自动更新当前业务系统记录的 `businessFlowFileId`。

注意：字段名仍保持为 `businessFlowFileId`，前端不要改成 `dataFlowFileId`。

### 4. 上传返回结构

两个新增接口返回结构一致：

```json
{
  "file": {
    "fileId": "file-xxx",
    "fileName": "topology.png",
    "objectKey": "files/file-xxx_topology.png",
    "storageProvider": "MINIO",
    "bucketName": "datasecrity",
    "contentType": "image/png",
    "fileSize": 102400,
    "bizType": "SURVEY_TOPOLOGY_DIAGRAM",
    "downloadUrl": "/api/v1/files/file-xxx/download"
  },
  "businessSystem": {
    "id": "sys-xxx",
    "topologyFileId": "file-xxx",
    "businessFlowFileId": "file-yyy"
  }
}
```

数据流转图上传时，`bizType` 为 `SURVEY_DATA_FLOW_DIAGRAM`，并更新 `businessSystem.businessFlowFileId`。

## 四、前端需要同步修改

1. “上传拓扑图”控件改为调用：
   `POST /api/v1/projects/{projectId}/survey/business-systems/{businessSystemId}/topology-diagram`

2. “上传数据流转图”控件改为调用：
   `POST /api/v1/projects/{projectId}/survey/business-systems/{businessSystemId}/data-flow-diagram`

3. 上传成功后，以响应中的 `businessSystem` 更新当前表单状态：
   - 拓扑图：读取 `businessSystem.topologyFileId`
   - 数据流转图：读取 `businessSystem.businessFlowFileId`

4. 如需下载或预览已上传文件，继续使用响应中的：
   `file.downloadUrl`

5. 前端仍使用 `businessFlowFileId` 字段，不需要因为页面文案改为“数据流转图”而调整字段名。

6. 文件选择校验建议与后端保持一致，仅允许图片类型：
   `.png`、`.jpg`、`.jpeg`、`.gif`、`.bmp`、`.webp`、`.svg` 或浏览器识别为 `image/*` 的文件。

7. 需要处理的错误码：
   - `FILE_REQUIRED`：未上传文件。
   - `INVALID_DIAGRAM_FILE`：上传的不是图片文件。
   - `MINIO_CONFIG_INCOMPLETE`：后端 MinIO 配置不完整。
   - `MINIO_BUCKET_UNAVAILABLE`：MinIO bucket 不可用。
   - `MINIO_UPLOAD_FAILED`：上传 MinIO 失败。
   - `MINIO_DOWNLOAD_FAILED`：从 MinIO 下载失败。
   - `MINIO_TIME_SKEW`：应用服务器与 MinIO 服务器时间差过大，需要同步两端系统时间。

## 五、保持不变

- 业务系统调研列表、新增、编辑接口保持不变。
- 文件下载接口保持不变。
- 表单字段 `topologyFileId` 保持不变。
- 表单字段 `businessFlowFileId` 保持不变。
- 前端不需要直接访问 MinIO。

## 六、后端验证

已执行测试：

```bash
python -m pytest tests/test_flow.py -q
```

测试结果：`11 passed`。
