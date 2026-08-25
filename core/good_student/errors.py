"""统一业务错误：code 进入 tool envelope 的 error.code。"""


class GoodStudentError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
