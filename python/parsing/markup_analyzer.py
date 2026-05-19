"""
Structural Markup Analyzer - State machine-based tag detection.

This module provides markup tag detection using state machine approach
instead of complex regular expressions, improving maintainability
and reducing dependency on regex patterns.
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional


class ParseState(Enum):
    """Markup parsing states for state machine"""
    NORMAL = auto()
    TAG_START = auto()
    TAG_NAME = auto()
    TAG_BODY = auto()


@dataclass
class TagMatch:
    """Represents a matched structural tag"""
    tag_type: str  # 'grid', 'sequence', 'item'
    tag_name: str  # 'table', 'list', 'list1', 'li'
    start_pos: int
    end_pos: int


class StructuralMarkupAnalyzer:
    """
    Analyzes text for structural markup tags using state machine.

    Unlike regex-based approaches, this uses explicit state transitions
    for parsing, making the logic more transparent and maintainable.

    Example:
        >>> analyzer = StructuralMarkupAnalyzer()
        >>> analyzer.contains_structural_markup("<table>data</table>")
        True
        >>> analyzer.contains_structural_markup("plain text")
        False
    """

    STRUCTURAL_TAGS = {
        'table': 'grid',
        'list': 'sequence',
        'li': 'item',
    }

    def __init__(self):
        self._state = ParseState.NORMAL
        self._current_tag = []
        self._position = 0

    def contains_structural_markup(self, text: str) -> bool:
        """
        Checks if text contains structural markup tags.

        Args:
            text: Content to analyze

        Returns:
            True if structural tags are found, False otherwise
        """
        return self.find_first_tag(text) is not None

    def find_first_tag(self, text: str) -> Optional[TagMatch]:
        """
        Finds the first structural tag in text.

        Uses state machine for parsing instead of regex.

        Args:
            text: Text to search

        Returns:
            TagMatch if found, None otherwise
        """
        self._reset()

        for i, char in enumerate(text):
            self._position = i

            if self._state == ParseState.NORMAL:
                if char == '<':
                    self._state = ParseState.TAG_START
                    self._current_tag = []

            elif self._state == ParseState.TAG_START:
                if char.isalpha():
                    self._state = ParseState.TAG_NAME
                    self._current_tag.append(char)
                else:
                    self._state = ParseState.NORMAL

            elif self._state == ParseState.TAG_NAME:
                if char.isalnum() or char.isdigit():
                    self._current_tag.append(char)
                elif char in [' ', '>', '/']:
                    # Tag name complete, check if structural
                    tag_name = ''.join(self._current_tag)
                    match = self._classify_tag(tag_name, i)
                    if match:
                        return match
                    self._state = ParseState.NORMAL
                else:
                    self._state = ParseState.NORMAL

        return None

    def _classify_tag(self, tag_name: str, position: int) -> Optional[TagMatch]:
        """
        Classifies tag name into structural categories.

        Different approach: explicit classification logic
        instead of negative lookahead regex.

        Args:
            tag_name: Name of the tag to classify
            position: Current position in text

        Returns:
            TagMatch if tag is structural, None otherwise
        """
        # Check for exact matches first
        if tag_name in self.STRUCTURAL_TAGS:
            return TagMatch(
                tag_type=self.STRUCTURAL_TAGS[tag_name],
                tag_name=tag_name,
                start_pos=position - len(tag_name) - 1,
                end_pos=position
            )

        # Check for numbered variants (e.g., 'list1', 'list2')
        if tag_name.startswith('list') and len(tag_name) > 4:
            suffix = tag_name[4:]
            if suffix.isdigit():
                return TagMatch(
                    tag_type='sequence',
                    tag_name=tag_name,
                    start_pos=position - len(tag_name) - 1,
                    end_pos=position
                )

        # Check for numbered li variants
        if tag_name.startswith('li') and len(tag_name) > 2:
            suffix = tag_name[2:]
            if suffix.isdigit():
                return TagMatch(
                    tag_type='item',
                    tag_name=tag_name,
                    start_pos=position - len(tag_name) - 1,
                    end_pos=position
                )

        return None

    def _reset(self):
        """Resets parser state for new parsing operation"""
        self._state = ParseState.NORMAL
        self._current_tag = []
        self._position = 0


# Factory function for backward compatibility
def create_markup_analyzer() -> StructuralMarkupAnalyzer:
    """
    Creates a new markup analyzer instance.

    Returns:
        Configured StructuralMarkupAnalyzer
    """
    return StructuralMarkupAnalyzer()
