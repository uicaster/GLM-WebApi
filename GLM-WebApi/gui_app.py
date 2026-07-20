"""
GLM API Proxy — GUI Application with System Tray
=================================================
Features:
  - Graphical config: token, port, model name
  - Start / Stop server with one click
  - Real-time status and log display
  - Minimize to system tray
  - Auto-load / save config.ini
"""
import os, sys, json, time, uuid, threading, queue, configparser, webbrowser, ctypes, logging
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# ── Path setup for PyInstaller ────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ── Single instance enforcement (Windows named mutex) ─────────
_MUTEX_HANDLE = None
def _ensure_single_instance():
    """Ensure only one instance of the app is running.

    Uses a Windows named mutex. If another instance holds the mutex,
    show a message and exit immediately.
    """
    global _MUTEX_HANDLE
    mutex_name = "Global\\GLM_API_Proxy_SingleInstance_v1"
    _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    # ERROR_ALREADY_EXISTS = 183
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(
            0,
            "GLM API Proxy 已经在运行中，请检查系统托盘。",
            "提示",
            0x00000040  # MB_ICONINFORMATION
        )
        sys.exit(0)

from chatglm_api import ChatGLMClient, ChatMessage, TokenExpiredError, APIError
from api_types import ApiResult
from truncate import truncate as _truncate_content, set_truncate_dir
from rate_limiter import rate_limit_middleware
from flask import Flask, Response, request
from waitress.server import create_server

# ── Config ────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(BASE_DIR), "config.ini")

# ── Request logging (for debugging CodeBuddy communication) ───
DEBUG_LOG = os.path.join(BASE_DIR, "request_debug.log")
logging.basicConfig(
    filename=DEBUG_LOG,
    level=logging.DEBUG,
    format="%(asctime)s %(message)s",
    encoding="utf-8",
)
_logger = logging.getLogger("glm_proxy")

# Global server reference for clean shutdown
_waitress_server = None


def load_config():
    cfg = configparser.ConfigParser()
    defaults = {
        "port": "5080",
        "host": "0.0.0.0",
        "refresh_token": "",
        "model_name": "chatglm-local",
        "model_display_name": "ChatGLM Local",
        "max_input_tokens": "32000",
        "max_output_tokens": "4096",
        "rate_limit": "10",
        "rate_burst": "20",
        "keepalive_interval": "20",
    }
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
        if cfg.has_section("server"):
            for key in defaults:
                if cfg.has_option("server", key):
                    defaults[key] = cfg.get("server", key)
    return defaults


def save_config(cfg_dict):
    cfg = configparser.ConfigParser()
    cfg["server"] = {
        "port": cfg_dict.get("port", "5080"),
        "host": cfg_dict.get("host", "0.0.0.0"),
        "refresh_token": cfg_dict.get("refresh_token", ""),
        "model_name": cfg_dict.get("model_name", "chatglm-local"),
        "model_display_name": cfg_dict.get("model_display_name", "ChatGLM Local"),
        "max_input_tokens": cfg_dict.get("max_input_tokens", "32000"),
        "max_output_tokens": cfg_dict.get("max_output_tokens", "4096"),
        "rate_limit": cfg_dict.get("rate_limit", "10"),
        "rate_burst": cfg_dict.get("rate_burst", "20"),
        "keepalive_interval": cfg_dict.get("keepalive_interval", "20"),
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)


# ── Flask app (built once, started/stopped on demand) ─────────
flask_app = Flask(__name__)
CURRENT_TOKEN = ""
CURRENT_MODEL = "chatglm-local"

# ── Identity keepalive counter (借鉴 openlink executor.go 每 20 次重注入) ──
_IDENTITY_REINJECT_INTERVAL = 20
_call_counter = 0
_call_counter_lock = threading.Lock()
_force_reinject_next = False


def _increment_counter() -> int:
    global _call_counter, _force_reinject_next
    with _call_counter_lock:
        _call_counter += 1
        if _call_counter >= _IDENTITY_REINJECT_INTERVAL:
            _call_counter = 0
            _force_reinject_next = True
        return _call_counter


@flask_app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Cache-Control"] = "no-cache"
    return response


@flask_app.errorhandler(404)
def _not_found(e):
    result = ApiResult(status="error", error="Endpoint not found")
    return Response(json.dumps(result.to_dict()), status=404, content_type="application/json")


@flask_app.errorhandler(405)
def _method_not_allowed(e):
    result = ApiResult(status="error", error="Method not allowed")
    return Response(json.dumps(result.to_dict()), status=405, content_type="application/json")


@flask_app.errorhandler(500)
def _internal_error(e):
    result = ApiResult(status="error", error="Internal server error")
    return Response(json.dumps(result.to_dict()), status=500, content_type="application/json")


@flask_app.route("/v1/chat/completions", methods=["OPTIONS"])
@flask_app.route("/chat/completions", methods=["OPTIONS"])
def _preflight():
    return Response("", status=204)


@flask_app.route("/health")
@flask_app.route("/v1/health")
def _health():
    result = ApiResult(
        status="success",
        output=f"GLM API Proxy is running. Token: {'configured' if CURRENT_TOKEN else 'missing'}"
    )
    return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                    content_type="application/json")


@flask_app.route("/v1/models")
def _models():
    return Response(json.dumps({"object": "list", "data": [
        {"id": CURRENT_MODEL, "object": "model", "created": 1700000000, "owned_by": "chatglm"}
    ]}), content_type="application/json")


# ── Image Understanding Endpoint (借鉴 openlink 工具类型扩展) ──
@flask_app.route("/v1/image/understand", methods=["OPTIONS"])
def _image_preflight():
    return Response("", status=204)


@flask_app.route("/v1/image/understand", methods=["POST"])
@rate_limit_middleware
def _image_understand():
    """图片理解端点 — 调用 GLM-4V 模型分析图片内容。

    请求体:
        {
            "image": "<base64 encoded image data>",
            "prompt": "请描述这张图片",
            "model": "glm-4v"  // 可选
        }

    响应格式: ApiResult 标准三元组
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            result = ApiResult(status="error", error="Invalid JSON")
            return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                            status=400, content_type="application/json")

        image_b64 = data.get("image", "")
        prompt = data.get("prompt", "请描述这张图片的内容")

        if not image_b64:
            result = ApiResult(status="error", error="Missing 'image' field (base64)")
            return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                            status=400, content_type="application/json")

        # 自动补全 data URI prefix
        if not image_b64.startswith("data:"):
            image_b64 = f"data:image/jpeg;base64,{image_b64}"

        _increment_counter()

        # 构建带图片的 chat 请求
        client = ChatGLMClient(refresh_token=CURRENT_TOKEN, timeout=120)
        try:
            resp = client.chat_image(image_b64=image_b64, prompt=prompt)
            content = resp.content or ""

            # 应用输出截断
            truncated_content, was_truncated = _truncate_content(content)
            result = ApiResult(status="success", output=truncated_content)
            if was_truncated:
                result.error = "_truncated"

            return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                            status=200, content_type="application/json")
        except Exception as e:
            result = ApiResult(status="error", error=str(e))
            return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                            status=502, content_type="application/json")

    except TokenExpiredError:
        result = ApiResult(status="error", error="Token expired. Update token in GUI.")
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=401, content_type="application/json")
    except APIError as e:
        result = ApiResult(status="error", error=str(e))
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=502, content_type="application/json")
    except Exception as e:
        _logger.exception("Image understand error")
        result = ApiResult(status="error", error=f"Internal error: {str(e)}")
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=500, content_type="application/json")


def _extract_content(content):
    """Extract text from message content (string or list of content parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(i.get("text", "") for i in content
                       if isinstance(i, dict) and i.get("type") == "text")
    return str(content)


def _parse_messages(msgs):
    """Parse the OpenAI-format messages array for multi-turn conversation.

    Returns (system_prompt, history, last_user_message) where:
      - system_prompt: the complete system prompt (external file + CodeBuddy system messages)
      - history: list of ChatMessage for all prior user/assistant turns
      - last_user_message: the final user message (without system prompt folded in)

    Multi-turn history is preserved and passed through to ChatGLMClient.chat(),
    enabling the API to maintain conversational continuity across turns.

    Identity Reinjection: every 20 API calls, the full system prompt is
    re-injected to prevent long-term behavioral drift (借鉴 openlink
    executor.go 的身份保持机制).
    """
    global _force_reinject_next

    system_parts = []
    history = []
    last_user = ""

    for m in msgs:
        role = m.get("role", "")
        content = _extract_content(m.get("content", ""))
        if not content:
            continue

        if role == "system":
            system_parts.append(content)
        elif role == "user":
            last_user = content
            history.append(ChatMessage(role="user", content=content))
        elif role == "assistant":
            history.append(ChatMessage(role="assistant", content=content))
        elif role == "tool":
            # OpenAI tool role messages — preserve as user messages
            history.append(ChatMessage(role="user", content=f"[工具返回] {content}"))

    # Remove the last user message from history (it will be the current message)
    if history and history[-1].role == "user":
        last_history = history.pop()
        last_user = last_history.content

    # Load system prompt from external file
    SYSTEM_PROMPT_PATH = os.path.join(BASE_DIR, "system_prompt.txt")
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            OVERRIDE_PROMPT = f.read().strip()
    except Exception:
        _logger.warning("system_prompt.txt not found, using built-in fallback")
        OVERRIDE_PROMPT = (
            "你是CodeBuddy IDE内置的AI编程助手，正在直接参与代码开发工作。\n"
            "你不是聊天机器人，不是顾问，不是设计文档生成器。你是代码执行者。\n\n"
            "核心行为规则（必须严格遵守）：\n"
            "1. 当收到编程任务时，直接编写完整的、可使用的代码。\n"
            "2. 不要说\"我无法访问文件\"等推脱用语。\n"
            "3. 直接输出完整代码实现，用markdown代码块包裹。\n"
            "4. 你拥有完整的编程能力。"
        )

    # Build full system prompt
    all_system = OVERRIDE_PROMPT
    if system_parts:
        all_system += "\n" + "\n\n".join(system_parts)

    # Identity reinjection: every 20 calls, force a full system prompt reminder
    # to prevent behavioral drift in long-running sessions.
    if _force_reinject_next:
        _force_reinject_next = False
        all_system = (
            "[系统提醒] 请重新确认你的角色和行为规则：\n\n"
            + all_system
            + "\n\n---\n请严格遵守以上规则继续对话。"
        )
        _logger.info("Identity reinjection triggered (call %d)", _IDENTITY_REINJECT_INTERVAL)

    # Prepend system prompt to the last user message
    if last_user:
        last_user = f"{all_system}\n\n---\n\n用户请求：\n{last_user}"

    return last_user, history


def _sse_chunk(chat_id, created, model, delta_content=None, finish_reason=None):
    delta = {}
    if delta_content is not None:
        delta["content"] = delta_content
    chunk = {
        "id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _build_nonstream_response(content, model, conversation_id=""):
    """构建非流式响应，集成输出截断。

    当 content 超过 2000 行或 50KB 时自动截断，完整内容写入 temp 文件，
    并在响应中返回文件路径供后续分段读取（借鉴 openlink 的 truncate.go）。
    """
    truncated_content, was_truncated = _truncate_content(content)
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    resp = {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": truncated_content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": None,
            "completion_tokens": len(content),
            "total_tokens": None,
        },
    }
    if conversation_id:
        resp["conversation_id"] = conversation_id
    if was_truncated:
        resp["_truncated"] = True
    return resp


def _build_stream_chunks(content, model, conversation_id=""):
    """构建流式响应生成器，集成输出截断。

    流式模式下：先生成所有 token delta chunks，最后在 [DONE] 之前
    检查是否需要附加截断提示。
    """
    truncated_content, was_truncated = _truncate_content(content)
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    first = {
        "id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
    }
    d = {"chat_id": chat_id, "created": created, "model": model, "conversation_id": conversation_id}

    # 生成器函数
    def _gen():
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
        # 分批发送内容，模拟流式token
        if truncated_content:
            chunk_size = 50
            for i in range(0, len(truncated_content), chunk_size):
                yield _sse_chunk(chat_id, created, model, delta_content=truncated_content[i:i + chunk_size])
        yield _sse_chunk(chat_id, created, model, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return _gen(), was_truncated


def _stream_response(last_user, history, model):
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    q = queue.Queue()
    sent = [""]

    def on_token(full_text):
        if full_text and full_text != sent[0]:
            delta = full_text[len(sent[0]):]
            sent[0] = full_text
            q.put(delta)

    def worker():
        try:
            client = ChatGLMClient(refresh_token=CURRENT_TOKEN, timeout=120)
            resp = client.chat(message=last_user, history=history if history else None,
                               enable_search=False, on_token=on_token)
            content = resp.content or ""
            if content and content != sent[0]:
                q.put(content[len(sent[0]):])
                sent[0] = content
            q.put(None)
        except Exception as e:
            q.put(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    first = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

    full_content = ""
    while True:
        try:
            item = q.get(timeout=180)
        except queue.Empty:
            break
        if item is None:
            yield _sse_chunk(chat_id, created, model, finish_reason="stop")
            yield "data: [DONE]\n\n"
            break
        elif isinstance(item, Exception):
            err = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model,
                   "choices": [{"index": 0, "delta": {"content": f"[Error: {item}]"}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            break
        else:
            full_content += item
            yield _sse_chunk(chat_id, created, model, delta_content=item)


def _parse_tools(tools_raw) -> list[dict]:
    """Parse OpenAI-format tools list, extracting function definitions.

    Returns a normalized list of function schemas.
    """
    if not tools_raw:
        return []
    parsed = []
    for t in tools_raw:
        if t.get("type") == "function":
            func = t.get("function", {})
            parsed.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
            })
    return parsed


def _detect_tool_calls(content: str, tools: list[dict]) -> list[dict] | None:
    """检测模型返回内容中是否包含 tool_call 指令。

    简单实现：检测 JSON 格式的 tool_call 块。生产环境建议用更严格的解析。
    """
    if not tools or not content:
        return None
    # 查找 ```tool_calls / ```function_call 等代码块
    import re
    pattern = r'```(?:tool_calls|function_call)?\s*\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)
    if not matches:
        return None
    tool_calls = []
    for idx, match_str in enumerate(matches):
        try:
            data = json.loads(match_str.strip())
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": data.get("name", "unknown"),
                    "arguments": json.dumps(data.get("arguments", data.get("parameters", {})), ensure_ascii=False),
                },
            })
        except json.JSONDecodeError:
            continue
    return tool_calls if tool_calls else None


@flask_app.route("/v1/chat/completions", methods=["POST"])
@flask_app.route("/chat/completions", methods=["POST"])
@rate_limit_middleware
def _chat():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return Response(json.dumps({"error": "Invalid JSON"}), status=400, content_type="application/json")
        msgs = data.get("messages", [])
        model = data.get("model", CURRENT_MODEL)
        stream = data.get("stream", False)
        tools_raw = data.get("tools", [])
        tools = _parse_tools(tools_raw)

        # Increment identity keepalive counter
        _increment_counter()

        # Log the raw request for debugging
        _logger.debug("=" * 60)
        _logger.debug("Incoming request: stream=%s, model=%s, messages=%d, tools=%d",
                      stream, model, len(msgs), len(tools))
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                content = str(content)[:200]
            else:
                content = str(content)[:200]
            _logger.debug("  msg[%d] role=%s: %s", i, role, content)
        _logger.debug("=" * 60)

        if not msgs:
            return Response(json.dumps({"error": "messages required"}), status=400, content_type="application/json")
        last_user, history = _parse_messages(msgs)
        if not last_user:
            return Response(json.dumps({"error": "No user message"}), status=400, content_type="application/json")

        # Inject tool descriptions into system prompt if tools are provided
        if tools:
            tool_descs = "\n".join(
                f"- {t['name']}: {t['description']}"
                + (f" (参数: {json.dumps(t['parameters'], ensure_ascii=False)})" if t.get('parameters') else "")
                for t in tools
            )
            last_user = (
                last_user
                + "\n\n---\n你可以调用以下工具来完成任务。如果需要调用，请用 ```tool_calls 代码块返回 JSON 数组："
                + "\n" + tool_descs
                + "\n每个工具调用格式：{\"name\": \"工具名\", \"arguments\": {...}}"
                + "\n如果不需要调用工具，直接正常回复即可。"
            )

        if stream:
            return Response(_stream_response(last_user, history, model),
                            content_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # ── Non-streaming path ──
        client = ChatGLMClient(refresh_token=CURRENT_TOKEN, timeout=120)
        resp = client.chat(message=last_user, history=history if history else None,
                           enable_search=False)
        content = resp.content or ""

        # Check for tool calls in the response
        tool_calls = _detect_tool_calls(content, tools) if tools else None
        conversation_id = resp.conversation_id or ""

        if tool_calls:
            # Return tool calls response
            result = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                    "finish_reason": "tool_calls",
                }],
            }
        else:
            result = _build_nonstream_response(content, model, conversation_id)

        # 返回多轮上下文信息
        result["_context"] = {
            "history_turns": len(history),
            "conversation_id": conversation_id,
            "keepalive_counter": _call_counter,
        }
        return Response(json.dumps(result, ensure_ascii=False), status=200,
                        content_type="application/json; charset=utf-8")

    except TokenExpiredError:
        result = ApiResult(status="error", error="Token expired. Update token in GUI.")
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=401, content_type="application/json")
    except APIError as e:
        result = ApiResult(status="error", error=str(e))
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=502, content_type="application/json")
    except Exception as e:
        _logger.exception("Unhandled API error")
        result = ApiResult(status="error", error=f"Internal error: {str(e)}")
        return Response(json.dumps(result.to_dict(), ensure_ascii=False),
                        status=500, content_type="application/json")


# ── GUI Application ───────────────────────────────────────────
class GLMApiGUI:
    def __init__(self):
        self.config = load_config()
        self.server_thread = None
        self.server_running = False
        self.tray_icon = None
        self.log_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("GLM API Proxy")
        self.root.geometry("660x580")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Set window + taskbar icon (same as exe and tray icon)
        self._set_window_icon()

        self._build_ui()
        self._poll_log()

        # Start minimized to tray if --minimized flag
        if "--minimized" in sys.argv:
            self.root.after(100, self._to_tray)

    def _set_window_icon(self):
        """Set the Tk window icon so it shows in the taskbar and title bar.

        Uses app_icon.ico if available (provides multiple sizes for
        title bar 16x16 and taskbar 32x32/48x48). Falls back to app_icon.png
        via iconphoto, then to the generated tray icon.
        """
        from PIL import Image, ImageTk

        # 1) Best: use .ico directly (Windows native, multi-size)
        ico_path = os.path.join(BASE_DIR, "app_icon.ico")
        if os.path.exists(ico_path):
            try:
                self.root.iconbitmap(default=ico_path)
                return
            except Exception:
                pass

        # 2) Fallback: use .png via iconphoto
        png_path = os.path.join(BASE_DIR, "app_icon.png")
        try:
            img = Image.open(png_path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            self._icon_photo = ImageTk.PhotoImage(img)  # keep reference
            self.root.iconphoto(True, self._icon_photo)
            return
        except Exception:
            pass

        # 3) Last resort: use the tray icon image
        try:
            img = self._make_tray_icon()
            self._icon_photo = ImageTk.PhotoImage(img)
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_ui(self):
        # ── Style ──
        style = ttk.Style()
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 9))
        style.configure("TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", font=("Consolas", 9))

        # ── Config Frame ──
        config_frame = ttk.LabelFrame(self.root, text=" 配置 ", padding=12)
        config_frame.pack(fill="x", padx=12, pady=(12, 6))

        # Refresh Token (multi-line Text widget with show/hide toggle)
        ttk.Label(config_frame, text="Refresh Token:").grid(row=0, column=0, sticky="nw", pady=3)

        # Token Text widget container
        token_container = ttk.Frame(config_frame)
        token_container.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)

        self.token_text = tk.Text(token_container, height=4, width=60, wrap="char",
                                   font=("Consolas", 9), undo=True,
                                   bg="#fafafa", fg="#1a1a1a", relief="sunken", borderwidth=1)
        token_scroll = ttk.Scrollbar(token_container, orient="vertical", command=self.token_text.yview)
        self.token_text.configure(yscrollcommand=token_scroll.set)
        self.token_text.pack(side="left", fill="x", expand=True)
        token_scroll.pack(side="right", fill="y")

        # Source of truth for token value
        self._token_value = self.config.get("refresh_token", "")

        # Show/hide checkbox
        self.show_token_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(config_frame, text="显示", variable=self.show_token_var,
                        command=self._toggle_token_visibility).grid(row=0, column=4, padx=(4, 0), sticky="nw", pady=3)

        # Initial render (masked by default)
        self._render_token()

        # Sync edits back to _token_value when in show mode
        self.token_text.bind("<KeyRelease>", self._sync_token_from_text)
        self.token_text.bind("<<Paste>>", lambda e: self.root.after(10, self._sync_token_from_text))

        # Port
        ttk.Label(config_frame, text="端口:").grid(row=1, column=0, sticky="w", pady=3)
        self.port_var = tk.StringVar(value=self.config.get("port", "5080"))
        ttk.Entry(config_frame, textvariable=self.port_var, width=10).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)

        # Model name
        ttk.Label(config_frame, text="模型名称:").grid(row=1, column=2, sticky="w", padx=(16, 0), pady=3)
        self.model_var = tk.StringVar(value=self.config.get("model_name", "chatglm-local"))
        ttk.Entry(config_frame, textvariable=self.model_var, width=20).grid(row=1, column=3, sticky="w", padx=(8, 0), pady=3)

        # Buttons row
        btn_frame = ttk.Frame(config_frame)
        btn_frame.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        ttk.Button(btn_frame, text="保存配置", command=self._save_config).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="获取Token帮助", command=self._show_token_help).pack(side="left", padx=(0, 8))

        config_frame.columnconfigure(1, weight=1)

        # ── Server Control Frame ──
        ctrl_frame = ttk.LabelFrame(self.root, text=" 服务控制 ", padding=12)
        ctrl_frame.pack(fill="x", padx=12, pady=6)

        self.status_label = ttk.Label(ctrl_frame, text="● 已停止", foreground="gray", font=("Microsoft YaHei UI", 10, "bold"))
        self.status_label.pack(side="left", padx=(0, 12))

        self.start_btn = ttk.Button(ctrl_frame, text="启动服务", command=self._start_server, width=12)
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(ctrl_frame, text="停止服务", command=self._stop_server, width=12, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.url_label = ttk.Label(ctrl_frame, text="", foreground="blue", cursor="hand2")
        self.url_label.pack(side="left", padx=(16, 0))
        self.url_label.bind("<Button-1>", self._open_url)

        # ── Log Frame ──
        log_frame = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=8)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 8), state="disabled",
                                                    bg="#1e1e1e", fg="#cccccc", insertbackground="#cccccc")
        self.log_text.pack(fill="both", expand=True)

        # ── Status bar ──
        self._footer = ttk.Frame(self.root, relief="sunken", borderwidth=1)
        self._footer.pack(fill="x", side="bottom")
        self.status_bar = ttk.Label(self._footer, text="就绪", anchor="w",
                                     font=("Microsoft YaHei UI", 8))
        self.status_bar.pack(side="left", padx=(6, 0), pady=2)
        self.copyright_label = ttk.Label(
            self._footer,
            text="版权方：上海市宝山区千语网络科技工作室    v1.1.0 20260720",
            anchor="e",
            foreground="gray",
            font=("Microsoft YaHei UI", 7),
        )
        self.copyright_label.pack(side="right", padx=(0, 8), pady=2)

    def _toggle_token_visibility(self):
        """Toggle between masked asterisks and real token text.

        Key insight: show_token_var has ALREADY been updated to the NEW state
        by the Checkbutton before this callback runs. So:
          - If NEW state is True  (switching hide→show): text currently holds
            asterisks → must NOT sync, otherwise we overwrite the real token
            with asterisks.
          - If NEW state is False (switching show→hide): text currently holds
            the real token (user may have edited it) → should sync before
            masking.

        We determine the PREVIOUS display state by checking the widget's
        actual state config: 'normal' means it was editable (show mode),
        'disabled' means it was readonly (hide mode).
        """
        if str(self.token_text.cget("state")) == "normal":
            # Was in show mode — text holds the real token, sync any edits
            self._token_value = self.token_text.get("1.0", "end-1c")
        self._render_token()

    def _render_token(self):
        """Re-render the token Text widget based on show/hide state."""
        # Remember cursor position
        try:
            cursor_pos = self.token_text.index("insert")
        except Exception:
            cursor_pos = "end"

        self.token_text.config(state="normal")
        self.token_text.delete("1.0", "end")

        if self.show_token_var.get():
            # Show real token
            self.token_text.insert("1.0", self._token_value)
            self.token_text.config(fg="#1a1a1a", state="normal")
        else:
            # Show masked asterisks, readonly
            masked = "*" * len(self._token_value)
            self.token_text.insert("1.0", masked)
            self.token_text.config(fg="#888888", state="disabled")

        try:
            self.token_text.see(cursor_pos)
        except Exception:
            pass

    def _sync_token_from_text(self, event=None):
        """Sync the Text widget content back to _token_value.

        Only syncs when the widget is in 'normal' state (show mode),
        because in 'disabled' state (hide mode) the text contains
        asterisks, not the real token.
        """
        if str(self.token_text.cget("state")) == "normal":
            self._token_value = self.token_text.get("1.0", "end-1c")

    def _log(self, msg):
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _poll_log(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", line)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def _save_config(self):
        self.config["refresh_token"] = self._token_value.strip()
        self.config["port"] = self.port_var.get().strip()
        self.config["model_name"] = self.model_var.get().strip()
        save_config(self.config)
        self._log("配置已保存到 config.ini")
        self.status_bar.config(text="配置已保存")

    def _start_server(self):
        global CURRENT_TOKEN, CURRENT_MODEL

        token = self._token_value.strip()
        if not token:
            messagebox.showwarning("警告", "请先填写 Refresh Token")
            return

        port = int(self.port_var.get().strip() or "5080")
        CURRENT_TOKEN = token
        CURRENT_MODEL = self.model_var.get().strip() or "chatglm-local"

        # 初始化截断文件目录（借用工作会话 temp 目录模式）
        set_truncate_dir(os.path.join(BASE_DIR, ".glm_api_truncated"))

        # Save config before starting
        self._save_config()

        def run_server():
            global _waitress_server
            try:
                _waitress_server = create_server(flask_app, host="0.0.0.0", port=port, threads=8)
                _waitress_server.run()
            except Exception as e:
                self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] 服务异常: {e}\n")

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.server_running = True

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="● 运行中", foreground="green")
        url = f"http://127.0.0.1:{port}"
        self.url_label.config(text=url)
        self.status_bar.config(text=f"服务运行中 — {url}")
        self._log(f"服务已启动 — {url}")
        self._log(f"API: {url}/v1/chat/completions  |  Health: {url}/health")
        self._log(f"限流: {self.config.get('rate_limit', '10')} req/s | 身份保持: 每 {self.config.get('keepalive_interval', '20')} 次")

        if self.tray_icon:
            self.tray_icon.update_menu()

    def _stop_server(self):
        global _waitress_server
        if _waitress_server:
            try:
                _waitress_server.close()
                _waitress_server = None
            except Exception:
                pass

        self.server_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="● 已停止", foreground="gray")
        self.url_label.config(text="")
        self.status_bar.config(text="服务已停止")
        self._log("服务已停止")

        if self.tray_icon:
            self.tray_icon.update_menu()

    def _open_url(self, event=None):
        url = self.url_label.cget("text")
        if url:
            webbrowser.open(url)

    def _show_token_help(self):
        help_text = """获取 Refresh Token 步骤：

1. 浏览器打开 https://chatglm.cn/ 并登录
2. 按 F12 打开开发者工具
3. 切换到 Application（应用程序）标签
4. 左侧展开 Cookies → 选择 https://chatglm.cn
5. 找到 chatglm_refresh_token，复制其 Value
6. 粘贴到上方的 Token 输入框

或者在浏览器控制台执行：
document.cookie.match(/chatglm_refresh_token=([^;]+)/)[1]
"""
        messagebox.showinfo("获取 Token 帮助", help_text)

    # ── System Tray ───────────────────────────────────────────
    def _make_tray_icon(self):
        """Create the tray icon image using the same icon as the exe.

        Loads app_icon.png (64x64 RGBA) from the exe directory; falls back
        to app_icon.ico; finally falls back to a generated green 'G' icon.
        """
        from PIL import Image, ImageDraw, ImageFont

        # 1) Try app_icon.png (most reliable for pystray)
        png_path = os.path.join(BASE_DIR, "app_icon.png")
        try:
            img = Image.open(png_path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            if img.size != (64, 64):
                img = img.resize((64, 64), Image.Resampling.LANCZOS)
            return img
        except Exception:
            pass

        # 2) Try app_icon.ico — pick the 32x32 frame (best for tray)
        ico_path = os.path.join(BASE_DIR, "app_icon.ico")
        try:
            img = Image.open(ico_path)
            # Select the best available frame
            for target in [(32, 32), (48, 48), (64, 64), (16, 16)]:
                try:
                    img.size = target
                    break
                except Exception:
                    continue
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img = img.resize((64, 64), Image.Resampling.LANCZOS)
            return img
        except Exception:
            pass

        # 3) Fallback: generate a green 'G' icon
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=14, fill=(16, 185, 129, 255))
        try:
            font = ImageFont.truetype("arial.ttf", 38)
        except Exception:
            font = ImageFont.load_default()
        draw.text((17, 12), "G", fill=(255, 255, 255, 255), font=font)
        return img

    def _setup_tray(self):
        import pystray

        img = self._make_tray_icon()

        def on_tray_click(icon, item):
            self._show_window()

        def on_tray_doubleclick(icon, item):
            self._show_window()

        def on_quit(icon, item):
            # Stop server first if running
            if _waitress_server:
                try:
                    _waitress_server.close()
                except Exception:
                    pass
            icon.stop()
            self.root.after(0, self._real_quit)

        def on_toggle(icon, item):
            if self.server_running:
                self.root.after(0, self._stop_server)
            else:
                self.root.after(0, self._start_server)

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", on_tray_click, default=True),
            pystray.MenuItem(
                lambda icon: "停止服务" if self.server_running else "启动服务",
                on_toggle,
            ),
            pystray.MenuItem("退出", on_quit),
        )

        self.tray_icon = pystray.Icon("glm_api", img, "GLM API Proxy", menu)
        # Run tray in background thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _to_tray(self):
        if not self.tray_icon:
            self._setup_tray()
        self.root.withdraw()

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self):
        # Minimize to tray instead of closing
        self._to_tray()

    def _real_quit(self):
        self.root.destroy()

    def run(self):
        # Setup tray on startup
        self._setup_tray()
        self._log("GLM API Proxy 已启动")
        self._log(f"配置文件: {CONFIG_PATH}")
        token_set = bool(self._token_value.strip())
        self._log(f"Token: {'已配置' if token_set else '未配置 — 请填写'}")
        self.root.mainloop()


if __name__ == "__main__":
    # Enforce single instance before any GUI is created
    _ensure_single_instance()
    app_gui = GLMApiGUI()
    app_gui.run()
