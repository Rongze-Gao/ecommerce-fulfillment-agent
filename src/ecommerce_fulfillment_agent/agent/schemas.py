"""Agent 最终回答使用的 Pydantic 数据结构。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AnswerStatus = Literal[
    "answered",
    "partially_answered",
    "out_of_scope",
]


class FulfillmentAgentResponse(BaseModel):
    """电商履约诊断 Agent 的最终结构化回答。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    answer_status: AnswerStatus = Field(
        description=(
            "回答状态："
            "answered 表示证据足以回答；"
            "partially_answered 表示只能回答一部分；"
            "out_of_scope 表示超出项目数据或能力范围。"
        )
    )

    summary: str = Field(
        min_length=1,
        description="面向用户的直接结论，应简洁说明最终判断。",
    )

    findings: list[str] = Field(
        default_factory=list,
        description=(
            "对用户问题的分点回答。对于项目能力问题，"
            "这里列出项目可以分析的内容；对于业务分析问题，"
            "这里列出由证据支持的主要发现。"
        ),
    )

    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "只能记录本次工具调用返回的定量数据、筛选条件、"
            "检索到的真实评论，或者系统提示词明确给出的数据范围。"
            "不得把项目能力介绍、分析假设或建议写入证据。"
            "当前没有业务证据时应返回空列表。"
        ),
    )

    limitations: list[str] = Field(
        default_factory=list,
        description=(
            "当前结论的具体限制，例如当前没有取得工具结果，"
            "或者缺少退款、仓库、物流承运商、天气和因果关系数据。"
            "当回答状态为 partially_answered 或 out_of_scope 时，"
            "该列表至少应该包含一项限制。"
        ),
    )

    next_steps: list[str] = Field(
        default_factory=list,
        description=(
            "基于当前能力可以继续进行的调查建议，"
            "或者为了回答问题需要补充的数据。"
            "当回答状态为 partially_answered 或 out_of_scope 时，"
            "应提供最接近的可行分析方向。"
        ),
    )