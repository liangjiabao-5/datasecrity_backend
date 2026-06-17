# Visio 对象插入文档实现分析文档

## 目录

- [1. 项目概述](#1-项目概述)
- [2. Visio 对象处理完整数据流](#2-visio-对象处理完整数据流)
- [3. 核心实现一：Visio 对象提取（解析阶段）](#3-核心实现一visio-对象提取解析阶段)
  - [3.1 extract_oles 函数](#31-extract_oles-函数)
  - [3.2 OLE 对象类型识别与存储](#32-ole-对象类型识别与存储)
- [4. 核心实现二：Visio 对象插入文档（导出阶段）](#4-核心实现二visio-对象插入文档导出阶段)
  - [4.1 insert_attachments 函数](#41-insert_attachments-函数)
  - [4.2 Visio 插入完整代码](#42-visio-插入完整代码)
  - [4.3 参数说明](#43-参数说明)
  - [4.4 关键逻辑步骤详解](#44-关键逻辑步骤详解)
  - [4.5 XML 结构说明](#45-xml-结构说明)
- [5. 核心实现三：Visio 嵌入验证](#5-核心实现三visio-嵌入验证)
  - [5.1 verify_docx 函数](#51-verify_docx-函数)
  - [5.2 验证逻辑步骤](#52-验证逻辑步骤)
- [6. 核心技术与依赖](#6-核心技术与依赖)
  - [6.1 技术栈总览](#61-技术栈总览)
  - [6.2 关键 API 与命名空间](#62-关键-api-与命名空间)
- [7. 不同实现方式对比分析](#7-不同实现方式对比分析)
- [8. 使用指南](#8-使用指南)
  - [8.1 环境准备](#81-环境准备)
  - [8.2 完整调用示例](#82-完整调用示例)
  - [8.3 验证嵌入结果](#83-验证嵌入结果)
  - [8.4 注意事项](#84-注意事项)

---

## 1. 项目概述

本项目是一个基于 Milvus 向量数据库的 RAG（检索增强生成）系统，核心功能包括文档解析、向量化索引、语义搜索和结果导出。其中，Visio 对象的处理是文档附件管理的重要部分，涵盖了从源文档中**提取** Visio OLE 对象、将其**存储**为附件文件、以及在导出文档时**重新嵌入** Visio 对象的完整生命周期。

涉及 Visio 对象处理的关键文件：

| 文件 | 职责 |
|------|------|
| [src/parser.py](file:///home/aiserver2/Milvus-demo/src/parser.py) | 从源 .docx 文档中提取 Visio OLE 对象 |
| [main.py](file:///home/aiserver2/Milvus-demo/main.py) | 将 Visio 对象以 OLE 方式插入导出的 .docx 文档 |
| [verify_visio_embed.py](file:///home/aiserver2/Milvus-demo/verify_visio_embed.py) | 验证导出文档中 Visio OLE 嵌入的正确性 |

---

## 2. Visio 对象处理完整数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                        源 .docx 文档                                │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  <w:object>                                                  │   │
│  │    <v:shape id="_x0000_i1025" ...>                          │   │
│  │      <v:imagedata .../>                                      │   │
│  │    </v:shape>                                                │   │
│  │    <o:OLEObject ProgID="Visio.Drawing.15" r:id="rId7" .../> │   │
│  │  </w:object>                                                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ ① DocxParser.parse() 提取
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    data/attachments/ 目录                            │
│  ├── {uuid}.vsdx          (Visio 二进制文件)                       │
│  └── {uuid}.vsdx.json     (元数据: type/orig_filename/content_type)│
└───────────────────────────┬─────────────────────────────────────────┘
                            │ ② 内容中标记 {{ATTACHMENT:/path/to/file}}
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│              MySQL + Milvus (索引存储)                               │
│  chunks.content 包含 {{ATTACHMENT:...}} 占位符                      │
│  attachments 表记录文件路径和类型                                    │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ ③ 搜索结果返回
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│              export_results_to_docx() 导出                          │
│  正则匹配 {{ATTACHMENT:...}} → insert_attachments() 重新嵌入        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  1. 创建 OLE Part (Part + PackURI)                          │   │
│  │  2. 建立关系 doc.part.relate_to(ole_part, RT.OLE_OBJECT)    │   │
│  │  3. 构造 XML: w:object > v:shape + o:OLEObject              │   │
│  │  4. 插入段落 paragraph._p.addnext(p_obj)                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ ④ 输出文档
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│              output/search_top1.docx                                 │
│  包含重新嵌入的 Visio OLE 对象                                       │
│  可通过 verify_visio_embed.py 验证                                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心实现一：Visio 对象提取（解析阶段）

> 源文件：[src/parser.py](file:///home/aiserver2/Milvus-demo/src/parser.py)

### 3.1 extract_oles 函数

该函数从 .docx 文档的段落中提取所有 OLE 对象，包括 Visio 嵌入对象。

```python
def extract_oles(para, doc_part):
    oles = []
    default_ns = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
        'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
        'o': 'urn:schemas-microsoft-com:office:office',
        'v': 'urn:schemas-microsoft-com:vml',
    }
    nsmap = {**default_ns, **(para._element.nsmap or {})}
    ole_nodes = para._element.findall('.//o:OLEObject', namespaces=nsmap)
    for node in ole_nodes:
        rId = node.get(qn('r:id'))
        if rId and rId in doc_part.rels:
            rel = doc_part.rels[rId]
            oles.append(rel.target_part)
    return oles
```

**关键逻辑说明：**

| 步骤 | 说明 |
|------|------|
| 命名空间合并 | 将默认命名空间与段落元素自身的命名空间合并，确保 XML 查询兼容性 |
| XML 查询 | 使用 `findall('.//o:OLEObject', ...)` 查找段落中所有 OLE 对象节点 |
| 关系解析 | 通过 `r:id` 属性从文档部件的关系集合中获取目标 Part（即嵌入的 Visio 文件） |
| 返回 Part 列表 | 返回 `rel.target_part` 对象列表，每个 Part 包含 Visio 文件的二进制数据和内容类型 |

### 3.2 OLE 对象类型识别与存储

提取 OLE 对象后，通过**文件魔数（Magic Bytes）**识别 Visio 文件类型，并保存到磁盘：

```python
if found_oles:
    for ole_part in found_oles:
        ct = getattr(ole_part, 'content_type', '') or ''
        blob = ole_part.blob
        ext = 'bin'
        try:
            head = blob[:4]
            if head[:2] == b'PK':
                ext = 'vsdx'
            elif head == b'\xD0\xCF\x11\xE0':
                ext = 'vsd'
        except Exception:
            pass
        fname = f"{uuid.uuid4()}.{ext}"
        save_path = os.path.abspath(os.path.join("data/attachments", fname))
        with open(save_path, "wb") as f:
            f.write(blob)
        meta = {
            'type': 'visio',
            'original_filename': fname,
            'content_type': ct
        }
        with open(save_path + ".json", "w", encoding="utf-8") as jf:
            json.dump(meta, jf, ensure_ascii=False)
        attachment_buffer.append({'file_path': save_path, 'type': 'visio', 'original_filename': fname})
        text += f"\n{{{{ATTACHMENT:{save_path}}}}}"
```

**文件类型识别规则：**

| 魔数 | 文件格式 | 扩展名 | 说明 |
|------|----------|--------|------|
| `PK`（前2字节为 `0x50 0x4B`） | Open Packaging Convention | `.vsdx` | Visio 2013+ 格式，基于 ZIP/XML |
| `\xD0\xCF\x11\xE0`（前4字节） | Compound Binary Format | `.vsd` | Visio 旧版格式，基于 OLE2 复合文档 |
| 其他 | 未知 | `.bin` | 兜底处理 |

**元数据 JSON 示例**（实际项目中的 `data/attachments/*.vsdx.json`）：

```json
{
    "type": "visio",
    "original_filename": "bc64cd34-13cc-4b30-abe6-5bcf2ef2096e.vsdx",
    "content_type": "application/vnd.ms-visio.drawing"
}
```

---

## 4. 核心实现二：Visio 对象插入文档（导出阶段）

> 源文件：[main.py](file:///home/aiserver2/Milvus-demo/main.py) 第 90-166 行

### 4.1 insert_attachments 函数

`insert_attachments` 是附件插入的统一入口函数，根据文件扩展名分派到不同的处理逻辑。对于 Visio 文件（`.vsd`、`.vsdx`、`.vss`、`.vssx`、`.vdx`、`.vsb`、`.bin`），采用 OLE 嵌入方式将其插入 Word 文档。

### 4.2 Visio 插入完整代码

```python
elif ext in ['.vsd', '.vsdx', '.vss', '.vssx', '.vdx', '.vsb', '.bin']:
    try:
        pkg = doc.part.package
        visio_ext = os.path.splitext(token_path)[1].lower()
        partname = pkg.next_partname(f"/word/embeddings/visio%d{visio_ext}")
        with open(token_path, "rb") as f:
            blob = f.read()

        if visio_ext == '.vsdx':
            content_type = 'application/vnd.ms-visio.drawing'
        else:
            content_type = 'application/vnd.ms-visio.application'

        ole_part = Part(PackURI(partname), content_type, blob, pkg)
        r_id = doc.part.relate_to(ole_part, RT.OLE_OBJECT)

        obj = OxmlElement("w:object")

        if 'v' not in ns.nsmap:
            ns.nsmap['v'] = 'urn:schemas-microsoft-com:vml'
        if 'o' not in ns.nsmap:
            ns.nsmap['o'] = 'urn:schemas-microsoft-com:office:office'
        if 'w' not in ns.nsmap:
            ns.nsmap['w'] = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

        shape = OxmlElement('v:shape')
        shape_id = f'_x0000_i{r_id[3:]}'
        shape.set('id', shape_id)
        shape.set('type', '#_x0000_t75')
        shape.set('style', 'width:400pt;height:300pt')
        shape.set(qn('o:ole'), '')

        imagedata = OxmlElement('v:imagedata')
        imagedata.set(qn('o:relid'), r_id)
        imagedata.set('src', f'visio_placeholder{r_id[3:]}.png')
        shape.append(imagedata)

        obj.append(shape)

        ole = OxmlElement("o:OLEObject")
        ole.set(qn("r:id"), r_id)
        if visio_ext == '.vsdx':
            ole.set("ProgID", "Visio.Drawing.15")
        else:
            ole.set("ProgID", "Visio.Drawing.12")
        ole.set("Type", "Embed")
        ole.set("ShapeID", shape_id)
        ole.set("DrawAspect", "Content")
        ole.set("ObjectID", f"_obj{r_id[3:]}")
        obj.append(ole)

        p_obj = OxmlElement("w:p")
        p_pr = OxmlElement("w:pPr")
        p_obj.append(p_pr)
        r_obj = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")
        r_obj.append(r_pr)
        r_obj.append(obj)
        p_obj.append(r_obj)
        paragraph._p.addnext(p_obj)

        cap_p = doc.paragraphs[-1]
        cap_p.add_run(f"（Visio对象：{os.path.basename(token_path)}）")
    except Exception as e:
        print(f"Visio 嵌入异常: {token_path}, error={e}")
        paragraph.add_run(f"[Visio嵌入异常: {os.path.basename(token_path)}]")
```

### 4.3 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `doc` | `docx.Document` | 目标 Word 文档对象 |
| `paragraph` | `docx.text.paragraph.Paragraph` | 当前段落，Visio 对象将插入到该段落之后 |
| `token_path` | `str` | Visio 文件的绝对路径 |

**支持的 Visio 文件扩展名：**

| 扩展名 | 文件类型 | ProgID | Content-Type |
|--------|----------|--------|--------------|
| `.vsdx` | Visio 2013+ 绘图 | `Visio.Drawing.15` | `application/vnd.ms-visio.drawing` |
| `.vsd` | Visio 旧版绘图 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |
| `.vssx` | Visio 2013+ 模板 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |
| `.vss` | Visio 旧版模板 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |
| `.vdx` | Visio XML 绘图 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |
| `.vsb` | Visio 二进制 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |
| `.bin` | 通用二进制 | `Visio.Drawing.12` | `application/vnd.ms-visio.application` |

### 4.4 关键逻辑步骤详解

#### 步骤 1：创建 OLE Part（包部件）

```python
pkg = doc.part.package
visio_ext = os.path.splitext(token_path)[1].lower()
partname = pkg.next_partname(f"/word/embeddings/visio%d{visio_ext}")
with open(token_path, "rb") as f:
    blob = f.read()

if visio_ext == '.vsdx':
    content_type = 'application/vnd.ms-visio.drawing'
else:
    content_type = 'application/vnd.ms-visio.application'

ole_part = Part(PackURI(partname), content_type, blob, pkg)
```

- 通过 `doc.part.package` 获取文档的 OPC 包对象
- `pkg.next_partname()` 自动生成不冲突的部件 URI，如 `/word/embeddings/visio1.vsdx`
- `Part(PackURI(...), content_type, blob, pkg)` 创建新的包部件，将 Visio 二进制数据作为 blob 存入

#### 步骤 2：建立 OLE 关系

```python
r_id = doc.part.relate_to(ole_part, RT.OLE_OBJECT)
```

- `RT.OLE_OBJECT` 是 `python-docx` 预定义的 OLE 对象关系类型常量
- 其值为 `http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject`
- `relate_to()` 返回关系 ID（如 `rId7`），用于后续 XML 中引用

#### 步骤 3：构造 VML Shape 容器

```python
shape = OxmlElement('v:shape')
shape_id = f'_x0000_i{r_id[3:]}'
shape.set('id', shape_id)
shape.set('type', '#_x0000_t75')
shape.set('style', 'width:400pt;height:300pt')
shape.set(qn('o:ole'), '')

imagedata = OxmlElement('v:imagedata')
imagedata.set(qn('o:relid'), r_id)
imagedata.set('src', f'visio_placeholder{r_id[3:]}.png')
shape.append(imagedata)
```

- `v:shape` 是 VML（Vector Markup Language）图形容器，用于在 Word 中显示 OLE 对象的可视表示
- `type="#_x0000_t75"` 引用预定义的图片形状类型
- `style='width:400pt;height:300pt'` 设置默认显示尺寸
- `o:ole` 属性标记此形状为 OLE 对象容器
- `v:imagedata` 提供占位图像引用，避免显示黑框

#### 步骤 4：构造 OLEObject 元素

```python
ole = OxmlElement("o:OLEObject")
ole.set(qn("r:id"), r_id)
if visio_ext == '.vsdx':
    ole.set("ProgID", "Visio.Drawing.15")
else:
    ole.set("ProgID", "Visio.Drawing.12")
ole.set("Type", "Embed")
ole.set("ShapeID", shape_id)
ole.set("DrawAspect", "Content")
ole.set("ObjectID", f"_obj{r_id[3:]}")
```

| 属性 | 值 | 说明 |
|------|----|------|
| `r:id` | 如 `rId7` | 关系 ID，指向 `/word/embeddings/` 中的 Visio 文件 |
| `ProgID` | `Visio.Drawing.15` 或 `Visio.Drawing.12` | COM 对象的程序标识符，决定双击时由哪个应用打开 |
| `Type` | `Embed` | 嵌入类型（Embed=嵌入，Link=链接） |
| `ShapeID` | 如 `_x0000_i7` | 关联的 VML Shape ID |
| `DrawAspect` | `Content` | 绘制外观（Content=内容视图，Icon=图标视图） |
| `ObjectID` | 如 `_obj7` | OLE 对象唯一标识 |

#### 步骤 5：组装段落并插入

```python
p_obj = OxmlElement("w:p")
p_pr = OxmlElement("w:pPr")
p_obj.append(p_pr)
r_obj = OxmlElement("w:r")
r_pr = OxmlElement("w:rPr")
r_obj.append(r_pr)
r_obj.append(obj)
p_obj.append(r_obj)
paragraph._p.addnext(p_obj)
```

- 构造完整的段落 XML 结构：`w:p > w:pPr + w:r > w:rPr + w:object`
- 使用 `paragraph._p.addnext(p_obj)` 将新段落插入到当前段落**之后**
- 这种方式操作底层 XML 元素，确保插入位置精确

### 4.5 XML 结构说明

最终生成的 XML 结构如下：

```xml
<w:p>
  <w:pPr/>
  <w:r>
    <w:rPr/>
    <w:object>
      <v:shape id="_x0000_i7" type="#_x0000_t75"
               style="width:400pt;height:300pt" o:ole="">
        <v:imagedata o:relid="rId7" src="visio_placeholder7.png"/>
      </v:shape>
      <o:OLEObject r:id="rId7" ProgID="Visio.Drawing.15"
                   Type="Embed" ShapeID="_x0000_i7"
                   DrawAspect="Content" ObjectID="_obj7"/>
    </w:object>
  </w:r>
</w:p>
```

对应的 `.rels` 文件中的关系条目：

```xml
<Relationship Id="rId7"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
  Target="embeddings/visio1.vsdx"/>
```

---

## 5. 核心实现三：Visio 嵌入验证

> 源文件：[verify_visio_embed.py](file:///home/aiserver2/Milvus-demo/verify_visio_embed.py)

### 5.1 verify_docx 函数

```python
def verify_docx(file_path):
    with zipfile.ZipFile(file_path, 'r') as zf:
        file_list = zf.namelist()

        # 1. 检查 embeddings 目录
        embeddings = [f for f in file_list if f.startswith('word/embeddings/')]
        for e in embeddings:
            print(f"  - {e} ({zf.get_info(e).file_size} bytes)")

        # 2. 检查关系文件
        rel_path = 'word/_rels/document.xml.rels'
        with zf.open(rel_path) as f:
            tree = ET.parse(f)
            root = tree.getroot()
            ns = {'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
            ole_rels = []
            for r in root.findall('rel:Relationship', ns):
                if 'oleObject' in r.get('Type', ''):
                    ole_rels.append((r.get('Id'), r.get('Target')))

        # 3. 检查 document.xml 中的结构
        doc_path = 'word/document.xml'
        with zf.open(doc_path) as f:
            tree = ET.parse(f)
            root = tree.getroot()
            ns = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                'o': 'urn:schemas-microsoft-com:office:office',
                'v': 'urn:schemas-microsoft-com:vml'
            }
            objects = root.findall('.//w:object', ns)
            for i, obj in enumerate(objects):
                shape = obj.find('v:shape', ns)
                ole_obj = obj.find('o:OLEObject', ns)
```

### 5.2 验证逻辑步骤

验证分为三个层级：

| 层级 | 检查内容 | 预期结果 |
|------|----------|----------|
| **文件层** | `word/embeddings/` 目录下是否存在 Visio 文件 | 应存在 `visio1.vsdx` 等文件 |
| **关系层** | `word/_rels/document.xml.rels` 中是否有 `oleObject` 类型关系 | 应存在指向 `embeddings/visio*.vsdx` 的关系 |
| **文档层** | `word/document.xml` 中是否包含完整的 `w:object` 结构 | 应同时包含 `v:shape` 和 `o:OLEObject` |

---

## 6. 核心技术与依赖

### 6.1 技术栈总览

| 技术/库 | 版本 | 用途 | 在 Visio 处理中的角色 |
|---------|------|------|----------------------|
| **python-docx** | - | Word 文档操作 | 核心：OPC 包操作、XML 构造、关系管理 |
| **OOXML/OPC** | ISO 29500 | Office Open XML 标准 | 定义 OLE 嵌入的 XML 结构和包格式 |
| **VML** | - | Vector Markup Language | 提供 OLE 对象的可视容器（`v:shape`） |
| **OLE 2.0** | - | Object Linking and Embedding | 嵌入对象的 COM 技术基础 |
| **lxml** | - | XML 处理（python-docx 底层） | `OxmlElement`、`qn()` 的底层实现 |

### 6.2 关键 API 与命名空间

#### python-docx 核心 API

| API | 模块 | 说明 |
|-----|------|------|
| `OxmlElement(tag)` | `docx.oxml.shared` | 创建 OOXML 元素 |
| `qn(tag)` | `docx.oxml.ns` | 将命名空间前缀转换为完整 URI，如 `qn('r:id')` → `{...relationships}id` |
| `Part(pack_uri, content_type, blob, package)` | `docx.opc.part` | 创建 OPC 包部件 |
| `PackURI(uri)` | `docx.opc.packuri` | 创建包 URI 对象 |
| `doc.part.relate_to(part, rel_type)` | `docx.opc.part` | 建立部件间关系 |
| `doc.part.package` | `docx.opc.package` | 获取文档所属的 OPC 包 |
| `pkg.next_partname(pattern)` | `docx.opc.package` | 自动生成不冲突的部件名称 |
| `RT.OLE_OBJECT` | `docx.opc.constants` | OLE 对象关系类型常量 |
| `paragraph._p.addnext(el)` | lxml | 在当前 XML 元素后插入兄弟元素 |

#### XML 命名空间

| 前缀 | URI | 用途 |
|------|-----|------|
| `w` | `http://schemas.openxmlformats.org/wordprocessingml/2006/main` | Word 文档主命名空间 |
| `r` | `http://schemas.openxmlformats.org/officeDocument/2006/relationships` | 关系引用 |
| `v` | `urn:schemas-microsoft-com:vml` | VML 矢量图形 |
| `o` | `urn:schemas-microsoft-com:office:office` | Office 通用属性（OLEObject） |
| `wp` | `http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing` | Word 绘图锚点 |
| `a` | `http://schemas.openxmlformats.org/drawingml/2006/main` | DrawingML 主命名空间 |

---

## 7. 不同实现方式对比分析

本项目采用的是 **OLE 嵌入方式**，以下是 Visio 对象插入 Word 文档的几种常见实现方式对比：

| 对比维度 | OLE 嵌入（本项目采用） | 图片替代法 | Open XML SDK 方式 | Aspose.Words 方式 |
|----------|----------------------|-----------|-------------------|-------------------|
| **实现原理** | 通过 OPC 包将 Visio 文件作为 OLE Part 嵌入，使用 VML Shape 作为可视容器 | 将 Visio 转换为 PNG/EMF 图片后插入 | 使用 Microsoft 的 Open XML SDK 构造完整 OOXML 结构 | 使用 Aspose.Words 商业库的高层 API |
| **核心库** | python-docx + lxml | python-docx | Open XML SDK (.NET) | Aspose.Words for Python |
| **可编辑性** | ✅ 双击可在 Visio 中打开编辑 | ❌ 仅静态图片 | ✅ 双击可在 Visio 中打开编辑 | ✅ 双击可在 Visio 中打开编辑 |
| **预览图** | ⚠️ 需额外处理，当前使用占位符 | ✅ 直接显示转换后的图片 | ✅ 可生成完整预览图 | ✅ 自动生成预览图 |
| **文件大小** | 较大（包含完整 Visio 数据） | 较小（仅图片） | 较大（包含完整 Visio 数据） | 较大（包含完整 Visio 数据） |
| **兼容性** | ⚠️ 需安装 Visio 才能编辑 | ✅ 所有 Word 版本可查看 | ⚠️ 需安装 Visio 才能编辑 | ⚠️ 需安装 Visio 才能编辑 |
| **实现复杂度** | 中等（需手动构造 XML） | 低（标准图片插入） | 中等（SDK 封装较好） | 低（高层 API） |
| **跨平台** | ✅ Python 跨平台 | ✅ Python 跨平台 | ❌ 仅 .NET/Windows | ✅ Python 跨平台 |
| **成本** | 免费（开源库） | 免费（开源库） | 免费（开源 SDK） | 💰 商业授权 |
| **适用场景** | 需保留 Visio 可编辑性的文档导出 | 仅需展示 Visio 图表外观 | .NET 生态下的文档处理 | 企业级文档处理，需完整功能 |

**本项目选择 OLE 嵌入方式的原因：**

1. **保留可编辑性**：导出的文档中 Visio 对象可双击打开编辑，满足业务需求
2. **纯 Python 实现**：无需依赖 .NET 或商业库，与项目技术栈一致
3. **数据完整性**：嵌入完整的 Visio 文件数据，不丢失信息
4. **已知局限性**：缺少真实预览图（当前使用占位符），在未安装 Visio 的环境中可能显示异常

---

## 8. 使用指南

### 8.1 环境准备

```bash
# 安装依赖
pip install python-docx pymilvus==2.3.6 sentence-transformers sqlalchemy pymysql python-dotenv numpy pandas marshmallow
```

### 8.2 完整调用示例

#### 方式一：通过主程序自动处理

```python
from main import export_results_to_docx

results = [
    {
        'score': 0.95,
        'filename': 'example.docx',
        'title': '系统架构 > 网络拓扑',
        'content': '网络拓扑图如下：\n{{ATTACHMENT:/path/to/data/attachments/uuid.vsdx}}'
    }
]

export_results_to_docx(results, 'output/result.docx')
```

#### 方式二：单独调用 Visio 插入函数

```python
from docx import Document
from main import insert_attachments

doc = Document()
paragraph = doc.add_paragraph("以下是 Visio 图表：")

# 插入 Visio 文件
insert_attachments(doc, paragraph, "/path/to/diagram.vsdx")

doc.save("output/with_visio.docx")
```

#### 方式三：仅插入 Visio OLE 对象（底层 API）

```python
from docx import Document
from docx.oxml.shared import OxmlElement, qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import ns

def insert_visio_ole(doc, paragraph, visio_path):
    pkg = doc.part.package
    visio_ext = '.vsdx'
    partname = pkg.next_partname(f"/word/embeddings/visio%d{visio_ext}")

    with open(visio_path, "rb") as f:
        blob = f.read()

    content_type = 'application/vnd.ms-visio.drawing'
    ole_part = Part(PackURI(partname), content_type, blob, pkg)
    r_id = doc.part.relate_to(ole_part, RT.OLE_OBJECT)

    if 'v' not in ns.nsmap:
        ns.nsmap['v'] = 'urn:schemas-microsoft-com:vml'
    if 'o' not in ns.nsmap:
        ns.nsmap['o'] = 'urn:schemas-microsoft-com:office:office'

    obj = OxmlElement("w:object")

    shape = OxmlElement('v:shape')
    shape_id = f'_x0000_i{r_id[3:]}'
    shape.set('id', shape_id)
    shape.set('type', '#_x0000_t75')
    shape.set('style', 'width:400pt;height:300pt')
    shape.set(qn('o:ole'), '')

    imagedata = OxmlElement('v:imagedata')
    imagedata.set(qn('o:relid'), r_id)
    shape.append(imagedata)
    obj.append(shape)

    ole = OxmlElement("o:OLEObject")
    ole.set(qn("r:id"), r_id)
    ole.set("ProgID", "Visio.Drawing.15")
    ole.set("Type", "Embed")
    ole.set("ShapeID", shape_id)
    ole.set("DrawAspect", "Content")
    ole.set("ObjectID", f"_obj{r_id[3:]}")
    obj.append(ole)

    p_obj = OxmlElement("w:p")
    p_obj.append(OxmlElement("w:pPr"))
    r_obj = OxmlElement("w:r")
    r_obj.append(OxmlElement("w:rPr"))
    r_obj.append(obj)
    p_obj.append(r_obj)
    paragraph._p.addnext(p_obj)

doc = Document()
p = doc.add_paragraph("Visio 图表：")
insert_visio_ole(doc, p, "diagram.vsdx")
doc.save("output/visio_embedded.docx")
```

### 8.3 验证嵌入结果

```bash
python verify_visio_embed.py output/search_top1.docx
```

预期输出：

```
=== 验证文档: output/search_top1.docx ===
发现嵌入对象: 1
  - word/embeddings/visio1.vsdx (xxxxx bytes)
发现 OLE 关系: 1
  - ID: rId7, Target: embeddings/visio1.vsdx
发现 w:object 元素: 1
  对象 1:
    - 包含 v:shape (ID: _x0000_i7, Style: width:400pt;height:300pt)
    - 包含 o:OLEObject (ProgID: Visio.Drawing.15, r:id: rId7)
=== 验证完成 ===
```

### 8.4 注意事项

1. **ProgID 版本匹配**：`Visio.Drawing.15` 对应 Visio 2013+，`Visio.Drawing.12` 对应 Visio 2007-2010。使用错误的 ProgID 可能导致双击无法正确打开编辑器。

2. **预览图缺失**：当前实现使用占位符 `v:imagedata`，在未安装 Visio 的环境中可能显示为空白或黑框。如需完整预览，应额外生成 Visio 文件的缩略图并作为图片 Part 嵌入。

3. **命名空间注册**：`v` 和 `o` 命名空间非 python-docx 默认注册，需在构造 XML 前手动添加到 `ns.nsmap`，否则 `qn()` 函数无法正确解析带前缀的标签。

4. **Content-Type 准确性**：`.vsdx` 文件必须使用 `application/vnd.ms-visio.drawing`，其他格式使用 `application/vnd.ms-visio.application`。错误的 Content-Type 可能导致 Word 拒绝加载嵌入对象。

5. **文件大小**：OLE 嵌入会将完整 Visio 文件数据写入 .docx 包内，大文件会显著增加文档体积。

6. **插入位置**：使用 `paragraph._p.addnext(p_obj)` 将 Visio 段落插入到当前段落之后，而非在当前段落内插入。这是 OOXML 规范要求的结构——`w:object` 必须位于独立的 `w:p/w:r` 内。
