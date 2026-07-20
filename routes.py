"""
Flask API 路由模块 —— 从 gui_app.py 拆分出来，独立管理所有端点。

参照 openlink 项目的 server.go 分层设计：
  server.go → 路由注册（/health, /auth, /config, /tools, /exec, /prompt, /skills, /files）
  本模块 → 路由注册（/v1/chat/completions, /health, /models）

用于改善原 gui_app.py 单文件 758 行的维护困难问题。
"""
import logging
from flask import Flask, request, jsonify, Response, stream_with_context

from chatglm_api import ChatGLMClient
from api_types import ApiResult

logger = logging.getLogger(__name__)


def register_routes(app: Flask, client: ChatGLMClient, assistant_id: str):
    """向 Flask app 注册所有 API 路由。

    Args:
        app: Flask 应用实例
        client: ChatGLM API 客户端
        assistant_id: 助手 ID（从配置读取）
    """

    @app.route("/health", methods=["GET"])
    def health():
        """健康检查端点。参考 openlink 的 /health 端点设计。"""
        return jsonify({"status": "ok", "service": "GLM Api Proxy"})

    @app.route("/v1/models", methods=["GET"])
    def list_models():
        """OpenAI 兼容的模型列表端点。"""
        return jsonify({
            "object": "list",
            "data": [
                {"id": "glm-4", "object": "model", "created": 1700000000, "owned_by": "glm-api-proxy"},
                {"id": "glm-4-flash", "object": "model", "created": 1700000000, "owned_by": "glm-api-proxy"},
            ],
        })

    @app.route("/v1/chat/completions", methods=["POST"])
    def chat_completions():
        """OpenAI 兼容的对话补全端点。

        支持 stream 和非 stream 模式。
        借鉴 openlink 的 /exec 端点：统一的 JSON 入参 → 标准化 JSON 输出。
        """
        data = request.get_json(force=True)

        if not data or "messages" not in data:
            result = ApiResult(status="error", error="missing 'messages' in request body")
            return jsonify(result.to_dict()), 400

        messages = data["messages"]
        model = data.get("model", "glm-4")
        temperature = data.get("temperature", 0.6)
        stream = data.get("stream", False)
        max_tokens = data.get("max_tokens", 4096)

        logger.info(
            f"API Request | model={model} stream={stream} "
            f"messages={len(messages)} temp={temperature}"
        )

        try:
            if stream:
                return _handle_stream(client, messages, model, temperature, max_tokens)
            else:
                return _handle_non_stream(client, messages, model, temperature, max_tokens)
        except client.TokenExpiredError:
            result = ApiResult(status="error", error="ChatGLM token expired, please refresh")
            return jsonify(result.to_dict()), 401
        except client.APIError as e:
            result = ApiResult(status="error", error=f"ChatGLM API error: {str(e)}")
            return jsonify(result.to_dict()), 502
        except Exception as e:
            logger.exception("Unhandled error in chat_completions")
            result = ApiResult(status="error", error=f"internal server error: {str(e)}")
            return jsonify(result.to_dict()), 500

    return app


def _handle_non_stream(client, messages, model, temperature, max_tokens):
    """非流式响应处理"""
    import time
    import json
    from flask import jsonify

    response = client.chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    return jsonify(response)


def _handle_stream(client, messages, model, temperature, max_tokens):
    """流式响应处理 —— SSE"""
    import time
    from flask import Response, stream_with_context
    from api_types import StreamChunk

    def generate():
        try:
            for chunk in client.chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ):
                sse = chunk.to_sse()
                yield sse.encode("utf-8")
            yield "data: [DONE]\n\n".encode("utf-8")
        except Exception as e:
            import json
            error_chunk = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {error_chunk}\n\n".encode("utf-8")
            yield "data: [DONE]\n\n".encode("utf-8")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
