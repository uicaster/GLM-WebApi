# GLM-WebApi v1.1.0

OpenAI 兼容的 ChatGLM 网页版 API 代理服务。通过逆向 chatglm.cn 网页版对话接口，将其封装为标准的 OpenAI `/v1/chat/completions` API，支持流式（SSE）和非流式两种响应模式，可直接对接 CodeBuddy、ChatGPT-Next-Web、LobeChat 等客户端。

注意：本项目仅用于学习研究，严禁用于商业用途。
Notice: This project is for learning and research purposes only. Commercial use is strictly prohibited.

## 目录结构

```
GLM Api/
├── GLM-WebApi/             # 项目主目录
│   ├── GLM_Api.exe         # GUI 可执行文件（打包产物）
│   ├── gui_app.py          # GUI 版入口（Tkinter + 系统托盘）
│   ├── chatglm_api.py      # ChatGLM 逆向 API 客户端
│   ├── api_types.py         # 标准化响应类型
│   ├── routes.py            # 模块化路由（备选/参考）
│   ├── truncate.py          # 输出截断模块
│   ├── rate_limiter.py      # 令牌桶限流中间件
│   ├── system_prompt.txt    # 外部化系统提示词
│   ├── config.example.ini   # 配置文件模板
│   ├── app_icon.ico         # 应用图标
│   └── _internal/           # PyInstaller 打包依赖
├── project_memory.md        # 开发日志 & 思路备忘
├── README.md                # 本文件
├── .gitignore
├── config.ini               # 用户配置文件
├── requirements.txt
├── build_gui.py             # GUI 版打包脚本
└── GLM-WebApi.rar           # 离线备份压缩包
```

## 快速开始

### 方式一：使用 GUI 版（推荐）

1. 进入 `GLM-WebApi/` 目录
2. 双击 `GLM_Api.exe`
3. 在界面中填入 Refresh Token，点击「启动服务」
4. 关闭窗口时自动最小化到系统托盘

### 方式二：从源码运行

```bash
# 安装依赖
pip install flask waitress requests pystray Pillow

# 启动 GUI 服务
cd GLM-WebApi/
python gui_app.py
```

### 方式三：重新打包

```bash
pip install pyinstaller flask waitress requests pystray Pillow

# 进入项目目录
cd GLM-WebApi/

# 打包（含完整依赖）
pyinstaller --onedir --windowed --icon=app_icon.ico --name=GLM_Api --add-data "system_prompt.txt;." --add-data "config.example.ini;." --collect-all=waitress --collect-all=pystray --collect-all=PIL --collect-all=requests --collect-all=flask --noconfirm --distpath . gui_app.py
```

## GUI 版功能

| 功能 | 说明 |
|------|------|
| Token 配置 | 界面输入框，支持显示/隐藏切换 |
| 启动/停止服务 | 一键按钮，实时状态指示（绿色运行/灰色停止） |
| 端口/模型名配置 | 界面直接修改，保存到 config.ini |
| 运行日志 | 实时显示请求和响应日志 |
| 系统托盘 | 关闭窗口自动最小化到托盘，右键菜单可启动/停止/退出 |
| 点击 URL 打开浏览器 | 运行中的服务地址可点击直接访问 |
| 版权信息 | 右下角显示版权方和版本号 |

## Changelog

### v1.1.0 (2026-07-20)

- GUI 右下角添加版权信息"版权方：上海市宝山区千语网络科技工作室"及版本号 v1.1.0
- 修复 PyInstaller 打包缺失 `requests` / `flask` 依赖的问题
- 项目重命名为 GLM-WebApi，整理目录结构（源码统一收进 `GLM-WebApi/`）
- 上传 GitHub，根目录留存 `GLM-WebApi.rar` 离线备份

### v1.0.0 (2026-07-20)

- 多轮对话历史传递
- 图片理解支持（GLM-4V 视觉模型）
- Function Calling 支持（软实现，prompt 引导 + JSON 解析）
- 输出截断 + temp 文件保存（防上下文爆炸）
- 请求频率限制中间件（令牌桶算法）
- 身份保持计数器（防 AI 行为退化）
- 提示词外部化（`system_prompt.txt`）
- 标准化响应类型（`ApiResult` / `StreamChunk`）
- 模块化路由拆分
- GUI 版（Tkinter + 系统托盘）打包为独立 exe

## 配置说明

编辑 `config.ini`：

```ini
[server]
# 服务端口
port = 5080

# 绑定地址（0.0.0.0 = 所有网卡）
host = 0.0.0.0

# ChatGLM refresh_token
# 获取方式：登录 chatglm.cn → F12 → Application → Cookies → chatglm_refresh_token
refresh_token = eyJhbGciOiJI...

# 模型标识符（API 响应中返回的 model 字段）
model_name = chatglm-local

# 最大输入/输出 token
max_input_tokens = 32000
max_output_tokens = 4096
```

## API 端点

### POST `/v1/chat/completions`

OpenAI 兼容的对话接口，支持流式和非流式两种模式。

**请求体：**

```json
{
  "model": "chatglm-local",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "stream": false
}
```

**非流式响应（`stream: false`）：**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1782356957,
  "model": "chatglm-local",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "你好！有什么可以帮你的？"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 2, "completion_tokens": 12, "total_tokens": 14}
}
```

**流式响应（`stream: true`）：**

返回 `text/event-stream`，逐块推送：

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1782356957,"model":"chatglm-local","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1782356957,"model":"chatglm-local","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1782356957,"model":"chatglm-local","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### POST `/v1/image/understand`

图片理解接口，使用 GLM-4V 视觉模型。

**请求体：**

```json
{
  "image": "<base64 编码的图片>",
  "prompt": "描述这张图片"
}
```

### GET `/v1/models`

返回可用模型列表：

```json
{
  "object": "list",
  "data": [{
    "id": "chatglm-local",
    "object": "model",
    "created": 1700000000,
    "owned_by": "chatglm"
  }]
}
```

### GET `/health`

健康检查：

```json
{"status": "ok", "token_configured": true}
```

## CodeBuddy 集成

编辑 `C:\Users\Administrator\.codebuddy\models.json`，添加：

```json
{
  "id": "chatglm-local",
  "name": "ChatGLM Local",
  "vendor": "OpenAI",
  "url": "http://127.0.0.1:5080/v1/chat/completions",
  "apiKey": "sk-chatglm-local",
  "maxInputTokens": 32000,
  "maxOutputTokens": 4096,
  "supportsToolCall": true,
  "supportsImages": true
}
```

重启 CodeBuddy，在模型列表中选择 **ChatGLM Local** 即可使用。

> **注意：** `apiKey` 可以填任意值（服务端使用 `config.ini` 中的 `refresh_token` 鉴权），但必须以 `sk-` 开头，否则 CodeBuddy 会报鉴权失败。

## 获取 refresh_token

1. 浏览器打开 [chatglm.cn](https://chatglm.cn/) 并登录
2. 按 `F12` 打开开发者工具
3. 切换到 **Application**（应用程序）标签
4. 左侧展开 **Cookies** → 选择 `https://chatglm.cn`
5. 找到 `chatglm_refresh_token`，复制其 Value
6. 粘贴到 `config.ini` 的 `refresh_token` 字段

或者通过浏览器控制台执行：

```javascript
document.cookie.match(/chatglm_refresh_token=([^;]+)/)[1]
```

## 技术架构

```
┌─────────────┐     POST /v1/chat/completions     ┌──────────────────┐
│  CodeBuddy  │ ──────────────────────────────────▶│  GLM API Proxy   │
│  / 客户端    │◀────────────────────────────────── │  (Waitress)      │
└─────────────┘     SSE stream / JSON response     └──────┬───────────┘
                                                          │
                                                          │ 1. refresh_token → access_token
                                                          ▼
                                                 ┌──────────────────┐
                                                 │  chatglm.cn API  │
                                                 │  (逆向接口)       │
                                                 └──────────────────┘
```

### 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| GUI 应用 | `gui_app.py` | Tkinter 界面 + Flask 服务 + pystray 系统托盘 |
| ChatGLM 客户端 | `chatglm_api.py` | 逆向 chatglm.cn 接口：Token 刷新、SSE 流式对话、签名算法 |
| 响应类型 | `api_types.py` | 标准化 `ApiResult` / `StreamChunk` |
| 限流模块 | `rate_limiter.py` | 令牌桶算法，默认 10 req/s |
| 截断模块 | `truncate.py` | 输出截断 + temp 文件保存 |
| 系统提示词 | `system_prompt.txt` | 外部化 system prompt |
| 配置文件 | `config.ini` | 外置配置：端口、Token、模型名、限流参数等 |

### 关键技术点

- **Token 刷新机制**：`refresh_token` → `access_token`（有效期 1 小时，内存缓存自动续期）
- **签名算法**：MD5(`{timestamp}-{nonce}-{secret}`)，时间戳插入算法对齐网页版
- **SSE 流式转发**：ChatGLM 的 SSE 响应通过 `on_token` 回调 → `queue.Queue` → Flask 生成器，转为标准 OpenAI delta 格式
- **请求头伪装**：完整模拟浏览器请求头（User-Agent、Sec-Ch-Ua、X-App-Platform 等）

## 错误排查

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `token_expired` (40102) | refresh_token 过期 | 重新登录 chatglm.cn 获取新 token |
| `500 Internal Error` | ChatGLM 接口异常 | 检查日志，可能是并发限制或网络问题 |
| CodeBuddy Error 10004 | 响应缺少 SSE 流式格式 | 确保使用支持 `stream: true` 的版本 |
| CodeBuddy Error 500 鉴权失败 | apiKey 格式不正确 | 确保 apiKey 以 `sk-` 开头 |
| `Invalid JSON body` | 请求体格式错误 | 确保 Content-Type 为 `application/json` |

## 限制说明

- ChatGLM 网页版接口为逆向获取，**不稳定**，可能随官方更新失效
- 游客账号每号限 1 条消息，建议使用登录账号
- Function Calling 为"软实现"（prompt 引导 + JSON 解析），非原生 tool 调用
- 流式场景截断逻辑偏简单，超长流可能仍有内存压力

正式项目请使用 [智谱 AI 官方 API](https://open.bigmodel.cn/)。

## 技术栈

- **Python 3.13** + Flask + Waitress
- **PyInstaller 6.20** 打包
- **requests** HTTP 客户端
- **SSE** (Server-Sent Events) 流式协议

## License

仅供学习和测试使用。
