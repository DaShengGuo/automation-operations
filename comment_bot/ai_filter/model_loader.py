"""
model_loader.py — GPU 模型单例管理器
懒加载所有 AI 模型，FP16 精度，线程安全。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional, Any

import torch
from douyin_core import config as cfg

logger = logging.getLogger(__name__)


class ModelLoader:
    """单例 GPU 模型管理器"""

    _instance: Optional["ModelLoader"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._clip_model: Any = None
        self._clip_processor: Any = None
        self._bge_model: Any = None
        self._llm_model: Any = None
        self._blip_model: Any = None
        self._blip_processor: Any = None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._load_lock = threading.Lock()
        logger.info(f"[ModelLoader] 设备: {self._device}")

    @property
    def device(self) -> str:
        return self._device

    @property
    def is_gpu_available(self) -> bool:
        return self._device == "cuda"

    # ── CLIP ──

    def get_clip(self) -> tuple[Any, Any]:
        if self._clip_model is None:
            with self._load_lock:
                if self._clip_model is None:
                    logger.info("[CLIP] 加载中...")
                    from transformers import CLIPModel, CLIPProcessor
                    self._clip_model = CLIPModel.from_pretrained(
                        cfg.AI_CLIP_MODEL_NAME,
                        torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
                    ).to(self._device).eval()
                    self._clip_processor = CLIPProcessor.from_pretrained(
                        cfg.AI_CLIP_MODEL_NAME
                    )
                    logger.info("[CLIP] 加载完成")
        return self._clip_model, self._clip_processor

    # ── BGE ──

    def get_bge(self) -> Any:
        if self._bge_model is None:
            with self._load_lock:
                if self._bge_model is None:
                    logger.info(f"[BGE] 加载 {cfg.AI_BGE_MODEL_NAME}...")
                    from sentence_transformers import SentenceTransformer
                    self._bge_model = SentenceTransformer(
                        cfg.AI_BGE_MODEL_NAME,
                        device=self._device,
                    )
                    logger.info("[BGE] 加载完成")
        return self._bge_model

    # ── BLIP (可选) ──

    def get_blip(self) -> tuple[Any, Any]:
        if self._blip_model is None and cfg.AI_USE_BLIP:
            with self._load_lock:
                if self._blip_model is None:
                    logger.info("[BLIP] 加载中...")
                    from transformers import (
                        BlipForConditionalGeneration,
                        BlipProcessor,
                    )
                    self._blip_model = BlipForConditionalGeneration.from_pretrained(
                        "Salesforce/blip-image-captioning-base",
                        torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
                    ).to(self._device).eval()
                    self._blip_processor = BlipProcessor.from_pretrained(
                        "Salesforce/blip-image-captioning-base"
                    )
                    logger.info("[BLIP] 加载完成")
        return self._blip_model, self._blip_processor

    def unload_blip(self):
        if self._blip_model is not None:
            del self._blip_model, self._blip_processor
            self._blip_model = self._blip_processor = None
            torch.cuda.empty_cache()
            logger.info("[BLIP] 已卸载")

    # ── LLM ──

    def get_llm(self) -> Any:
        if self._llm_model is None and cfg.AI_USE_LLM:
            with self._load_lock:
                if self._llm_model is None:
                    self._load_llm()
        return self._llm_model

    def _load_llm(self):
        model_path = cfg.AI_LLM_MODEL_PATH
        if not __import__("os").path.exists(model_path):
            logger.warning(f"[LLM] 模型文件不存在: {model_path}")
            logger.warning("[LLM] 请下载 Qwen2.5-1.5B-Instruct GGUF 到 data/models/")
            return
        logger.info("[LLM] 加载中...")
        try:
            from llama_cpp import Llama
            self._llm_model = Llama(
                model_path=model_path,
                n_ctx=2048,
                n_gpu_layers=-1 if self._device == "cuda" else 0,
                verbose=False,
            )
            logger.info("[LLM] 加载完成")
        except ImportError:
            logger.warning("[LLM] llama-cpp-python 未安装, LLM 不可用")
        except Exception as e:
            logger.error(f"[LLM] 加载失败: {e}")

    def unload_llm(self):
        if self._llm_model is not None:
            del self._llm_model
            self._llm_model = None
            torch.cuda.empty_cache()
            logger.info("[LLM] 已卸载")

    # ── 健康检查 ──

    def health_check(self) -> dict:
        return {
            "device": self._device,
            "clip": self._clip_model is not None,
            "bge": self._bge_model is not None,
            "llm": self._llm_model is not None,
            "blip": self._blip_model is not None,
            "gpu_mem_used": (
                f"{torch.cuda.memory_allocated() / 1024**3:.1f}GB"
                if self._device == "cuda" else "N/A"
            ),
        }
