"""
标准化 API 响应类型。
参考 openlink 项目的 types.go 设计：统一使用 Status/Output/Error 三元组。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApiResult:
    """工具/API 调用的标准化结果。

    对应 openlink 的 types.ToolResponse:
      - status: "success" | "error"
      - output: 成功时的返回数据
      - error: 失败时的错误描述
    """
    status: str = "success"
    output: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        d = {"status": self.status}
        if self.output:
            d["output"] = self.output
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class StreamChunk:
    """SSE 流式响应块"""
    chat_id: str
    created: int
    model: str
    delta_content: Optional[str] = None
    finish_reason: Optional[str] = None

    def to_sse(self) -> str:
        import json
        delta = {}
        if self.delta_content is not None:
            delta["content"] = self.delta_content
        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": self.finish_reason}],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
