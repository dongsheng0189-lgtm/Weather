"""icinfo 模組自訂例外。"""


class IcinfoError(Exception):
    """icinfo 模組基礎例外。"""


class CaptchaExpired(IcinfoError):
    """Session 中沒 JSESSIONID（使用者沒先 GET captcha 就 POST search，或 session 過期）。"""


class UpstreamError(IcinfoError):
    """上游移民署 API 錯誤（5xx、timeout、格式異常）。"""
