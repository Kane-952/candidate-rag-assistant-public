"""Run from the project root: python scripts/build_index.py"""
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.errors import ServiceError
from backend.app.rag.chunking import load_knowledge_base, save_chunks
from backend.app.rag.dense import DenseRetriever


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    settings = get_settings()
    chunks = load_knowledge_base(settings.kb_dir, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise ServiceError('Knowledge Base empty：请添加 knowledge_base/**/*.md。')
    # 在临时目录完成模型推理后才替换正式文件。构建期间请停止 API 服务。
    staging = settings.index_dir / ('build-' + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    save_chunks(chunks, staging / 'chunks.jsonl')
    DenseRetriever(chunks, settings).build(staging)
    for name in ('chunks.jsonl', 'vectors.faiss', 'manifest.json'):
        (staging / name).replace(settings.index_dir / name)
    staging.rmdir()
    save_chunks(chunks, ROOT / 'data' / 'processed' / 'chunks.jsonl')
    logging.info('Built %d chunks. Index: %s', len(chunks), settings.index_dir)


if __name__ == '__main__':
    try:
        main()
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
