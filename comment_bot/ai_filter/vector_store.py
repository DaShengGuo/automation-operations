"""
vector_store.py — Qdrant 向量数据库（内存模式）
存储正/负样本参考数据，用于语义相似度搜索。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, CollectionInfo,
)

from douyin_core import config as cfg

logger = logging.getLogger(__name__)

COLLECTION_TARGET = "target_content"
COLLECTION_EXCLUDE = "exclude_content"


class VectorStore:
    """Qdrant 向量存储，两个 Collection: target_content / exclude_content"""

    def __init__(self, path: str = None, use_in_memory: bool = None):
        if path is None:
            path = cfg.AI_QDRANT_PATH
        if use_in_memory is None:
            use_in_memory = cfg.AI_QDRANT_USE_IN_MEMORY

        if use_in_memory:
            self.client = QdrantClient(":memory:")
            logger.info("[Qdrant] 内存模式")
        else:
            Path(path).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=path)
            logger.info(f"[Qdrant] 持久化模式: {path}")

        self._vector_size: Optional[int] = None
        self._ensure_collections()
        self._seeded = False

    def _ensure_collections(self):
        """创建 Collection（如不存在）"""
        existing = {c.name for c in self.client.get_collections().collections}
        for name in [COLLECTION_TARGET, COLLECTION_EXCLUDE]:
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=512,  # bge-small-zh 输出维度
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"[Qdrant] 创建 Collection: {name}")

    @property
    def is_seeded(self) -> bool:
        if self._seeded:
            return True
        try:
            info: CollectionInfo = self.client.get_collection(COLLECTION_TARGET)
            self._seeded = info.points_count > 0
        except Exception:
            self._seeded = False
        return self._seeded

    def seed_from_file(self, seed_path: str = None, bge_model=None):
        """从 JSON 文件注入种子数据"""
        if seed_path is None:
            seed_path = cfg.AI_SEED_PATH
        if not Path(seed_path).exists():
            logger.warning(f"[Qdrant] 种子文件不存在: {seed_path}")
            return
        if bge_model is None:
            logger.error("[Qdrant] 需要 BGE 模型来嵌入种子数据")
            return

        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.info(f"[Qdrant] 注入种子数据: {len(data.get('target',[]))} 正样本 + {len(data.get('exclude',[]))} 负样本")

        # 注入 target
        targets = data.get("target", [])
        if targets:
            texts = [t["text"] for t in targets]
            embeddings = bge_model.encode(texts, normalize_embeddings=True)
            points = [
                PointStruct(
                    id=t["id"] if isinstance(t["id"], int) else hash(t["id"]) % (10**9),
                    vector=emb.tolist(),
                    payload={"text": t["text"], "category": t.get("category", "")},
                )
                for t, emb in zip(targets, embeddings)
            ]
            self.client.upsert(collection_name=COLLECTION_TARGET, points=points)

        # 注入 exclude
        excludes = data.get("exclude", [])
        if excludes:
            texts = [e["text"] for e in excludes]
            embeddings = bge_model.encode(texts, normalize_embeddings=True)
            points = [
                PointStruct(
                    id=e["id"] if isinstance(e["id"], int) else hash(e["id"]) % (10**9) + 10**8,
                    vector=emb.tolist(),
                    payload={"text": e["text"], "category": e.get("category", "")},
                )
                for e, emb in zip(excludes, embeddings)
            ]
            self.client.upsert(collection_name=COLLECTION_EXCLUDE, points=points)

        self._seeded = True
        logger.info("[Qdrant] 种子数据注入完成")

    def search_target(self, vector: np.ndarray, top_k: int = 3) -> list[dict]:
        """搜索 target_content Collection"""
        if not self.is_seeded:
            return []
        results = self.client.search(
            collection_name=COLLECTION_TARGET,
            query_vector=vector.tolist(),
            limit=top_k,
        )
        return [
            {"score": r.score, "text": r.payload.get("text", ""),
             "category": r.payload.get("category", "")}
            for r in results
        ]

    def search_exclude(self, vector: np.ndarray, top_k: int = 3) -> list[dict]:
        """搜索 exclude_content Collection"""
        if not self.is_seeded:
            return []
        results = self.client.search(
            collection_name=COLLECTION_EXCLUDE,
            query_vector=vector.tolist(),
            limit=top_k,
        )
        return [
            {"score": r.score, "text": r.payload.get("text", ""),
             "category": r.payload.get("category", "")}
            for r in results
        ]

    def search_both(self, vector: np.ndarray, top_k: int = 3) -> tuple[list[dict], list[dict]]:
        """同时搜索两个 Collection"""
        targets = self.search_target(vector, top_k)
        excludes = self.search_exclude(vector, top_k)
        return targets, excludes

    @property
    def target_count(self) -> int:
        try:
            return self.client.get_collection(COLLECTION_TARGET).points_count
        except Exception:
            return 0

    @property
    def exclude_count(self) -> int:
        try:
            return self.client.get_collection(COLLECTION_EXCLUDE).points_count
        except Exception:
            return 0
