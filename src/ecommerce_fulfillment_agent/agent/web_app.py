"""Olist 电商履约诊断 Agent 网页入口。

启动前需已运行 MCP SSE 服务、MySQL、Redis 与 Ollama。
默认监听 http://127.0.0.1:28081，仅绑定本机。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
)
from starlette.routing import Route

from ecommerce_fulfillment_agent.agent.fulfillment_agent import (
    answer_question,
    create_fulfillment_coordinator,
)
from ecommerce_fulfillment_agent.agent.memory import (
    clear_session_history,
)


WEB_HOST = "127.0.0.1"
WEB_PORT = 28081


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >
    <title>Olist 电商履约诊断 Agent</title>

    <style>
        :root {
            color-scheme: light;
            font-family:
                Inter,
                "Microsoft YaHei",
                "PingFang SC",
                sans-serif;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background: #f3f5f9;
            color: #1f2937;
        }

        .app {
            width: min(1100px, 100%);
            min-height: 100vh;
            margin: 0 auto;
            background: #ffffff;
            display: flex;
            flex-direction: column;
            box-shadow: 0 0 35px rgba(15, 23, 42, 0.08);
        }

        header {
            padding: 20px 28px;
            color: #ffffff;
            background:
                linear-gradient(
                    135deg,
                    #1e3a8a,
                    #2563eb
                );
        }

        header h1 {
            margin: 0 0 7px;
            font-size: 23px;
        }

        header p {
            margin: 0;
            color: #dbeafe;
            font-size: 14px;
        }

        .toolbar {
            padding: 10px 28px;
            border-bottom: 1px solid #e5e7eb;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            background: #f8fafc;
        }

        .session {
            color: #64748b;
            font-size: 12px;
            overflow-wrap: anywhere;
        }

        button {
            border: 0;
            border-radius: 8px;
            padding: 10px 17px;
            cursor: pointer;
            font-size: 14px;
        }

        .clear-button {
            background: #e2e8f0;
            color: #334155;
        }

        .clear-button:hover {
            background: #cbd5e1;
        }

        #chat {
            flex: 1;
            overflow-y: auto;
            padding: 26px;
            min-height: 520px;
        }

        .message {
            display: flex;
            margin-bottom: 20px;
        }

        .message.user {
            justify-content: flex-end;
        }

        .bubble {
            max-width: 88%;
            border-radius: 14px;
            padding: 14px 17px;
            line-height: 1.65;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }

        .user .bubble {
            background: #2563eb;
            color: #ffffff;
            border-bottom-right-radius: 4px;
        }

        .assistant .bubble {
            width: min(900px, 100%);
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-bottom-left-radius: 4px;
        }

        .thinking {
            color: #64748b;
            font-style: italic;
        }

        .summary {
            font-size: 16px;
            font-weight: 600;
            color: #0f172a;
        }

        .section {
            margin-top: 18px;
        }

        .section-title {
            margin-bottom: 7px;
            font-weight: 700;
            color: #1e3a8a;
        }

        .section ol {
            margin: 0;
            padding-left: 23px;
        }

        .section li {
            margin-bottom: 7px;
        }

        .status {
            display: inline-block;
            margin-top: 15px;
            padding: 4px 9px;
            border-radius: 999px;
            background: #dbeafe;
            color: #1d4ed8;
            font-size: 12px;
        }

        details {
            margin-top: 15px;
            color: #475569;
        }

        details pre {
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            padding: 10px;
            background: #eef2f7;
            border-radius: 8px;
        }

        .composer {
            border-top: 1px solid #e5e7eb;
            padding: 16px 22px 20px;
            background: #ffffff;
        }

        .input-row {
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }

        textarea {
            flex: 1;
            min-height: 72px;
            max-height: 220px;
            resize: vertical;
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 12px 14px;
            font: inherit;
            line-height: 1.5;
            outline: none;
        }

        textarea:focus {
            border-color: #2563eb;
            box-shadow:
                0 0 0 3px rgba(37, 99, 235, 0.12);
        }

        .send-button {
            background: #2563eb;
            color: #ffffff;
            min-width: 90px;
            height: 45px;
        }

        .send-button:hover {
            background: #1d4ed8;
        }

        .send-button:disabled {
            background: #94a3b8;
            cursor: not-allowed;
        }

        .hint {
            margin-top: 8px;
            color: #94a3b8;
            font-size: 12px;
        }

        @media (max-width: 700px) {
            header,
            .toolbar,
            #chat,
            .composer {
                padding-left: 15px;
                padding-right: 15px;
            }

            .bubble {
                max-width: 96%;
            }

            .input-row {
                flex-direction: column;
            }

            .send-button {
                width: 100%;
            }
        }
    </style>
</head>

<body>
<div class="app">
    <header>
        <h1>Olist 电商履约诊断 Agent</h1>
        <p>
            履约指标 · 分组排名 · 顾客评论证据
        </p>
    </header>

    <div class="toolbar">
        <div class="session">
            会话：
            <span id="session-id"></span>
        </div>

        <button
            class="clear-button"
            id="clear-button"
        >
            新建对话
        </button>
    </div>

    <main id="chat"></main>

    <div class="composer">
        <div class="input-row">
            <textarea
                id="question"
                placeholder="请输入履约分析问题……"
            ></textarea>

            <button
                class="send-button"
                id="send-button"
            >
                发送
            </button>
        </div>

        <div class="hint">
            Enter发送，Shift + Enter换行
        </div>
    </div>
</div>

<script>
    const chat = document.getElementById("chat");
    const questionInput =
        document.getElementById("question");
    const sendButton =
        document.getElementById("send-button");
    const clearButton =
        document.getElementById("clear-button");
    const sessionElement =
        document.getElementById("session-id");

    let sessionId =
        localStorage.getItem(
            "fulfillment_agent_session_id"
        );

    if (!sessionId) {
        sessionId = crypto.randomUUID();

        localStorage.setItem(
            "fulfillment_agent_session_id",
            sessionId
        );
    }

    sessionElement.textContent = sessionId;

    function scrollToBottom() {
        chat.scrollTop = chat.scrollHeight;
    }

    function createMessage(role) {
        const wrapper =
            document.createElement("div");

        wrapper.className =
            `message ${role}`;

        const bubble =
            document.createElement("div");

        bubble.className = "bubble";

        wrapper.appendChild(bubble);
        chat.appendChild(wrapper);

        scrollToBottom();

        return bubble;
    }

    function addUserMessage(text) {
        const bubble =
            createMessage("user");

        bubble.textContent = text;
    }

    function addTextMessage(role, text) {
        const bubble =
            createMessage(role);

        bubble.textContent = text;

        return bubble;
    }

    function addSection(
        bubble,
        title,
        values
    ) {
        if (!values || values.length === 0) {
            return;
        }

        const section =
            document.createElement("div");

        section.className = "section";

        const heading =
            document.createElement("div");

        heading.className = "section-title";
        heading.textContent = title;

        section.appendChild(heading);

        const list =
            document.createElement("ol");

        for (const value of values) {
            const item =
                document.createElement("li");

            item.textContent = value;
            list.appendChild(item);
        }

        section.appendChild(list);
        bubble.appendChild(section);
    }

    function addAgentResponse(
        response,
        toolCalls
    ) {
        const bubble =
            createMessage("assistant");

        const summary =
            document.createElement("div");

        summary.className = "summary";
        summary.textContent =
            response.summary;

        bubble.appendChild(summary);

        addSection(
            bubble,
            "主要发现",
            response.findings
        );

        addSection(
            bubble,
            "数据与评论证据",
            response.evidence
        );

        addSection(
            bubble,
            "分析限制",
            response.limitations
        );

        addSection(
            bubble,
            "建议的下一步",
            response.next_steps
        );

        const status =
            document.createElement("div");

        status.className = "status";
        status.textContent =
            `回答状态：${response.answer_status}`;

        bubble.appendChild(status);

        if (toolCalls && toolCalls.length > 0) {
            const details =
                document.createElement("details");

            const detailsTitle =
                document.createElement("summary");

            detailsTitle.textContent =
                `本轮调用了 ${toolCalls.length} 个业务工具`;

            details.appendChild(detailsTitle);

            const content =
                document.createElement("pre");

            content.textContent =
                toolCalls.map(
                    (toolCall, index) => {
                        return (
                            `${index + 1}. ` +
                            `${toolCall.name}\n` +
                            JSON.stringify(
                                toolCall.args,
                                null,
                                2
                            )
                        );
                    }
                ).join("\n\n");

            details.appendChild(content);
            bubble.appendChild(details);
        }

        scrollToBottom();
    }

    async function sendQuestion() {
        const question =
            questionInput.value.trim();

        if (!question) {
            return;
        }

        addUserMessage(question);

        questionInput.value = "";
        sendButton.disabled = true;

        const thinkingBubble =
            addTextMessage(
                "assistant",
                "Thinking..."
            );

        thinkingBubble.classList.add(
            "thinking"
        );

        try {
            const httpResponse =
                await fetch(
                    "/api/chat",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body: JSON.stringify({
                            session_id: sessionId,
                            question: question
                        })
                    }
                );

            const payload =
                await httpResponse.json();

            thinkingBubble.parentElement.remove();

            if (!httpResponse.ok) {
                throw new Error(
                    payload.error ||
                    "请求处理失败"
                );
            }

            addAgentResponse(
                payload.response,
                payload.tool_calls
            );

        } catch (error) {
            thinkingBubble.textContent =
                `处理失败：${error.message}`;

            thinkingBubble.classList.remove(
                "thinking"
            );
        } finally {
            sendButton.disabled = false;
            questionInput.focus();
            scrollToBottom();
        }
    }

    async function clearConversation() {
        const confirmed = window.confirm(
            "确定清空当前对话并创建新会话吗？"
        );

        if (!confirmed) {
            return;
        }

        try {
            await fetch(
                "/api/clear",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        session_id: sessionId
                    })
                }
            );
        } finally {
            sessionId = crypto.randomUUID();

            localStorage.setItem(
                "fulfillment_agent_session_id",
                sessionId
            );

            sessionElement.textContent =
                sessionId;

            chat.innerHTML = "";

            addTextMessage(
                "assistant",
                "新对话已经创建，可以开始提问。"
            );
        }
    }

    sendButton.addEventListener(
        "click",
        sendQuestion
    );

    clearButton.addEventListener(
        "click",
        clearConversation
    );

    questionInput.addEventListener(
        "keydown",
        event => {
            if (
                event.key === "Enter" &&
                !event.shiftKey
            ) {
                event.preventDefault();
                sendQuestion();
            }
        }
    );

    addTextMessage(
        "assistant",
        "你好，我可以查询履约指标、业务分组排名，" +
        "并检索真实顾客评论作为定性证据。"
    );

    questionInput.focus();
</script>
</body>
</html>
"""


@asynccontextmanager
async def lifespan(app: Starlette):
    """在Web服务启动时只创建一次Agent。"""

    print("正在初始化履约诊断Agent...")

    (
        memory_chain,
        tools,
    ) = await create_fulfillment_coordinator()

    app.state.memory_chain = (
        memory_chain
    )
    app.state.tool_names = [
        tool.name
        for tool in tools
    ]

    print(
        "Agent初始化完成，可用工具："
        + "、".join(
            app.state.tool_names
        )
    )

    yield


async def homepage(
    request: Request,
) -> HTMLResponse:
    """返回聊天页面。"""

    return HTMLResponse(
        HTML_PAGE
    )


async def chat_api(
    request: Request,
) -> JSONResponse:
    """接收问题并返回Agent结构化回答。"""

    try:
        body = await request.json()

        question = str(
            body.get("question", "")
        ).strip()

        session_id = str(
            body.get("session_id", "")
        ).strip()

        if not question:
            return JSONResponse(
                {
                    "error": "问题不能为空。"
                },
                status_code=400,
            )

        if not session_id:
            return JSONResponse(
                {
                    "error": (
                        "session_id不能为空。"
                    )
                },
                status_code=400,
            )

        result = await answer_question(
            memory_chain=(
                request.app.state.memory_chain
            ),
            question=question,
            session_id=session_id,
        )

        response = result[
            "structured_response"
        ]

        tool_calls: list[
            dict[str, Any]
        ] = result[
            "business_tool_calls"
        ]

        return JSONResponse(
            {
                "response": (
                    response.model_dump(
                        mode="json"
                    )
                ),
                "tool_calls": tool_calls,
            }
        )

    except Exception as exc:
        return JSONResponse(
            {
                "error": (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )
            },
            status_code=500,
        )


async def clear_api(
    request: Request,
) -> JSONResponse:
    """清除指定Redis对话会话。"""

    try:
        body = await request.json()

        session_id = str(
            body.get("session_id", "")
        ).strip()

        if session_id:
            clear_session_history(
                session_id
            )

        return JSONResponse(
            {
                "status": "success"
            }
        )

    except Exception as exc:
        return JSONResponse(
            {
                "error": (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )
            },
            status_code=500,
        )


routes = [
    Route(
        "/",
        endpoint=homepage,
        methods=["GET"],
    ),
    Route(
        "/api/chat",
        endpoint=chat_api,
        methods=["POST"],
    ),
    Route(
        "/api/clear",
        endpoint=clear_api,
        methods=["POST"],
    ),
]


app = Starlette(
    debug=False,
    routes=routes,
    lifespan=lifespan,
)


def main() -> None:
    """启动本地Agent网页。"""

    print(
        "模式：Olist电商履约诊断Agent网页"
    )
    print(
        f"页面地址："
        f"http://{WEB_HOST}:{WEB_PORT}"
    )
    print(
        "请保持MCP服务端正在运行。"
    )
    print(
        "停止网页：Ctrl+C"
    )

    uvicorn.run(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()