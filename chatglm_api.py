"""
ChatGLM (智谱清言) Web API 封装
===============================
逆向 chatglm.cn 网页版对话接口，封装为可调用的 Python API。

认证方式：从浏览器 Cookie 获取 chatglm_refresh_token
"""

import hashlib
import json
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

import requests

logger = logging.getLogger("chatglm_api")

API_BASE = "https://chatglm.cn"
REFRESH_URL = f"{API_BASE}/chatglm/user-api/user/refresh"
STREAM_URL = f"{API_BASE}/chatglm/backend-api/assistant/stream"
DEFAULT_ASSISTANT_ID = "65940acff94777010aa6b796"
DEFAULT_SIGN_SECRET = "8a1317a7468aa3ad86e997d08f3f31cb"
ACCESS_TOKEN_EXPIRES = 3600
DEFAULT_TIMEOUT = 120

FAKE_HEADERS = {
    "Accept": "text/event-stream",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "App-Name": "chatglm",
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
    "Origin": "https://chatglm.cn",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-App-Fr": "browser_extension",
    "X-App-Platform": "pc",
    "X-App-Version": "0.0.1",
    "X-Lang": "zh",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
    ),
}


def _uuid_hex() -> str:
    return _uuid.uuid4().hex


def _generate_sign(secret: str = DEFAULT_SIGN_SECRET) -> dict:
    e = int(time.time() * 1000)
    A = str(e)
    t = len(A)
    digits = [int(c) for c in A]
    sum_digits = sum(digits)
    a = (sum_digits - digits[t - 2]) % 10
    timestamp_str = A[: t - 2] + str(a) + A[t - 1:]
    nonce = _uuid_hex()
    raw = f"{timestamp_str}-{nonce}-{secret}"
    sign = hashlib.md5(raw.encode()).hexdigest()
    return {"timestamp": timestamp_str, "nonce": nonce, "sign": sign}


def _make_headers(token: str) -> dict:
    sign_data = _generate_sign()
    return {
        **FAKE_HEADERS,
        "Authorization": f"Bearer {token}",
        "X-Device-Id": _uuid_hex(),
        "X-Request-Id": _uuid_hex(),
        "X-Sign": sign_data["sign"],
        "X-Timestamp": sign_data["timestamp"],
        "X-Nonce": sign_data["nonce"],
    }


def _make_refresh_headers(refresh_token: str) -> dict:
    sign_data = _generate_sign()
    return {
        **FAKE_HEADERS,
        "Authorization": f"Bearer {refresh_token}",
        "X-Device-Id": _uuid_hex(),
        "X-Nonce": sign_data["nonce"],
        "X-Request-Id": _uuid_hex(),
        "X-Sign": sign_data["sign"],
        "X-Timestamp": str(sign_data["timestamp"]),
    }


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatResponse:
    conversation_id: str = ""
    content: str = ""
    reasoning_content: Optional[str] = None
    finish_reason: str = "stop"
    model: str = "glm"
    raw: dict = field(default_factory=dict)


class TokenCache:
    def __init__(self):
        self._cache: dict[str, dict] = {}

    def get(self, refresh_token: str) -> Optional[str]:
        entry = self._cache.get(refresh_token)
        if entry and entry["expires_at"] > time.time():
            return entry["access_token"]
        return None

    def set(self, refresh_token: str, access_token: str):
        self._cache[refresh_token] = {
            "access_token": access_token,
            "expires_at": time.time() + ACCESS_TOKEN_EXPIRES - 60,
        }

    def clear(self, refresh_token: str):
        self._cache.pop(refresh_token, None)


class ChatGLMClient:
    def __init__(
        self,
        refresh_token: str,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
        sign_secret: str = DEFAULT_SIGN_SECRET,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 3,
    ):
        self.refresh_token = refresh_token
        self.assistant_id = assistant_id
        self.sign_secret = sign_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self._token_cache = TokenCache()
        self._session = requests.Session()

    def _acquire_token(self) -> str:
        is_guest = "is_guest" in self.refresh_token or "true" in self.refresh_token.lower()
        if not is_guest:
            cached = self._token_cache.get(self.refresh_token)
            if cached:
                return cached

        for attempt in range(self.max_retries):
            try:
                token_data = self._refresh_token()
                self._token_cache.set(self.refresh_token, token_data["access_token"])
                return token_data["access_token"]
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning(f"Token refresh attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def _refresh_token(self) -> dict:
        headers = _make_refresh_headers(self.refresh_token)
        resp = self._session.post(REFRESH_URL, headers=headers, timeout=15)
        raw_text = resp.text
        logger.debug(f"Refresh response [{resp.status_code}]: {raw_text[:300]}")
        try:
            data = resp.json()
        except Exception:
            raise APIError(f"Token refresh invalid [{resp.status_code}]: {raw_text[:200]}")
        code = data.get("code") or data.get("status")
        msg = data.get("message", "")

        if code == 0:
            result = data.get("result", {})
            return {
                "access_token": result["access_token"],
                "refresh_token": result.get("refresh_token", self.refresh_token),
            }
        elif code == 401 or "40102" in str(msg):
            self._token_cache.clear(self.refresh_token)
            raise TokenExpiredError(f"refresh_token expired: {msg}")
        else:
            raise APIError(f"Token refresh failed [{code}]: {msg}")

    def check_token_alive(self) -> bool:
        try:
            self._refresh_token()
            return True
        except TokenExpiredError:
            return False
        except Exception:
            return False

    def chat_image(
        self,
        image_b64: str,
        prompt: str = "请描述这张图片的内容",
        on_token: Optional[Callable[[str], None]] = None,
    ) -> ChatResponse:
        """图片理解 — 调用 GLM-4V 多模态能力。

        Args:
            image_b64: base64 编码的图片数据（含 data URI prefix）
            prompt: 针对图片的提问
            on_token: 可选的回调函数，每收到新 token 时调用

        Returns:
            ChatResponse，content 字段包含模型对图片的描述。
        """
        access_token = self._acquire_token()

        # 构建多模态消息体：文本 + 图片
        content_parts = [{"type": "text", "text": prompt}]
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": image_b64},
        })

        chat_messages = [{
            "role": "user",
            "content": content_parts,
        }]

        body = {
            "assistant_id": self.assistant_id,
            "conversation_id": "",
            "project_id": "",
            "chat_type": "user_chat",
            "messages": chat_messages,
            "meta_data": {
                "channel": "",
                "chat_mode": "common",
                "draft_id": "",
                "if_plus_model": True,
                "input_question_type": "xxxx",
                "is_networking": False,
                "is_test": False,
                "platform": "pc",
                "quote_log_id": "",
            },
        }

        referer = "https://chatglm.cn/main/alltoolsdetail"
        headers = _make_headers(access_token)
        headers["Referer"] = referer

        response = ChatResponse(model="glm-4v")
        full_content = ""

        try:
            resp = self._session.post(
                STREAM_URL, headers=headers, json=body, stream=True, timeout=self.timeout,
            )

            if resp.status_code != 200:
                try:
                    data = resp.json()
                    self._handle_api_error(data.get("message", str(data)))
                except json.JSONDecodeError:
                    raise APIError(f"Image API error [{resp.status_code}]: {resp.text[:200]}")
                return response

            buffer = ""
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk if isinstance(chunk, str) else chunk.decode("utf-8")

                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    lines = line.strip().split("\n")
                    for l in lines:
                        if l.startswith("data: ") and l[6:].strip() != "[DONE]":
                            try:
                                event_data = json.loads(l[6:])
                                if "parts" in event_data:
                                    for part in event_data["parts"]:
                                        for item in part.get("content", []):
                                            if item.get("type") == "text":
                                                text = item.get("text", "")
                                                if part.get("status") == "finish":
                                                    full_content = text
                                                elif text and text != full_content:
                                                    full_content = text
                                                    if on_token:
                                                        on_token(text)
                            except json.JSONDecodeError:
                                pass

            response.content = full_content
            return response

        except requests.Timeout:
            raise APIError(f"Image request timeout ({self.timeout}s)")
        except requests.ConnectionError as e:
            raise APIError(f"Connection failed: {e}")

    def chat(
        self,
        message: str,
        history: Optional[list[ChatMessage]] = None,
        conversation_id: Optional[str] = None,
        chat_mode: Optional[str] = None,
        enable_search: bool = True,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> ChatResponse:
        access_token = self._acquire_token()

        all_messages = []
        if history:
            all_messages.extend(history)
        all_messages.append(ChatMessage(role="user", content=message))

        chat_messages = []
        for m in all_messages:
            chat_messages.append({
                "role": m.role,
                "content": [{"type": "text", "text": m.content}],
            })

        body = {
            "assistant_id": self.assistant_id,
            "conversation_id": conversation_id or "",
            "project_id": "",
            "chat_type": "user_chat",
            "messages": chat_messages,
            "meta_data": {
                "channel": "",
                "chat_mode": chat_mode or "common",
                "draft_id": "",
                "if_plus_model": True,
                "input_question_type": "xxxx",
                "is_networking": enable_search,
                "is_test": False,
                "platform": "pc",
                "quote_log_id": "",
            },
        }

        referer = "https://chatglm.cn/main/alltoolsdetail"
        headers = _make_headers(access_token)
        headers["Referer"] = referer

        response = ChatResponse(model="glm")
        full_content = ""

        try:
            resp = self._session.post(
                STREAM_URL, headers=headers, json=body, stream=True, timeout=self.timeout,
            )

            if resp.status_code != 200:
                data = resp.json()
                logger.error(f"Chat API error [{resp.status_code}]: {json.dumps(data, ensure_ascii=False)[:500]}")
                self._handle_api_error(data.get("message", str(data)))
                return response

            ct = resp.headers.get("content-type", "")
            if "text/event-stream" not in ct:
                try:
                    data = resp.json()
                    msg = data.get("message", "") or data.get("status", "")
                    logger.error(f"Chat API JSON error: {json.dumps(data, ensure_ascii=False)[:500]}")
                    if data.get("status") == 10061:
                        raise APIError("ChatGLM concurrency limit")
                    raise APIError(f"ChatGLM API error: {msg}")
                except json.JSONDecodeError:
                    raise APIError(f"Unexpected response [{ct}]: {resp.text[:200]}")

            buffer = ""
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk if isinstance(chunk, str) else chunk.decode("utf-8")

                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    lines = line.strip().split("\n")
                    for l in lines:
                        if l.startswith("data: "):
                            data_str = l[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                event_data = json.loads(data_str)
                                response.raw = event_data

                                if "parts" in event_data:
                                    parts = event_data["parts"]
                                    for part in parts:
                                        content_items = part.get("content", [])
                                        if isinstance(content_items, list):
                                            for item in content_items:
                                                if item.get("type") == "text":
                                                    text = item.get("text", "")
                                                    part_status = part.get("status", "")
                                                    if part_status == "finish":
                                                        response.finish_reason = "stop"
                                                        if text:
                                                            full_content = text
                                                    elif text and text != full_content:
                                                        full_content = text
                                                        if on_token:
                                                            on_token(text)

                                        if part.get("conversation_id"):
                                            response.conversation_id = part["conversation_id"]

                                        if part.get("status") == "finish":
                                            response.finish_reason = "stop"

                                if event_data.get("conversation_id") and not response.conversation_id:
                                    response.conversation_id = event_data["conversation_id"]

                                if "result" in event_data:
                                    result = event_data["result"]
                                    if result.get("status") == "finish":
                                        response.finish_reason = "stop"
                                        response.conversation_id = result.get("conversation_id", response.conversation_id)
                                        final_content = result.get("content", "")
                                        if final_content:
                                            full_content = final_content

                            except json.JSONDecodeError:
                                logger.debug(f"SSE parse error: {data_str[:100]}")

            response.content = full_content
            return response

        except requests.Timeout:
            raise APIError(f"Request timeout ({self.timeout}s)")
        except requests.ConnectionError as e:
            raise APIError(f"Connection failed: {e}")

    def _handle_api_error(self, message: str):
        if "40102" in message or "过期" in message:
            self._token_cache.clear(self.refresh_token)
            raise TokenExpiredError(f"Token expired: {message}")
        raise APIError(f"API error: {message}")


class ChatGLMError(Exception):
    pass


class TokenExpiredError(ChatGLMError):
    pass


class APIError(ChatGLMError):
    pass
