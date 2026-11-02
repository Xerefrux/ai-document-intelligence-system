"""Text to vectors for semantic search.

Providers:
  hash     character n-gram hashing, runs offline with no model. This is a fuzzy LEXICAL vector: it
           matches typos, word endings and partial words ("gloves" ~ "glove", "thermomter" ~ "thermometer").
           It does not know that "notebook" and "laptop" mean the same thing.
  gemini   Google embedding model over the REST API (real semantic vectors, needs GEMINI_API_KEY)
  openai   any OpenAI compatible /embeddings endpoint (real semantic vectors, needs a key)
  none     semantic search off

The gemini and openai providers are covered by tests against a mock server. They were not run
against the real services while this project was built, because the build sandbox has no access to them.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass

import httpx
import numpy as np

from ..config import Settings

log = logging.getLogger(__name__)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


class EmbeddingError(RuntimeError):
    pass


@dataclass
class Embedder:
    name: str  # stored with every vector so vectors from different models are never mixed
    dim: int
    min_sim: float = 0.25  # cosine below this is not reported as a match (not tuned for the hosted models, see docs/LIMITATIONS.md)

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class HashEmbedder(Embedder):
    def __init__(self, dim: int = 512):
        super().__init__(f"hash-{dim}", dim, min_sim=0.15)

    def _features(self, text: str) -> dict[int, float]:
        words = [w.casefold() for w in _WORD.findall(text)]
        feats: dict[int, float] = {}

        def add(token: str, weight: float) -> None:
            h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 40) & 1 else -1.0
            feats[idx] = feats.get(idx, 0.0) + sign * weight

        for w in words:
            add("w:" + w, 1.0)
            padded = f"^{w}$"
            for n in (3, 4):
                if len(padded) >= n:
                    for i in range(len(padded) - n + 1):
                        add(f"g{n}:" + padded[i : i + n], 0.5)
        for a, b in zip(words, words[1:]):
            add(f"b:{a}_{b}", 0.7)
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            for idx, v in self._features(t or "").items():
                out[r, idx] = v
        # sublinear scaling so a long text does not drown a short query
        out = np.sign(out) * np.sqrt(np.abs(out))
        return _normalize(out)


class GeminiEmbedder(Embedder):
    def __init__(self, settings: Settings):
        model = settings.embedding_model or "gemini-embedding-001"
        super().__init__(f"gemini-{model}-{settings.embedding_dim}", settings.embedding_dim)
        self.s, self.model = settings, model

    def embed(self, texts: list[str]) -> np.ndarray:
        s = self.s
        url = f"{s.gemini_base_url.rstrip('/')}/v1beta/models/{self.model}:batchEmbedContents"
        rows: list[list[float]] = []
        with httpx.Client(timeout=s.llm_timeout_seconds) as client:
            for i in range(0, len(texts), 64):
                batch = texts[i : i + 64]
                body = {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": (t or " ")[:8000]}]}, "outputDimensionality": self.dim} for t in batch]}
                rows.extend(_post_with_retry(client, url, body, {"x-goog-api-key": s.gemini_api_key}, lambda d: [e["values"] for e in d["embeddings"]]))
        return _normalize(np.array(rows, dtype=np.float32))


class OpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings):
        model = settings.embedding_model or "text-embedding-3-small"
        super().__init__(f"openai-{model}-{settings.embedding_dim}", settings.embedding_dim)
        self.s, self.model = settings, model

    def embed(self, texts: list[str]) -> np.ndarray:
        s = self.s
        url = f"{s.openai_base_url.rstrip('/')}/embeddings"
        rows: list[list[float]] = []
        with httpx.Client(timeout=s.llm_timeout_seconds) as client:
            for i in range(0, len(texts), 96):
                batch = [(t or " ")[:8000] for t in texts[i : i + 96]]
                body = {"model": self.model, "input": batch}
                if "text-embedding-3" in self.model:
                    body["dimensions"] = self.dim
                rows.extend(_post_with_retry(client, url, body, {"Authorization": f"Bearer {s.openai_api_key}"}, lambda d: [e["embedding"] for e in sorted(d["data"], key=lambda x: x["index"])]))
        return _normalize(np.array(rows, dtype=np.float32))


def _post_with_retry(client: httpx.Client, url: str, body: dict, headers: dict, pick):
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = client.post(url, json=body, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504):
                raise EmbeddingError(f"embedding service answered {r.status_code}")
            if r.status_code >= 400:
                raise EmbeddingError(f"embedding service answered {r.status_code}: {r.text[:200]}")
            return pick(r.json())
        except (httpx.HTTPError, EmbeddingError, KeyError, ValueError) as exc:
            last = exc
            time.sleep(min(8.0, 0.5 * 2**attempt))
    raise EmbeddingError(f"embedding request failed: {last}")


def get_embedder(settings: Settings) -> Embedder | None:
    p = settings.embedding_provider
    if p == "none":
        return None
    if p == "hash":
        return HashEmbedder(settings.embedding_dim)
    if p == "gemini":
        return GeminiEmbedder(settings)
    if p == "openai":
        return OpenAIEmbedder(settings)
    return None


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def cosine_topk(matrix: np.ndarray, query: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Rows of `matrix` (already normalised) closest to `query`. Returns (row index, cosine similarity)."""
    if matrix.size == 0:
        return []
    sims = matrix @ query
    k = min(k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(int(i), float(sims[i])) for i in idx if not math.isnan(sims[i])]
