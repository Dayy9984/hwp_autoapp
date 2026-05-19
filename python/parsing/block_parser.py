"""
Block Parser - Non-regex based block extraction.

Replaces complex regex patterns for table/list block detection
with explicit parsing logic.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class BlockInfo:
    """Information about a parsed block"""
    block_type: str  # 'grid', 'sequence', 'item'
    content: str
    start_pos: int
    end_pos: int
    attributes: Dict[str, str]


class BlockParser:
    """
    Parses structural blocks (tables, lists) from markup text.

    Uses explicit parsing instead of complex regex patterns,
    improving maintainability and clarity.
    """

    def __init__(self):
        self._position = 0
        self._text = ""

    def extract_table_blocks(self, text: str) -> List[BlockInfo]:
        """
        Extract table blocks from text.

        Replaces TABLE_BLOCK_PATTERN regex with explicit parsing.

        Args:
            text: Text to parse

        Returns:
            List of table blocks found
        """
        blocks = []
        self._text = text
        self._position = 0

        while self._position < len(text):
            # Look for table start tag
            start = text.find('<table', self._position)
            if start == -1:
                break

            # Find end of opening tag
            tag_end = text.find('>', start)
            if tag_end == -1:
                break

            # Extract attributes
            attrs = self._parse_attributes(text[start:tag_end + 1])

            # Find closing tag
            close_pos = self._find_closing_tag(text, 'table', tag_end + 1)
            if close_pos == -1:
                self._position = tag_end + 1
                continue

            # Extract content
            content = text[tag_end + 1:close_pos]

            blocks.append(BlockInfo(
                block_type='grid',
                content=content,
                start_pos=start,
                end_pos=close_pos + len('</table>'),
                attributes=attrs
            ))

            self._position = close_pos + len('</table>')

        return blocks

    def extract_list_blocks(self, text: str) -> List[BlockInfo]:
        """
        Extract list blocks from text.

        Replaces LIST_BLOCK_PATTERN regex with line-by-line parsing.

        Args:
            text: Text to parse

        Returns:
            List of list blocks found
        """
        blocks = []
        lines = text.split('\n')

        current_block = None
        start_line = -1

        for i, line in enumerate(lines):
            is_list_line = self._is_list_item_line(line)

            if is_list_line:
                if current_block is None:
                    # Start new block
                    current_block = []
                    start_line = i
                current_block.append(line)
            else:
                if current_block is not None:
                    # End current block
                    blocks.append(BlockInfo(
                        block_type='sequence',
                        content='\n'.join(current_block),
                        start_pos=start_line,
                        end_pos=i - 1,
                        attributes={}
                    ))
                    current_block = None

        # Handle block at end of text
        if current_block is not None:
            blocks.append(BlockInfo(
                block_type='sequence',
                content='\n'.join(current_block),
                start_pos=start_line,
                end_pos=len(lines) - 1,
                attributes={}
            ))

        return blocks

    def extract_list_items(self, text: str) -> List[Tuple[str, str]]:
        """
        Extract list items with markers and content.

        Replaces LIST_ITEM_PATTERN regex with explicit parsing.

        Args:
            text: Text containing list items

        Returns:
            List of (marker, content) tuples
        """
        items = []
        lines = text.split('\n')

        for line in lines:
            if not self._is_list_item_line(line):
                continue

            marker, content = self._parse_list_item(line)
            if marker is not None:
                items.append((marker, content))

        return items

    def extract_table_rows(self, table_html: str) -> List[str]:
        """
        Extract table rows from table HTML.

        Replaces TR_ROW_PATTERN regex.

        Args:
            table_html: Table HTML content

        Returns:
            List of row contents
        """
        rows = []
        pos = 0

        while pos < len(table_html):
            # Find <tr
            start = table_html.find('<tr', pos)
            if start == -1:
                break

            # Find end of opening tag
            tag_end = table_html.find('>', start)
            if tag_end == -1:
                break

            # Find closing </tr>
            close = table_html.find('</tr>', tag_end)
            if close == -1:
                # No closing tag, take rest of text
                rows.append(table_html[tag_end + 1:])
                break

            # Extract row content
            rows.append(table_html[tag_end + 1:close])
            pos = close + len('</tr>')

        return rows

    def extract_table_cells(self, row_html: str) -> List[str]:
        """
        Extract table cells from row HTML.

        Replaces TD_TH_CELL_PATTERN regex.

        Args:
            row_html: Row HTML content

        Returns:
            List of cell contents
        """
        cells = []
        pos = 0

        while pos < len(row_html):
            # Find <td or <th
            td_pos = row_html.find('<td', pos)
            th_pos = row_html.find('<th', pos)

            # Determine which comes first
            if td_pos == -1 and th_pos == -1:
                break
            elif td_pos == -1:
                start = th_pos
                tag_name = 'th'
            elif th_pos == -1:
                start = td_pos
                tag_name = 'td'
            else:
                if td_pos < th_pos:
                    start = td_pos
                    tag_name = 'td'
                else:
                    start = th_pos
                    tag_name = 'th'

            # Find end of opening tag
            tag_end = row_html.find('>', start)
            if tag_end == -1:
                break

            # Find closing tag
            close_tag = f'</{tag_name}>'
            close = row_html.find(close_tag, tag_end)

            if close == -1:
                # No closing tag, take rest of text
                cells.append(row_html[tag_end + 1:])
                break

            # Extract cell content
            cells.append(row_html[tag_end + 1:close])
            pos = close + len(close_tag)

        return cells

    def extract_numeric_list_tag(self, tag: str) -> Optional[int]:
        """
        Extract numeric suffix from list tag (e.g., 'list1' -> 1).

        Replaces NUMERIC_LIST_TAG_PATTERN regex.

        Args:
            tag: Tag name to parse

        Returns:
            Numeric suffix if found, None otherwise
        """
        tag = tag.strip().lower()

        # Check for li[N] pattern
        if tag.startswith('li'):
            suffix = tag[2:]
            if suffix.isdigit():
                return int(suffix)

        # Check for list[N] pattern
        if tag.startswith('list'):
            suffix = tag[4:]
            if suffix.isdigit():
                return int(suffix)

        return None

    def _is_list_item_line(self, line: str) -> bool:
        """Check if line is a list item"""
        line = line.strip()
        if not line:
            return False

        # Check for <li or <list tags
        return (line.startswith('<li') or
                line.startswith('<list'))

    def _parse_list_item(self, line: str) -> Tuple[Optional[str], str]:
        """
        Parse list item line into marker and content.

        Returns:
            (marker, content) tuple
        """
        line = line.strip()

        # Find opening tag end
        tag_end = line.find('>')
        if tag_end == -1:
            return None, line

        # Extract tag content (marker)
        tag_content = line[1:tag_end]
        parts = tag_content.split(None, 1)

        tag_name = parts[0]
        marker = parts[1] if len(parts) > 1 else ''

        # Extract content after tag
        content = line[tag_end + 1:].strip()

        return marker, content

    def _parse_attributes(self, tag: str) -> Dict[str, str]:
        """Parse attributes from tag"""
        attrs = {}

        # Remove < and >
        tag = tag.strip('<>')

        # Split by spaces
        parts = tag.split()
        if not parts:
            return attrs

        # First part is tag name, rest are attributes
        for part in parts[1:]:
            if '=' in part:
                key, value = part.split('=', 1)
                # Remove quotes
                value = value.strip('"\'')
                attrs[key] = value

        return attrs

    def _find_closing_tag(self, text: str, tag_name: str, start_pos: int) -> int:
        """
        Find position of closing tag, accounting for nested tags.

        Args:
            text: Text to search
            tag_name: Name of tag to close
            start_pos: Position to start search

        Returns:
            Position of closing tag, or -1 if not found
        """
        open_tag = f'<{tag_name}'
        close_tag = f'</{tag_name}>'

        depth = 1
        pos = start_pos

        while pos < len(text) and depth > 0:
            # Look for next open or close tag
            next_open = text.find(open_tag, pos)
            next_close = text.find(close_tag, pos)

            if next_close == -1:
                return -1

            if next_open != -1 and next_open < next_close:
                # Found nested opening tag
                depth += 1
                pos = next_open + len(open_tag)
            else:
                # Found closing tag
                depth -= 1
                if depth == 0:
                    return next_close
                pos = next_close + len(close_tag)

        return -1


# Factory function
def create_block_parser() -> BlockParser:
    """Create a new block parser instance"""
    return BlockParser()
