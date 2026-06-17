class BusinessError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.data = data


class NotFoundError(BusinessError):
    def __init__(self, message: str = "Resource not found."):
        super().__init__("NOT_FOUND", message, 404)
