"""
令牌桶限流中间件。
借鉴 openlink 项目 server.go 的节流设计，使用令牌桶算法。

默认配置：每秒 10 请求，突发 20 请求。
可通过环境变量 GLM_RATE_LIMIT（请求/秒）和 GLM_BURST（突发数）调整。
"""
import os
import time
import threading
import logging

logger = logging.getLogger("glm_proxy.rate_limiter")


class TokenBucket:
    """线程安全的令牌桶。

    参数:
        rate:  每秒填充的令牌数（默认 10）
        burst: 桶容量（最大令牌数，默认 20）
    """

    def __init__(self, rate: float = 10, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_fill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """尝试消费 tokens 个令牌。成功返回 True，否则 False。"""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_fill
        if elapsed > 0:
            self._tokens = min(self._burst, self._tokens + elapsed * self.rate)
        self._last_fill = now

    @property
    def _burst(self):
        return self.burst


# 全局单例：从环境变量读取配置
_RATE = float(os.environ.get("GLM_RATE_LIMIT", "10"))
_BURST = int(os.environ.get("GLM_BURST", "20"))
_bucket = TokenBucket(rate=_RATE, burst=_BURST)


def rate_limit_middleware(handler):
    """Flask 限流装饰器。

    对每个请求消费 1 个令牌。令牌不足时返回 429 Too Many Requests。
    """

    from functools import wraps
    from flask import Response
    import json

    @wraps(handler)
    def wrapper(*args, **kwargs):
        if not _bucket.consume():
            err = {"status": "error", "error": "Rate limit exceeded. Try again later."}
            return Response(
                json.dumps(err, ensure_ascii=False),
                status=429,
                content_type="application/json",
                headers={"Retry-After": "1"},
            )
        return handler(*args, **kwargs)

    return wrapper
