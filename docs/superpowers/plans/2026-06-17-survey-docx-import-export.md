# Survey Docx Import Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Word `.docx` import, export, and template-download support for the information survey page.

**Architecture:** Add one focused service, `app/services/survey_docx_service.py`, that owns Word XML parsing, data mapping, persistence, and document generation. Extend `app/blueprints/survey.py` with three endpoints that mirror existing Excel import/export response patterns.

**Tech Stack:** Flask, SQLAlchemy, Python standard library `zipfile` and `xml.etree.ElementTree`, existing `file_service`, pytest.

---

## File Structure

- Create `app/services/survey_docx_service.py`: validate `.docx`, parse template tables, map table cells to existing survey models, save imported data, generate exported Word bytes.
- Modify `app/blueprints/survey.py`: add `export-template`, `import`, and `export` routes before generic `/survey/<kind>` routes.
- Modify `tests/test_flow.py`: add integration tests for import/export and invalid file handling.
- Create `doc/信息调研Word导入导出功能修改说明.md`: front-end integration instructions.

## Tasks

### Task 1: Write Failing Tests

**Files:**
- Modify: `tests/test_flow.py`

- [ ] **Step 1: Add a helper that edits docx table cells in memory**

Add helper functions near existing test helpers:

```python
def survey_docx_stream(edits):
    template = Path("doc") / "附录A（资料性）调研表格.docx"
    source = BytesIO(template.read_bytes())
    output = BytesIO()
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        document = ET.fromstring(zin.read("word/document.xml"))
        tables = list(document.iter(W + "tbl"))
        for table_no, row_no, col_no, value in edits:
            cell = tables[table_no].findall(W + "tr")[row_no].findall(W + "tc")[col_no]
            texts = list(cell.iter(W + "t"))
            if texts:
                texts[0].text = value
                for extra in texts[1:]:
                    extra.text = ""
        xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        for item in zin.infolist():
            zout.writestr(item, xml if item.filename == "word/document.xml" else zin.read(item.filename))
    output.seek(0)
    return output
```

- [ ] **Step 2: Add import/export behavior test**

Add `test_survey_docx_import_overwrites_lists_and_export_fills_template`. It should create a project, insert stale list rows through existing endpoints, import a modified Word template, assert existing survey endpoints return imported values and stale list rows are gone, then export and assert `word/document.xml` contains imported values.

- [ ] **Step 3: Add invalid upload test**

Add `test_survey_docx_import_rejects_non_docx`, post a `.txt` file to `/survey/import`, and assert status `400`, code `IMPORT_VALIDATION_FAILED`.

- [ ] **Step 4: Run red tests**

Run:

```bash
pytest tests/test_flow.py::test_survey_docx_import_overwrites_lists_and_export_fills_template tests/test_flow.py::test_survey_docx_import_rejects_non_docx -q
```

Expected: tests fail because `/survey/import` and `/survey/export` are not implemented.

### Task 2: Implement Service

**Files:**
- Create: `app/services/survey_docx_service.py`

- [ ] **Step 1: Add constants and public API**

Create the service with:

```python
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEMPLATE_PATH = Path("doc") / "附录A（资料性）调研表格.docx"

def export_template_docx(project_id: str) -> tuple[str, bytes, str]:
    get_project(project_id)
    return f"信息调研模板-{project_id}.docx", TEMPLATE_PATH.read_bytes(), DOCX_MIME_TYPE

def import_survey_docx(project_id: str, file) -> dict:
    ...

def export_survey_docx(project_id: str) -> tuple[str, bytes, str]:
    ...
```

- [ ] **Step 2: Add docx XML helpers**

Implement `_load_docx_tables`, `_cell_text`, `_set_cell_text`, `_ensure_cell`, and `_write_document_xml` using `zipfile` and `ElementTree`. The helpers must only read and rewrite `word/document.xml`.

- [ ] **Step 3: Add parsing functions**

Implement `_parse_data_processor`, `_parse_business_system`, `_parse_data_assets`, `_parse_processing_activity`, and `_parse_security_protection` so table A.1-A.8 produce a dict with keys matching database snake_case fields.

- [ ] **Step 4: Add persistence functions**

Implement `_save_data_processor`, `_save_primary_business_system`, `_replace_list_records`, `_save_processing_activity`, and `_save_security_protection`. Use one `SessionLocal()` transaction and soft-delete old list rows before adding imported list records.

- [ ] **Step 5: Add export functions**

Implement `_fill_data_processor`, `_fill_business_system`, `_fill_asset_table`, `_fill_processing_activity`, and `_fill_security_protection`. Rebuild A.3-A.6 data rows based on current list length and write current survey values to the template.

### Task 3: Wire Flask Routes

**Files:**
- Modify: `app/blueprints/survey.py`

- [ ] **Step 1: Import response helpers and service**

Add:

```python
from io import BytesIO
from flask import Blueprint, request, send_file
from app.services import crud_service, file_service, survey_docx_service, survey_service
```

- [ ] **Step 2: Add routes before generic `/survey/<kind>`**

Add:

```python
@bp.get("/projects/<project_id>/survey/export-template")
def export_survey_template(project_id: str):
    return _send_generated_docx(project_id, survey_docx_service.export_template_docx(project_id), "SURVEY_DOCX_TEMPLATE")

@bp.post("/projects/<project_id>/survey/import")
def import_survey_docx(project_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(survey_docx_service.import_survey_docx(project_id, uploaded))

@bp.get("/projects/<project_id>/survey/export")
def export_survey_docx(project_id: str):
    return _send_generated_docx(project_id, survey_docx_service.export_survey_docx(project_id), "SURVEY_DOCX_EXPORT")
```

- [ ] **Step 3: Add `_send_generated_docx`**

Use the same pattern as other blueprints:

```python
def _send_generated_docx(project_id: str, generated: tuple[str, bytes, str], biz_type: str):
    file_name, content, content_type = generated
    file_service.save_bytes(file_name, content, content_type, biz_type=biz_type, project_id=project_id)
    return send_file(BytesIO(content), as_attachment=True, download_name=file_name, mimetype=content_type)
```

### Task 4: Documentation

**Files:**
- Create: `doc/信息调研Word导入导出功能修改说明.md`

- [ ] **Step 1: Document endpoints and front-end behavior**

Write the endpoint list, upload field name, import success behavior, refresh recommendations, error payload shape, and button placement.

### Task 5: Verify

**Files:**
- No code edits expected.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_flow.py::test_survey_docx_import_overwrites_lists_and_export_fills_template tests/test_flow.py::test_survey_docx_import_rejects_non_docx -q
```

Expected: both tests pass.

- [ ] **Step 2: Run broader test file**

Run:

```bash
pytest tests/test_flow.py -q
```

Expected: all tests in `tests/test_flow.py` pass.

## Self-Review

- Spec coverage: endpoints, import parsing, export generation, overwrite behavior, and front-end documentation are covered.
- Placeholder scan: no task uses TBD/TODO language.
- Type consistency: public service functions return the same tuple shape used by existing workbook services; import returns JSON-serializable dict.
