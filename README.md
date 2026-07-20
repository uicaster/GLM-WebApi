# GLM API Proxy

OpenAI 兼容的 ChatGLM 网页版 API 代理服务。通过逆向 chatglm.cn 网页版对话接口，将其封装为标准的 OpenAI `/v1/chat/completions` API，支持流式（SSE）和非流式两种响应模式，可直接对接 CodeBuddy、ChatGPT-Next-Web、LobeChat 等客户端。

## 目录结构

```
GLM Api/
├── server.py              # 命令行版服务入口（Flask + Waitress）
├── gui_app.py             # GUI 版入口（Tkinter + 系统托盘）
├── chatglm_api.py         # ChatGLM 逆向 API 客户端
├── build_exe.py           # 命令行版打包脚本
├── build_gui.py           # GUI 版打包脚本
├── config.ini             # 配置文件
├── dist/
│   ├── glm_api/           # 命令行版打包输出
│   │   ├── glm_api.exe
│   │   ├── config.ini
│   │   ├── 启动服务.bat
│   │   └── ...
│   └── glm_api_gui/       # GUI 版打包输出
│       ├── glm_api_gui.exe   # GUI 可执行文件
│       ├── config.ini
│       └── _internal/
└── build/                 # PyInstaller 中间产物（可删除）
```

## 快速开始

### 方式一：使用 GUI 版（推荐）

1. 进入 `dist/glm_api_gui/` 目录
2. 双击 `glm_api_gui.exe`
3. 在界面中填入 Refresh Token，点击「启动服务」
4. 关闭窗口时自动最小化到系统托盘

### 方式二：使用命令行版

1. 进入 `dist/glm_api/` 目录
2. 编辑 `config.ini`，填入你的 `refresh_token`
3. 双击 `启动服务.bat`
4. 访问 `http://127.0.0.1:5080/health` 验证服务

### 方式三：从源码运行

```bash
# 安装依赖
pip install flask waitress requests

# 启动服务
python server.py
```

### 方式四：重新打包

```bash
pip install pyinstaller flask waitress requests pystray Pillow

# 命令行版
python build_exe.py
# 输出到 dist/glm_api/

# GUI 版
python build_gui.py
# 输出到 dist/glm_api_gui/
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

### 命令行参数

所有参数均可覆盖 `config.ini` 中的配置：

```bash
glm_api.exe                          # 使用 config.ini 启动
glm_api.exe --port 8080              # 覆盖端口
glm_api.exe --host 127.0.0.1         # 覆盖绑定地址
glm_api.exe --token <新token>        # 覆盖 refresh_token
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
  "supportsToolCall": false,
  "supportsImages": false
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
| API 代理服务 | `server.py` | Flask 应用，处理 OpenAI 兼容请求，Waitress 生产 WSGI |
| ChatGLM 客户端 | `chatglm_api.py` | 逆向 chatglm.cn 接口：Token 刷新、SSE 流式对话、签名算法 |
| 配置文件 | `config.ini` | 外置配置：端口、Token、模型名 |
| 打包脚本 | `build_exe.py` | PyInstaller 打包为独立 exe |

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
- 不支持 function calling / tool use（`supportsToolCall: false`）
- 不支持图片输入（`supportsImages: false`）
- 仅提取最后一条 user 消息发送，历史上下文不传递（网页版通过 `conversation_id` 管理）

正式项目请使用 [智谱 AI 官方 API](https://open.bigmodel.cn/)。

## 技术栈

- **Python 3.13** + Flask + Waitress
- **PyInstaller 6.20** 打包
- **requests** HTTP 客户端
- **SSE** (Server-Sent Events) 流式协议

## License

仅供学习和测试使用。
