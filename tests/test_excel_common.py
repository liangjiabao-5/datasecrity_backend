from io import BytesIO

from openpyxl import Workbook
from werkzeug.datastructures import FileStorage

from app.services.excel_common import load_import_workbook


class SequentialOnlyUploadStream:
    def __init__(self, content: bytes):
        self._stream = BytesIO(content)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        if offset == 0 and whence == 0:
            return self._stream.seek(offset, whence)
        raise OSError("random access is not supported")


def _workbook_bytes() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "ok"
    workbook.save(stream)
    return stream.getvalue()


def test_load_import_workbook_reads_upload_content_into_seekable_memory_stream():
    uploaded = FileStorage(
        stream=SequentialOnlyUploadStream(_workbook_bytes()),
        filename="upload.xlsx",
    )

    workbook = load_import_workbook(uploaded)

    assert workbook.active["A1"].value == "ok"
