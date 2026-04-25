import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.services.keyframes import KeyFrame
from app.services.transcript import TranscriptResult

logger = logging.getLogger(__name__)


class KeyframeMode(str, Enum):
    IMAGE = "image"
    OCR = "ocr"
    OCR_IMAGE = "ocr+image"
    OCR_INLINE = "ocr-inline"
    OCR_INLINE_IMAGE = "ocr-inline+image"
    NONE = "none"


@dataclass
class SummaryResult:
    raw_response: str
    title: str
    tldr: str
    summary: str


class LLMBackendError(Exception):
    """Base exception for all LLM backend failures."""
    pass


class LLMBackend(ABC):
    @abstractmethod
    async def summarize(
        self,
        transcript: TranscriptResult,
        keyframes: list[KeyFrame],
        video_meta: dict,
        custom_prompt: str | None = None,
        custom_prompt_mode: str = "replace",
        model: str | None = None,
        keyframe_mode: KeyframeMode = KeyframeMode.IMAGE,
        ocr_paths: list[Path | None] | None = None,
        ocr_results: list | None = None,
        output_language: str | None = None,
    ) -> SummaryResult: ...

    @abstractmethod
    async def auth_status(self) -> dict: ...

    @abstractmethod
    def supported_modes(self) -> set[KeyframeMode]: ...
