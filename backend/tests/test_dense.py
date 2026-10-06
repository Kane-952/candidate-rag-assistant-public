import numpy as np
import pytest

from backend.app.config import Settings
from backend.app.errors import IndexUnavailable
from backend.app.models.schemas import Chunk
from backend.app.rag.dense import DenseRetriever


def test_faiss_roundtrip_and_stale_index(tmp_path):
    settings = Settings(_env_file=None)
    chunks = [Chunk(source='a.md', title='a', chunk_id='a', text='a'),
              Chunk(source='b.md', title='b', chunk_id='b', text='b')]
    dense = DenseRetriever(chunks, settings)
    dense.encode = lambda texts: np.array([[1, 0], [0, 1]], dtype=np.float32)
    dense.build(tmp_path)
    loaded = DenseRetriever(chunks, settings)
    loaded.load(tmp_path)
    loaded.encode = lambda texts: np.array([[0, 1]], dtype=np.float32)
    assert loaded.search('b', 10)[0].chunk_id == 'b'
    chunks[0].text = 'changed'
    with pytest.raises(IndexUnavailable):
        DenseRetriever(chunks, settings).load(tmp_path)
