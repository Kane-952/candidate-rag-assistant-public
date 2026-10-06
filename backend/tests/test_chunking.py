import pytest
from backend.app.rag.chunking import chunk_markdown, save_chunks, read_chunks


def test_chunking_preserves_heading_source_and_overlap(tmp_path):
    body = '# 项目\n## 方法\n' + '甲乙丙丁戊己庚辛壬癸' * 10
    chunks = chunk_markdown(body, 'projects/test.md', 40, 8)
    assert all(len(c.text) <= 40 and c.source == 'projects/test.md' for c in chunks)
    method = [c for c in chunks if c.title == '项目 / 方法']
    assert method[0].text[-8:] == method[1].text[:8]
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert chunks == chunk_markdown(body, 'projects/test.md', 40, 8)
    save_chunks(chunks, tmp_path / 'chunks.jsonl')
    assert read_chunks(tmp_path / 'chunks.jsonl') == chunks


def test_empty_and_invalid_chunking():
    assert chunk_markdown('  ', 'empty.md') == []
    with pytest.raises(ValueError):
        chunk_markdown('text', 'bad.md', 10, 10)


def test_code_heading_not_treated_as_markdown_heading():
    chunks = chunk_markdown('# Project\n\n```python\n# comment\n```\n', 'p.md')
    assert len(chunks) == 1 and chunks[0].title == 'Project'
