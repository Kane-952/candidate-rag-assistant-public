import hashlib
import json
import re
from pathlib import Path

from ..models.schemas import Chunk


def chunk_markdown(text: str, source: str, chunk_size=450, chunk_overlap=80) -> list[Chunk]:
    """按 Markdown 标题分节，再按字符数滑窗；标题路径保存在 title。"""
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError('Require 0 <= chunk_overlap < chunk_size')
    sections, headings, lines = [], [], []
    in_fence = False

    def flush():
        body = ''.join(lines).strip()
        if body:
            sections.append((' / '.join(h[1] for h in headings) or Path(source).stem, body))
        lines.clear()

    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        match = None if in_fence else re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if match:
            flush()
            level = len(match[1])
            headings = [h for h in headings if h[0] < level]
            headings.append((level, match[2]))
        lines.append(line)
    flush()
    chunks = []
    for title, body in sections:
        for start in range(0, len(body), chunk_size - chunk_overlap):
            part = body[start:start + chunk_size].strip()
            if part:
                identity = f'{source}\0{len(chunks)}\0{title}\0{part}'
                chunk_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
                chunks.append(Chunk(source=source, title=title, chunk_id=chunk_id, text=part))
            if start + chunk_size >= len(body):
                break
    return chunks


def load_knowledge_base(directory: Path, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    chunks = []
    for path in sorted(directory.rglob('*.md')):
        chunks.extend(chunk_markdown(path.read_text(encoding='utf-8-sig'), path.relative_to(directory).as_posix(), chunk_size, chunk_overlap))
    return chunks


def save_chunks(chunks: list[Chunk], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(c.model_dump(), ensure_ascii=False) + '\n' for c in chunks), encoding='utf-8')


def read_chunks(path: Path) -> list[Chunk]:
    return [Chunk.model_validate_json(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
