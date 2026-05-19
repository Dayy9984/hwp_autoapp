"""
Tag Classifier - Lookup table-based tag categorization.

This module provides tag classification using lookup tables and simple
validation rules, avoiding complex regular expressions.
"""

from typing import Optional, Set, List
from dataclasses import dataclass


@dataclass
class TagInfo:
    """Information about a classified tag"""
    category: str  # 'grid', 'sequence', 'item', 'unknown'
    name: str
    has_numeric_suffix: bool


class TagClassifier:
    """
    Classifies markup tags using lookup tables and simple validation.

    Approach: Instead of complex regex, use explicit categorization
    with straightforward validation rules.

    Example:
        >>> classifier = TagClassifier()
        >>> classifier.contains_structural_tags("<table>data</table>")
        True
        >>> info = classifier.classify("list1")
        >>> info.category
        'sequence'
    """

    # Primary tag categories
    GRID_TAGS: Set[str] = {'table'}
    SEQUENCE_TAGS: Set[str] = {'list', 'ol', 'ul'}
    ITEM_TAGS: Set[str] = {'li'}

    def __init__(self):
        self._enable_numbered_variants = True

    def contains_structural_tags(self, text: str) -> bool:
        """
        Check if text contains any structural tags.

        Args:
            text: Text to analyze

        Returns:
            True if structural tags found
        """
        # Extract all tags first
        tags = self._extract_tag_names(text)

        # Check each tag
        for tag in tags:
            info = self.classify(tag)
            if info.category != 'unknown':
                return True

        return False

    def classify(self, tag_name: str) -> TagInfo:
        """
        Classify a tag name into structural categories.

        Args:
            tag_name: Name of tag to classify

        Returns:
            TagInfo with category and metadata
        """
        tag_name = tag_name.lower().strip()

        # Check direct matches
        if tag_name in self.GRID_TAGS:
            return TagInfo(category='grid', name=tag_name, has_numeric_suffix=False)

        if tag_name in self.SEQUENCE_TAGS:
            return TagInfo(category='sequence', name=tag_name, has_numeric_suffix=False)

        if tag_name in self.ITEM_TAGS:
            return TagInfo(category='item', name=tag_name, has_numeric_suffix=False)

        # Check numbered variants if enabled
        if self._enable_numbered_variants:
            variant = self._check_numbered_variant(tag_name)
            if variant:
                return variant

        return TagInfo(category='unknown', name=tag_name, has_numeric_suffix=False)

    def _check_numbered_variant(self, tag_name: str) -> Optional[TagInfo]:
        """
        Check if tag is a numbered variant (e.g., 'list1', 'list2').

        Approach: Explicit string manipulation instead of regex.

        Args:
            tag_name: Tag name to check

        Returns:
            TagInfo if valid numbered variant, None otherwise
        """
        for base_tag in self.SEQUENCE_TAGS:
            if tag_name.startswith(base_tag) and len(tag_name) > len(base_tag):
                suffix = tag_name[len(base_tag):]
                if self._is_numeric_suffix(suffix):
                    return TagInfo(
                        category='sequence',
                        name=tag_name,
                        has_numeric_suffix=True
                    )

        for base_tag in self.ITEM_TAGS:
            if tag_name.startswith(base_tag) and len(tag_name) > len(base_tag):
                suffix = tag_name[len(base_tag):]
                if self._is_numeric_suffix(suffix):
                    return TagInfo(
                        category='item',
                        name=tag_name,
                        has_numeric_suffix=True
                    )

        return None

    def _is_numeric_suffix(self, suffix: str) -> bool:
        """
        Validate that suffix is purely numeric.

        Args:
            suffix: String to validate

        Returns:
            True if suffix is numeric
        """
        return suffix.isdigit() and len(suffix) > 0

    def _extract_tag_names(self, text: str) -> List[str]:
        """
        Extract tag names from text.

        Simple approach: Find < and >, extract content between them.

        Args:
            text: Text containing tags

        Returns:
            List of tag names
        """
        tags = []
        i = 0
        while i < len(text):
            if text[i] == '<':
                # Find closing >
                close_pos = text.find('>', i)
                if close_pos != -1:
                    # Extract tag content
                    tag_content = text[i+1:close_pos]
                    # Get tag name (first word)
                    tag_name = tag_content.split()[0] if tag_content else ''
                    # Remove / for closing tags
                    tag_name = tag_name.lstrip('/')
                    if tag_name:
                        tags.append(tag_name)
                    i = close_pos + 1
                else:
                    i += 1
            else:
                i += 1

        return tags


# Factory function
def create_tag_classifier() -> TagClassifier:
    """
    Creates a new tag classifier instance.

    Returns:
        Configured TagClassifier
    """
    return TagClassifier()
