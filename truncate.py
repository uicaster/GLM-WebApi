"""
输出截断模块。
借鉴 openlink 项目 truncate.go 的设计：MaxLines=2000, MaxBytes=50KB。
超限时写入 temp 文件并返回截断提示 + 文件路径。
"""
import os
import time
from pathlib import Path

# 与 openlink 的 const MaxLines/MaxBytes 对齐
MAX_LINES = 2000
MAX_BYTES = 50 * 1024

# temp 文件存储目录
_truncate_dir: str = ""


def set_truncate_dir(base_dir: str):
    """设置截断文件的存储目录（会话 temp 目录）。"""
    global _truncate_dir
    _truncate_dir = os.path.join(base_dir, "truncated")
    os.makedirs(_truncate_dir, exist_ok=True)


def truncate(content: str) -> tuple[str, bool]:
    """检查内容是否超限。

    Returns:
        (processed_content, was_truncated)
        若未超限，was_truncated=False，直接返回原内容。
        若超限，was_truncated=True，返回 截断预览 + 保存路径提示。
    """
    normalized = content.replace("\r\n", "\n")
    lines = normalized.split("\n")
    byte_len = len(normalized.encode("utf-8"))

    if len(lines) <= MAX_LINES and byte_len <= MAX_BYTES:
        return content, False

    # 计算截断
    end = min(MAX_LINES, len(lines))
    preview = "\n".join(lines[:end])
    if len(preview.encode("utf-8")) > MAX_BYTES:
        preview = preview[:MAX_BYTES]

    # 写入 temp 文件
    if not _truncate_dir:
        home = os.path.expanduser("~")
        _td = os.path.join(home, ".glm_api_proxy", "truncated")
    else:
        _td = _truncate_dir
    os.makedirs(_td, exist_ok=True)

    file_name = f"output_{int(time.time() * 1000000)}.txt"
    full_path = os.path.join(_td, file_name)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    hint = (
        f"\n\n---\n"
        f"[输出已截断] 共 {len(lines)} 行 / {byte_len} 字节。\n"
        f"完整内容保存至: {full_path}\n"
        f"使用文件读工具以 offset 分段读取剩余部分。"
    )
    return preview + hint, True
