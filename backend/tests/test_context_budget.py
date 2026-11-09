"""
Tests: бюджет объёма документов в контексте LLM.

Покрывает:
- _build_prev_docs_block — соблюдение лимита budget (часть C: надёжность);
- _is_context_length_error — распознавание переполнения контекста (HTTP 400).
"""

from main import _build_prev_docs_block, _MAX_LLM_DOC_CHARS, _MAX_CONVERSATION_DOCS_CHARS
from llm import _is_context_length_error


def _doc(name: str, size: int) -> dict:
    return {"filename": name, "content": "x" * size}


# ── _build_prev_docs_block: бюджет ──────────────────────────────────────────────

def test_prev_docs_empty():
    assert _build_prev_docs_block([]) == ""


def test_prev_docs_zero_budget():
    assert _build_prev_docs_block([_doc("a.docx", 100)], budget=0) == ""


def test_prev_docs_negative_budget():
    assert _build_prev_docs_block([_doc("a.docx", 100)], budget=-50) == ""


def test_prev_docs_within_budget_included():
    block = _build_prev_docs_block([_doc("contract.docx", 1000)], budget=100_000)
    assert "contract.docx" in block
    assert "x" * 1000 in block


def test_prev_docs_over_budget_omitted_with_notice():
    # Документ крупнее бюджета — не влезает, добавляется пометка об опущенных.
    block = _build_prev_docs_block([_doc("big.docx", 5000)], budget=1000)
    assert "big.docx" not in block
    assert "не показаны" in block


def test_prev_docs_priority_oldest_first():
    # Первый (старый) документ влезает, второй — нет: исходный договор приоритетнее.
    docs = [_doc("first.docx", 800), _doc("second.docx", 800)]
    block = _build_prev_docs_block(docs, budget=1000)
    assert "first.docx" in block
    assert "second.docx" not in block
    assert "Ещё 1" in block


def test_prev_docs_total_never_exceeds_budget():
    docs = [_doc(f"d{i}.docx", 50_000) for i in range(10)]
    block = _build_prev_docs_block(docs, budget=120_000)
    # Контент документов в сумме не превышает бюджет (плюс служебные заголовки).
    assert len(block) < 120_000 + 500


def test_prev_docs_default_budget_constant():
    # Дефолт сигнатуры — прежний предпочтительный лимит.
    docs = [_doc("d.docx", 10)]
    assert _build_prev_docs_block(docs) == _build_prev_docs_block(
        docs, budget=_MAX_CONVERSATION_DOCS_CHARS
    )


def test_combined_budget_constants_sane():
    # Общий бюджет должен вмещать хотя бы один полный файл (80k) + запас.
    assert _MAX_LLM_DOC_CHARS >= 80_000
    assert _MAX_CONVERSATION_DOCS_CHARS <= _MAX_LLM_DOC_CHARS


# ── _is_context_length_error ────────────────────────────────────────────────────

def test_ctx_error_openrouter_phrasing():
    body = '{"error":{"message":"This model maximum context length is 128000 tokens"}}'
    assert _is_context_length_error(body) is True


def test_ctx_error_context_length_exceeded():
    assert _is_context_length_error("context_length_exceeded") is True


def test_ctx_error_bytes_body():
    assert _is_context_length_error(b"maximum context length reached") is True


def test_ctx_error_unrelated_400():
    assert _is_context_length_error('{"error":"invalid model name"}') is False


def test_ctx_error_empty():
    assert _is_context_length_error("") is False
    assert _is_context_length_error(None) is False
