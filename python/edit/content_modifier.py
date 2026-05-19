"""Compatibility shim for 이전 `edit.content_modifier` 임포트 경로 호환."""

from modification import content_modifier as _content_modifier
import sys

sys.modules[__name__] = _content_modifier
