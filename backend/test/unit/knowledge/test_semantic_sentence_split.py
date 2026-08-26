from unittest.mock import Mock

import pytest

from yuxi.knowledge.chunking.ragflow_like.utils import semantic_utils


def test_pure_chinese_split_does_not_require_punkt_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯中文分句不应因英文分句资源缺失而阻断。"""
    missing_resource = Mock(side_effect=RuntimeError("缺少 punkt_tab"))
    monkeypatch.setattr(semantic_utils, "_ensure_punkt_tab", missing_resource)

    sentences = semantic_utils.split_mixed_sentences("第一句。第二句！")

    assert sentences == ["第一句。", "第二句！"]
    missing_resource.assert_not_called()


def test_english_split_checks_punkt_tab_before_tokenizing(monkeypatch: pytest.MonkeyPatch) -> None:
    """英文分句应先确认 punkt_tab 可用再调用 NLTK。"""
    resource_check = Mock()
    tokenizer = Mock(return_value=["First sentence.", "Second sentence."])
    monkeypatch.setattr(semantic_utils, "_ensure_punkt_tab", resource_check)
    monkeypatch.setattr(semantic_utils, "sent_tokenize", tokenizer)

    sentences = semantic_utils.split_mixed_sentences("First sentence. Second sentence.")

    assert sentences == ["First sentence.", "Second sentence."]
    resource_check.assert_called_once_with()
    tokenizer.assert_called_once_with("First sentence. Second sentence.")


def test_missing_punkt_tab_returns_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """资源缺失时应返回包含安装命令的明确错误。"""
    monkeypatch.setattr(semantic_utils, "_punkt_checked", False)
    monkeypatch.setattr(semantic_utils.nltk.data, "find", Mock(side_effect=LookupError))

    with pytest.raises(RuntimeError, match=r"python -m nltk\.downloader punkt_tab"):
        semantic_utils._ensure_punkt_tab()
