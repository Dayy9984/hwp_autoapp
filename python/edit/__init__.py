"""Backward-compatible aliases for 이전 `edit` 패키지 임포트 호환."""

from importlib import import_module
import sys

_content_modifier = import_module("modification.content_modifier")
sys.modules[__name__ + ".content_modifier"] = _content_modifier

ContentModifier = _content_modifier.ContentModifier

__all__ = ["ContentModifier"]
