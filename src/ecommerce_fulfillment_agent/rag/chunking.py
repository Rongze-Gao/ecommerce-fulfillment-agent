"""为评论类 RAG 数据提供可复用的父子文档切块功能。

完整评论作为父文档保存业务上下文，
切分得到的子文档负责生成向量并参与相似度检索。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass(frozen=True)
class ChunkingStrategy:
    """保存一种文本切块策略。

    chunk_size 为 None 表示保留整条评论、不进行切块。
    """

    name: str
    chunk_size: int | None
    chunk_overlap: int = 0

    def __post_init__(self) -> None:
        """在程序开始处理数据前检查配置是否合法。"""

        if not self.name.strip():
            raise ValueError("切块策略名称不能为空。")

        if self.chunk_size is None:
            if self.chunk_overlap != 0:
                raise ValueError(
                    "不切块策略的 chunk_overlap 必须为 0。"
                )
            return

        if self.chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0。")

        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0。")

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                "chunk_overlap 必须小于 chunk_size。"
            )

    @property
    def splitting_enabled(self) -> bool:
        """返回当前策略是否需要真正执行文本切块。"""

        return self.chunk_size is not None


def split_review_document(
    parent_document: Document,
    strategy: ChunkingStrategy,
) -> list[Document]:
    """将一条完整评论转换为一个或多个可检索子文档。"""

    parent_document_id = parent_document.metadata.get("document_id")

    if not parent_document_id:
        raise ValueError(
            "父文档 metadata 中必须包含 document_id。"
        )

    if not parent_document.page_content.strip():
        raise ValueError("父文档正文不能为空。")

    if strategy.splitting_enabled:
        # 这里显式使用 len，因此 chunk_size 的单位是字符，而不是 Token。
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=strategy.chunk_size,
            chunk_overlap=strategy.chunk_overlap,
            length_function=len,
            add_start_index=True,
            strip_whitespace=True,
        )

        # split_documents 会保留父文档已有的 metadata。
        raw_chunks = splitter.split_documents([parent_document])
    else:
        # 不切块策略仍然生成一个子文档，使后续处理接口保持一致。
        raw_chunks = [
            Document(
                page_content=parent_document.page_content,
                metadata={
                    **parent_document.metadata,
                    "start_index": 0,
                },
            )
        ]

    chunk_count = len(raw_chunks)
    child_documents: list[Document] = []

    for chunk_index, raw_chunk in enumerate(raw_chunks):
        # 每个子文档继续保留订单、评分、州等父文档业务元数据。
        chunk_metadata = dict(raw_chunk.metadata)

        chunk_metadata.update(
            {
                "chunk_id": (
                    f"{parent_document_id}:chunk:"
                    f"{chunk_index:04d}"
                ),
                "parent_document_id": parent_document_id,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "chunk_character_count": len(
                    raw_chunk.page_content
                ),
                "parent_character_count": len(
                    parent_document.page_content
                ),
                "chunking_strategy": strategy.name,
            }
        )

        child_documents.append(
            Document(
                page_content=raw_chunk.page_content,
                metadata=chunk_metadata,
            )
        )

    return child_documents


def split_review_documents(
    parent_documents: Sequence[Document],
    strategy: ChunkingStrategy,
) -> list[Document]:
    """批量切分多条评论，并汇总所有可检索子文档。"""

    child_documents: list[Document] = []

    for parent_document in parent_documents:
        child_documents.extend(
            split_review_document(
                parent_document=parent_document,
                strategy=strategy,
            )
        )

    return child_documents