"""本地来源校验。

服务面向单机（127.0.0.1），但浏览器里的任意网页都可以向 localhost
发请求，而 SOP/CORS 只阻止"读取"响应，不阻止"发出"请求。这里对写操作
统一校验 Origin 头：只要来源不是本机（127.0.0.1 / localhost）就拒绝。
curl 等无 Origin 头的非浏览器客户端不受影响。
"""

from fastapi import HTTPException, Request

_ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def verify_local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        host = origin.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
    except IndexError:
        raise HTTPException(403, "拒绝跨站请求：来源无效")
    if host not in _ALLOWED_ORIGIN_HOSTS:
        raise HTTPException(403, "拒绝跨站请求：来源不是本机")
