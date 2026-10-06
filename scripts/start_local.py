"""Start locally; rebuild stale indexes before serving."""
import socket
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings
from backend.app.rag.chunking import load_knowledge_base, read_chunks


def main():
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', 8000)) == 0:
            print('Port 8000 is already in use. Open http://127.0.0.1:8000/ or stop the existing server first.')
            return
    settings = get_settings()
    try:
        old = read_chunks(settings.index_dir / 'chunks.jsonl')
    except (OSError, ValueError):
        old = None
    current = load_knowledge_base(settings.kb_dir, settings.chunk_size, settings.chunk_overlap)
    if not current:
        raise SystemExit('Knowledge base is empty. Add Markdown files first.')
    import json
    try:
        manifest = json.loads((settings.index_dir / 'manifest.json').read_text(encoding='utf-8'))
        compatible = manifest['embedding_model'] == settings.embedding_model and manifest['max_length'] == settings.model_max_length and (settings.index_dir / 'vectors.faiss').exists()
    except (OSError, ValueError, KeyError):
        compatible = False
    if old != current or not compatible:
        print('Knowledge base changed. Rebuilding index...', flush=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_index.py')], cwd=ROOT, check=True)
    print('Open http://127.0.0.1:8000/ ; keep this window open. Ctrl+C stops the server.', flush=True)
    subprocess.run([sys.executable, '-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log'], cwd=ROOT, check=True)

if __name__ == '__main__':
    main()
