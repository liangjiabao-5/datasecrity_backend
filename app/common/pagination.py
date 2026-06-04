from flask import request


def page_args() -> tuple[int, int]:
    page_no = max(int(request.args.get("pageNo", 1)), 1)
    page_size = min(max(int(request.args.get("pageSize", 10)), 1), 200)
    return page_no, page_size


def paginate_query(query, serializer, page_no: int | None = None, page_size: int | None = None):
    if page_no is None or page_size is None:
        page_no, page_size = page_args()

    total = query.count()
    rows = query.offset((page_no - 1) * page_size).limit(page_size).all()
    return {
        "list": [serializer(row) for row in rows],
        "pageNo": page_no,
        "pageSize": page_size,
        "total": total,
    }
