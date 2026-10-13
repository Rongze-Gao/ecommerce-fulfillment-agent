"""把用户问题转换为多种巴西葡萄牙语检索表达。"""

from __future__ import annotations

from langchain_core.prompts import (
    ChatPromptTemplate,
)
from langchain_ollama import ChatOllama
from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from ecommerce_fulfillment_agent.config import (
    get_ollama_settings,
)


# 每个原始问题生成三个葡萄牙语查询变体。
PORTUGUESE_QUERY_COUNT = 3


class PortugueseQueryVariants(BaseModel):
    """查询翻译器返回的结构化结果。"""

    detected_language: str = Field(
        description=(
            "检测到的原始问题语言，"
            "使用简短语言代码，例如 zh、en、pt。"
        )
    )

    portuguese_queries: list[str] = Field(
        min_length=PORTUGUESE_QUERY_COUNT,
        max_length=PORTUGUESE_QUERY_COUNT,
        description=(
            "三个语义一致、措辞不同的"
            "巴西葡萄牙语检索问题。"
        ),
    )

    @field_validator("detected_language")
    @classmethod
    def validate_detected_language(
        cls,
        value: str,
    ) -> str:
        """清理并验证语言代码。"""

        normalized = value.strip().lower()

        if not normalized:
            raise ValueError(
                "detected_language 不能为空。"
            )

        return normalized

    @field_validator("portuguese_queries")
    @classmethod
    def validate_portuguese_queries(
        cls,
        values: list[str],
    ) -> list[str]:
        """确保三个查询非空且互不重复。"""

        normalized_queries: list[str] = []
        seen_queries: set[str] = set()

        for value in values:
            # 把连续空格、换行和制表符统一成一个空格。
            normalized = " ".join(value.split())

            if not normalized:
                raise ValueError(
                    "葡萄牙语检索问题不能为空。"
                )

            duplicate_key = (
                normalized.casefold()
            )

            if duplicate_key in seen_queries:
                raise ValueError(
                    "葡萄牙语检索问题不能重复。"
                )

            seen_queries.add(duplicate_key)
            normalized_queries.append(normalized)

        return normalized_queries


TRANSLATION_PROMPT = (
    ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
You are an internal query-translation component for an
e-commerce fulfillment retrieval system whose review
corpus is primarily written in Brazilian Portuguese.

For the user's question:
1. Detect the source language.
2. Produce exactly three concise Brazilian Portuguese
   retrieval queries.
3. Keep all three queries semantically equivalent to the
   original question, but vary useful e-commerce and
   logistics wording.

Strict rules:
- Use grammatically correct Brazilian Portuguese, including correct
  grammatical gender, articles, and prepositions.
- Preserve negation, quantities, dates, locations,
  Brazilian state codes, order IDs, product IDs,
  and seller IDs exactly.
- Preserve distinctions such as "delivered late",
  "not delivered", "damaged product",
  and "wrong product".
- Do not invent causes, facts, explanations,
  or conclusions.
- Do not answer the question.
- If the source is already Portuguese, create three
  distinct Portuguese retrieval paraphrases.
- Return only data matching the required structured
  schema.
""".strip(),
            ),
            (
                "human",
                "Original question:\n{question}",
            ),
        ]
    )
)


class QueryTranslator:
    """内部查询翻译策略，不作为 Agent Tool 暴露。"""

    def __init__(
        self,
        model: ChatOllama,
    ) -> None:
        """
        构建由 Prompt、ChatOllama 和结构化输出
        组成的 LCEL 链。
        """

        structured_model = (
            model.with_structured_output(
                PortugueseQueryVariants,
                method="json_schema",
            )
        )

        self._chain = (
            TRANSLATION_PROMPT
            | structured_model
        )

    def translate(
        self,
        question: str,
    ) -> PortugueseQueryVariants:
        """生成三个巴西葡萄牙语检索表达。"""

        normalized_question = " ".join(
            question.split()
        )

        if not normalized_question:
            raise ValueError(
                "待翻译的检索问题不能为空。"
            )

        result = self._chain.invoke(
            {
                "question": (
                    normalized_question
                )
            }
        )

        if not isinstance(
            result,
            PortugueseQueryVariants,
        ):
            raise TypeError(
                "查询翻译器没有返回 "
                "PortugueseQueryVariants。"
            )

        return result


def create_query_translator() -> QueryTranslator:
    """根据项目配置创建查询翻译器。"""

    settings = get_ollama_settings()

    model = ChatOllama(
        base_url=settings.base_url,
        model=settings.translation_model,

        # 翻译任务需要稳定，不需要随机发挥。
        temperature=0,

        # 关闭 qwen3 的深度思考，
        # 避免为简单翻译消耗额外时间。
        reasoning=False,

        # 三个短查询不需要很长的输出。
        num_predict=512,

        # 调用后让模型在 Ollama 中保留10分钟，
        # 后续连续查询不必反复加载模型。
        keep_alive="10m",

        tags=[
            "olist",
            "query-translation",
            "ollama",
        ],
    )

    return QueryTranslator(model)


def main() -> None:
    """运行一次真实中文问题的查询翻译验证。"""

    settings = get_ollama_settings()

    question = (
        "订单没有按预计日期送达，"
        "而且收到时包装已经破损。"
    )

    print(
        "模式：Ollama 内部查询翻译策略验证"
    )
    print(
        f"Ollama 地址：{settings.base_url}"
    )
    print(
        "翻译模型："
        f"{settings.translation_model}"
    )
    print(f"原始问题：{question}")

    translator = create_query_translator()
    result = translator.translate(question)

    print("\n=== 结构化翻译结果 ===")
    print(
        "检测语言："
        f"{result.detected_language}"
    )

    for position, query in enumerate(
        result.portuguese_queries,
        start=1,
    ):
        print(f"{position}. {query}")

    print(
        "\n[PASS] 原始问题已转换为三个"
        "互不重复的巴西葡萄牙语检索表达。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] 查询翻译验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc