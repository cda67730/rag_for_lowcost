"""後台 API 的錯誤格式：HTTP 狀態碼＋{error, code[, fields]}（docs/admin-api.md 通則）。"""
from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, error: str, fields: dict | None = None):
        self.status, self.code, self.error, self.fields = status, code, error, fields


def not_found() -> ApiError:
    """不存在與看不到不區分，避免被用來探測"""
    return ApiError(404, "not_found", "找不到")


def forbidden() -> ApiError:
    return ApiError(403, "forbidden", "沒有權限")


async def handler(request: Request, exc: ApiError):
    body = {"error": exc.error, "code": exc.code}
    if exc.fields:
        body["fields"] = exc.fields
    return JSONResponse(body, status_code=exc.status)
