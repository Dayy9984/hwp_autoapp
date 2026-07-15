"""
Dynamic document segment registry with position tracking.

Manages hierarchical segment positioning and content updates during live editing sessions.
"""

import re
import json
from typing import Any, Dict, Tuple, Optional, Callable
from operator import itemgetter


class ContentSegment:
    """Document element with spatial coordinates and textual payload"""

    def __init__(
        self,
        segment_id: str,
        position: Tuple[int, int, int],
        text: str = "",
        segment_type: str = "text",
        table_group_id: Optional[int] = None,
        attrs: Optional[Dict[str, str]] = None,
        td_sig: Optional[str] = None,
        p_sig: Optional[str] = None,
        table_path: Optional[str] = None,
    ):
        self.id = segment_id
        # Unpack spatial triplet
        lp, pp, cp = position
        self.list_pos = lp
        self.para_pos = pp
        self.char_pos = cp
        self.text = text
        # Element category: text/list/td/annotation_anchor/annotation_content
        self.segment_type = segment_type
        # Optional table membership indicator
        self.table_group_id = table_group_id
        self.attrs: Dict[str, str] = attrs or {}
        self.td_sig = td_sig
        self.p_sig = p_sig
        self.table_path = table_path

    @property
    def position(self) -> Tuple[int, int, int]:
        """Spatial coordinate triplet accessor"""
        return (self.list_pos, self.para_pos, self.char_pos)

    @property
    def block_type(self) -> str:
        """Legacy compatibility alias for segment_type."""
        return self.segment_type

    @block_type.setter
    def block_type(self, value: str) -> None:
        self.segment_type = value

    def update_position(
        self, list_pos: int = None, para_pos: int = None, char_pos: int = None
    ):
        """Mutate spatial coordinates selectively"""
        coords_updated = False
        if list_pos is not None:
            self.list_pos = list_pos
            coords_updated = True
        if para_pos is not None:
            self.para_pos = para_pos
            coords_updated = True
        if char_pos is not None:
            self.char_pos = char_pos
            coords_updated = True
        return coords_updated

    def has_text_content(self) -> bool:
        """Check if segment contains non-empty text"""
        return bool(self.text and self.text.strip())

    def is_table_member(self) -> bool:
        """Check if segment belongs to a table group"""
        return self.table_group_id is not None

    def __repr__(self):
        preview = self.text[:30] if self.text else ""
        return f"ContentSegment(id={self.id}, type={self.segment_type}, pos={self.position}, tbl_grp={self.table_group_id}, td_sig={self.td_sig}, p_sig={self.p_sig}, txt={preview}...)"


class AnnotationSegment(ContentSegment):
    """Footnote/endnote element with anchor-content linkage"""

    def __init__(
        self,
        segment_id: str,
        position: Tuple[int, int, int],
        text: str = "",
        annotation_num: int = None,
        anchor_id: str = None,
        content_id: str = None,
    ):
        super().__init__(segment_id, position, text, segment_type="annotation")
        # Numeric annotation identifier (1-indexed)
        self.annotation_num = annotation_num
        # Anchor marker element in document flow
        self.anchor_id = anchor_id
        # Content payload element (footnote text)
        self.content_id = content_id

    def __repr__(self):
        return f"AnnotationSegment(id={self.id}, num={self.annotation_num}, anchor_elem={self.anchor_id}, content_elem={self.content_id}, coords={self.position})"


class SegmentRegistry:
    """Hierarchical segment position/content tracking engine.

    Coordinates spatial updates across document modification operations.
    """

    def __init__(
        self,
        extracted: Tuple[str, Dict[int, Tuple[int, int, int]]],
        log_to_main: Callable[[str, Optional[str]], None] = None,
    ):
        markup_data, spatial_map = extracted
        self.log_to_main = log_to_main or (lambda msg, lvl: None)

        # Core element registry
        self.segments: Dict[str, ContentSegment] = {}

        # Insertion delta tracking for position adjustment
        self.insertion_tracker: Dict[int, Dict[int, int]] = {}

        # Annotation (footnote) indexing structures
        self.annotations: Dict[str, Dict] = {}  # num → metadata
        self.annotation_anchors: Dict[str, str] = {}  # anchor_id → num
        self.annotation_contents: Dict[str, str] = {}  # content_id → num

        # Table topology caches
        self._list_pos_to_table_group_id: Dict[int, int] = {}
        self._table_group_id_to_rep_pos: Dict[int, Tuple[int, int, int]] = {}

        # Bootstrap registry from HDML markup
        self.initialize_from_hdml(markup_data, spatial_map)

    @classmethod
    def from_segments(
        cls,
        segments: Dict[str, ContentSegment],
        log_to_main: Callable[[str, Optional[str]], None] = None,
    ) -> "SegmentRegistry":
        """Build a registry directly from prepared segments (HDML text not required)."""
        instance = cls.__new__(cls)
        instance.log_to_main = log_to_main or (lambda msg, lvl: None)

        instance.segments = {}
        for sid, seg in (segments or {}).items():
            if not isinstance(seg, ContentSegment):
                continue
            seg.id = str(getattr(seg, "id", sid))
            instance.segments[str(seg.id)] = seg

        instance.insertion_tracker = {}
        instance.annotations = {}
        instance.annotation_anchors = {}
        instance.annotation_contents = {}
        instance._list_pos_to_table_group_id = {}
        instance._table_group_id_to_rep_pos = {}
        instance._rebuild_table_group_indexes()
        return instance

    def _rebuild_table_group_indexes(self) -> None:
        """Rebuild list_pos/group caches from current segments."""
        self._list_pos_to_table_group_id = {}
        self._table_group_id_to_rep_pos = {}

        for elem in self.segments.values():
            raw_group = getattr(elem, "table_group_id", None)
            if raw_group is None:
                continue

            try:
                normalized_group = int(raw_group)
            except Exception:
                normalized_group = raw_group

            elem.table_group_id = normalized_group
            list_pos = getattr(elem, "list_pos", None)
            if isinstance(list_pos, int) and list_pos not in self._list_pos_to_table_group_id:
                self._list_pos_to_table_group_id[list_pos] = normalized_group

            rep_pos = self._table_group_id_to_rep_pos.get(normalized_group)
            if rep_pos is None or elem.position < rep_pos:
                self._table_group_id_to_rep_pos[normalized_group] = elem.position

        for elem in self.segments.values():
            if getattr(elem, "table_group_id", None) is not None:
                continue
            inherited_group = self._list_pos_to_table_group_id.get(getattr(elem, "list_pos", None))
            if inherited_group is not None:
                elem.table_group_id = inherited_group

    def initialize_from_hdml(self, hdml: str, id_to_pos: Dict):
        """Bootstrap registry from HDML markup and spatial map"""
        # Phase 1: Normalize position dictionary
        pos_map = self._normalize_pos_dict(id_to_pos)

        # Phase 2: Extract table topology first (order reversal!)
        table_topology = self._build_table_topology(hdml)

        # Phase 3: Build segments via dispatch pattern
        self._dispatch_segment_creation(hdml, pos_map)

        # Phase 4: Supplement missing ID tokens
        self._supplement_orphan_ids(hdml, pos_map)

        # Phase 5: Apply table group assignments
        self._apply_table_groups(table_topology)

    def _normalize_pos_dict(self, id_to_pos: Dict) -> Dict[str, Tuple]:
        """Standardize position mapping to string-keyed format"""
        normalized = {}
        for identifier, coordinates in id_to_pos.items():
            str_key = str(identifier)
            normalized[str_key] = coordinates
        return normalized

    def _build_table_topology(self, hdml: str) -> Dict[str, Dict[str, Any]]:
        """Extract table structure topology from HDML markup"""
        cell_to_group: Dict[str, Dict[str, Any]] = {}
        current_group_id = 0
        table_depth = 0
        table_stack: list[int] = []

        table_start_pattern = r"<table[^>]*>"
        cell_id_pattern = r'<td[^>]*\bid="([0-9]+)"'

        for line in hdml.split("\n"):
            start_count = len(re.findall(table_start_pattern, line))
            end_count = line.count("</table>")

            for _ in range(start_count):
                current_group_id += 1
                table_depth += 1
                table_stack.append(current_group_id)

            active_group = table_stack[-1] if table_stack else None
            if active_group is not None:
                discovered_cells = re.findall(cell_id_pattern, line)
                for cell_id in discovered_cells:
                    cell_to_group[cell_id] = {
                        "group_id": active_group,
                        "table_path": "/".join(str(g) for g in table_stack),
                        "depth": table_depth,
                    }

            for _ in range(end_count):
                if table_stack:
                    table_stack.pop()
                table_depth = max(0, table_depth - 1)

        return cell_to_group

    def _extract_attr_map(self, tag_text: str) -> Dict[str, str]:
        """Extract attribute key/value pairs from a single tag string."""
        if not tag_text:
            return {}
        attrs: Dict[str, str] = {}
        for key, value in re.findall(r'([a-zA-Z_:-]+)="([^"]*)"', tag_text):
            attrs[key] = value
        return attrs

    def _dispatch_segment_creation(self, hdml: str, pos_map: Dict):
        """Orchestrate segment extraction via strategy dispatch"""
        # Dispatch strategy pipeline for different element types
        extraction_pipeline = [
            ("text", self._extract_text_elements),
            ("list", self._extract_list_elements),
            ("cell", self._extract_cell_elements),
            ("textbox", self._extract_textbox_elements),
            ("annotation", self._extract_annotation_pairs),
        ]

        for strategy_name, extractor_func in extraction_pipeline:
            extractor_func(hdml, pos_map)

    def _extract_text_elements(self, hdml: str, pos_map: Dict):
        """Extract plain text elements via pattern matching"""
        pattern = r'(<p id="([0-9]+)"[^>]*>)([^<]*)</p>'

        for line in hdml.split("\n"):
            match_result = re.search(pattern, line)
            if match_result is None:
                continue

            opening_tag = match_result.group(1)
            elem_identifier = match_result.group(2)
            elem_payload = match_result.group(3).rstrip()
            tag_attrs = self._extract_attr_map(opening_tag)

            # Skip if position unknown or already registered
            if elem_identifier not in pos_map:
                continue
            if elem_identifier in self.segments:
                continue

            # Create and register element
            elem = ContentSegment(
                elem_identifier,
                tuple(pos_map[elem_identifier]),
                elem_payload or "",
                segment_type="text",
                attrs=tag_attrs,
                p_sig=tag_attrs.get("data-p-sig"),
            )
            self.segments[elem_identifier] = elem

    def _extract_list_elements(self, hdml: str, pos_map: Dict):
        """Extract bulleted/numbered list elements"""
        pattern = r'<list[^>]*>\s*(<p id="([0-9]+)"[^>]*>)([^<]*)</p>'

        for line in hdml.split("\n"):
            match_result = re.search(pattern, line)
            if match_result is None:
                continue

            opening_tag = match_result.group(1)
            elem_identifier = match_result.group(2)
            elem_payload = match_result.group(3).rstrip()
            tag_attrs = self._extract_attr_map(opening_tag)

            # Skip if position unknown
            if elem_identifier not in pos_map:
                continue

            if elem_identifier not in self.segments:
                # Create new list element
                elem = ContentSegment(
                    elem_identifier,
                    tuple(pos_map[elem_identifier]),
                    elem_payload or "",
                    segment_type="list",
                    attrs=tag_attrs,
                    p_sig=tag_attrs.get("data-p-sig"),
                )
                self.segments[elem_identifier] = elem
            else:
                # Upgrade existing text element to list type
                existing_elem = self.segments[elem_identifier]
                current_type = getattr(existing_elem, "segment_type", "text")
                if current_type != "list":
                    existing_elem.segment_type = "list"
                if tag_attrs.get("data-p-sig"):
                    existing_elem.p_sig = tag_attrs.get("data-p-sig")
                if tag_attrs:
                    existing_elem.attrs.update(tag_attrs)

    def _extract_cell_elements(self, hdml: str, pos_map: Dict):
        """Extract table cell container elements"""
        pattern = r'(<td[^>]*\bid="([0-9]+)"[^>]*>)'

        for line in hdml.split("\n"):
            for match_result in re.finditer(pattern, line):
                opening_tag = match_result.group(1)
                cell_identifier = match_result.group(2)
                tag_attrs = self._extract_attr_map(opening_tag)

                # Skip if position unknown or already registered
                if cell_identifier not in pos_map:
                    continue
                if cell_identifier in self.segments:
                    continue

                # Create cell container
                cell_elem = ContentSegment(
                    cell_identifier,
                    tuple(pos_map[cell_identifier]),
                    "",
                    segment_type="td",
                    attrs=tag_attrs,
                    td_sig=tag_attrs.get("data-td-sig"),
                )
                self.segments[cell_identifier] = cell_elem

    def _extract_textbox_elements(self, hdml: str, pos_map: Dict):
        """Extract textbox container elements"""
        pattern = r'<textbox id="([0-9]+)"[^>]*>'

        for line in hdml.split("\n"):
            match_result = re.search(pattern, line)
            if match_result is None:
                continue

            box_identifier = match_result.group(1)

            # Skip if position unknown
            if box_identifier not in pos_map:
                continue

            if box_identifier in self.segments:
                # Upgrade existing element to textbox type
                existing_box = self.segments[box_identifier]
                existing_box.segment_type = "textbox"
            else:
                # Create new textbox container
                box_elem = ContentSegment(
                    box_identifier,
                    tuple(pos_map[box_identifier]),
                    "",
                    segment_type="textbox",
                )
                self.segments[box_identifier] = box_elem

    def _extract_annotation_pairs(self, hdml: str, pos_map: Dict):
        """Extract footnote anchor/content element pairs"""
        pattern = r"^<각주([0-9]+)\s+([0-9]+)><([0-9]+)>\s*(.*)"

        for line in hdml.split("\n"):
            match_result = re.match(pattern, line)
            if match_result is None:
                continue

            # Parse annotation components
            anno_number = int(match_result.group(1))
            anchor_id = match_result.group(2)
            content_id = match_result.group(3)
            content_text = match_result.group(4).rstrip()

            # Build anchor marker element
            if anchor_id in pos_map:
                anchor_elem = AnnotationSegment(
                    anchor_id,
                    tuple(pos_map[anchor_id]),
                    f"[각주{anno_number}]",
                    annotation_num=anno_number,
                    anchor_id=anchor_id,
                    content_id=content_id,
                )
                anchor_elem.segment_type = "annotation_anchor"
                self.segments[anchor_id] = anchor_elem

            # Build content payload element
            if content_id in pos_map:
                content_elem = AnnotationSegment(
                    content_id,
                    tuple(pos_map[content_id]),
                    content_text,
                    annotation_num=anno_number,
                    anchor_id=anchor_id,
                    content_id=content_id,
                )
                content_elem.segment_type = "annotation_content"
                self.segments[content_id] = content_elem

            # Register in annotation lookup tables
            registry_key = str(anno_number)
            self.annotations[registry_key] = {
                "anchor_id": anchor_id,
                "content_id": content_id,
                "text": content_text,
                "annotation_num": anno_number,
            }
            self.annotation_anchors[anchor_id] = registry_key
            self.annotation_contents[content_id] = registry_key

    def _supplement_orphan_ids(self, hdml: str, pos_map: Dict):
        """Collect and register unmatched ID tokens"""
        try:
            # Scan entire HDML for ID attributes
            discovered_ids = set(re.findall(r'id="([0-9]+)"', hdml))

            # Register orphans as text elements
            for discovered_id in discovered_ids:
                # Skip if no position or already registered
                if discovered_id not in pos_map:
                    continue
                if discovered_id in self.segments:
                    continue

                # Create fallback text element
                orphan_elem = ContentSegment(
                    discovered_id,
                    tuple(pos_map[discovered_id]),
                    "",
                    segment_type="text",
                )
                self.segments[discovered_id] = orphan_elem
        except Exception:
            # Silently ignore supplementation failures
            pass

    def _apply_table_groups(self, topology: Dict[str, Dict[str, Any]]):
        """Distribute table group assignments using topology"""
        try:
            # Phase A: Assign groups to cell containers (TD elements)
            for elem in self.segments.values():
                elem_category = getattr(elem, "segment_type", "")
                if elem_category != "td":
                    continue

                # Lookup group assignment
                topo_entry = topology.get(elem.id)
                if topo_entry is None:
                    continue
                assigned_group = topo_entry.get("group_id")
                if assigned_group is None:
                    continue

                # Register position → group mapping
                lp = elem.list_pos
                if lp not in self._list_pos_to_table_group_id:
                    self._list_pos_to_table_group_id[lp] = assigned_group

                # Apply group to element
                elem.table_group_id = assigned_group
                elem.table_path = topo_entry.get("table_path")

                # Track representative position for group
                rep_pos = self._table_group_id_to_rep_pos.get(assigned_group)
                if rep_pos is None or elem.position < rep_pos:
                    self._table_group_id_to_rep_pos[assigned_group] = elem.position

            # Phase B: Propagate groups to content elements via list_pos
            for elem in self.segments.values():
                # Skip header positions
                if elem.list_pos <= 2:
                    continue

                # Skip special element types
                elem_category = getattr(elem, "segment_type", "")
                if elem_category in ("annotation_anchor", "annotation_content", "td"):
                    continue

                # Inherit group from list position
                inherited_group = self._list_pos_to_table_group_id.get(elem.list_pos)
                if inherited_group is None:
                    continue

                elem.table_group_id = inherited_group

                # Update representative position if needed
                rep_pos = self._table_group_id_to_rep_pos.get(inherited_group)
                if rep_pos is None or elem.position < rep_pos:
                    self._table_group_id_to_rep_pos[inherited_group] = elem.position

        except Exception as error:
            self.log_to_main(f"[ERROR] Group assignment failure: {error}", "ERROR")

    def retrieve_segment(self, segment_id: str) -> Optional[ContentSegment]:
        """Lookup segment by identifier"""
        sid = str(segment_id)
        return self.segments.get(sid) if sid in self.segments else None

    def retrieve_location(self, segment_id: str) -> Optional[Tuple[int, int, int]]:
        """Extract spatial coordinates for segment"""
        elem = self.retrieve_segment(segment_id)
        if not elem:
            return None
        return elem.position

    def retrieve_content(self, segment_id: str) -> Optional[str]:
        """Get textual payload of segment"""
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return None
        return elem.text

    def retrieve_cell_content(self, list_pos: int) -> str:
        """Aggregate cell content from constituent segments

        Args:
            list_pos: Spatial list coordinate

        Returns:
            Concatenated text ordered by paragraph position
        """
        # Filter for content-bearing segments at this position
        content_items = []
        for elem in self.segments.values():
            if elem.list_pos != list_pos:
                continue

            elem_category = getattr(elem, "segment_type", "text")
            if elem_category not in ("text", "list"):
                continue

            content_items.append((elem.para_pos, elem.text or ""))

        # Early exit for empty cells
        if not content_items:
            return ""

        # Assemble content in spatial order
        content_items.sort(key=lambda pair: pair[0])
        return "\n".join(payload for _, payload in content_items)

    def update_cell_content_segments(self, list_pos: int, new_text: str) -> None:
        """Distribute multiline text across cell segments by line splitting"""
        # Scan for content-bearing segments at target position
        segment_roster = []
        for sid, elem in self.segments.items():
            if elem.list_pos != list_pos:
                continue

            elem_category = getattr(elem, "segment_type", "text")
            if elem_category in ("text", "list"):
                segment_roster.append((elem.para_pos, sid))

        # Early exit for empty cells
        if not segment_roster:
            return

        # Order segments spatially
        segment_roster.sort(key=lambda pair: pair[0])

        # Split incoming text by lines
        text_lines = (new_text or "").splitlines()
        if not text_lines:
            text_lines = [""]

        # Distribute lines to segments
        for line_idx, (_, sid) in enumerate(segment_roster):
            payload = text_lines[line_idx] if line_idx < len(text_lines) else ""
            target_elem = self.retrieve_segment(sid)
            if target_elem:
                target_elem.text = payload

    def retrieve_final_content_segment_in_cell(self, segment_id: str) -> Optional[str]:
        """Find terminal content segment in cell by maximum paragraph position

        Args:
            segment_id: Cell anchor identifier

        Returns:
            Identifier of last content segment, None for empty cells
        """
        anchor = self.retrieve_segment(str(segment_id))
        if anchor is None:
            return None

        target_lp = anchor.list_pos

        # Scan for content segments at this list position
        content_entries = []
        for sid, elem in self.segments.items():
            if elem.list_pos != target_lp:
                continue

            elem_category = getattr(elem, "segment_type", "text")
            if elem_category not in ("text", "list"):
                continue

            content_entries.append((elem.para_pos, sid))

        # Handle empty cell case
        if not content_entries:
            return None

        # Extract terminal entry
        content_entries.sort(key=lambda pair: pair[0])
        return content_entries[-1][1]

    def retrieve_cell_segment_identifier(self, segment_id: str) -> Optional[str]:
        """Find parent cell (td) identifier for segment"""
        elem = self.retrieve_segment(str(segment_id))
        if elem is None:
            return None

        # If already a cell, return self
        if getattr(elem, "segment_type", None) == "td":
            return elem.id

        # Extract spatial attributes
        query_lp = elem.list_pos
        query_group = getattr(elem, "table_group_id", None)

        # Scan for matching cell container
        for sid, candidate in self.segments.items():
            if getattr(candidate, "segment_type", None) != "td":
                continue

            if candidate.list_pos != query_lp:
                continue

            # Validate group membership if applicable
            if query_group is not None:
                cand_group = getattr(candidate, "table_group_id", None)
                try:
                    if cand_group is not None and int(cand_group) != int(query_group):
                        continue
                except Exception:
                    continue

            return sid

        return None

    def retrieve_cell_segment_by_list_position(self, list_pos: int) -> Optional[str]:
        """Locate cell (td) element at specified list coordinate"""
        for sid, elem in self.segments.items():
            elem_category = getattr(elem, "segment_type", None)
            if elem_category != "td":
                continue

            elem_lp = getattr(elem, "list_pos", None)
            if elem_lp == list_pos:
                return sid

        return None

    def retrieve_table_group_identifier(self, segment_id: str) -> Optional[int]:
        """Extract table group membership for segment"""
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return None

        group_attr = getattr(elem, "table_group_id", None)
        return group_attr

    def retrieve_table_group_by_list_position(self, list_pos: int) -> Optional[int]:
        """Resolve group ID via list position lookup"""
        pos_key = list_pos
        if pos_key not in self._list_pos_to_table_group_id:
            return None
        return self._list_pos_to_table_group_id[pos_key]

    def retrieve_representative_location_for_table_group(
        self, table_group_id: int
    ) -> Optional[Tuple[int, int, int]]:
        """Fetch canonical coordinates for table group"""
        location_registry = self._table_group_id_to_rep_pos
        sentinel_marker = object()
        stored_coords = location_registry.get(table_group_id, sentinel_marker)
        coords_found = stored_coords is not sentinel_marker
        if not coords_found:
            return None
        return stored_coords

    def retrieve_adjusted_location(self, segment_id: str) -> Optional[Tuple[int, int, int]]:
        """Compute position after applying insertion delta"""
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return None

        base_coords = list(elem.position)
        lp, pp = elem.list_pos, elem.para_pos

        # Accumulate insertion offsets
        if lp not in self.insertion_tracker:
            return tuple(base_coords)

        delta = 0
        for insert_pp, insert_count in self.insertion_tracker[lp].items():
            if insert_pp <= pp:
                delta += insert_count

        base_coords[1] += delta
        return tuple(base_coords)

    def update_content(self, segment_id: str, new_text: str):
        """Replace textual payload of segment"""
        elem = self.retrieve_segment(segment_id)
        if elem is not None:
            elem.text = new_text

    def update_location(self, segment_id: str, new_position: Tuple[int, int, int]):
        """Relocate segment to new coordinates"""
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return

        lp, pp, cp = new_position
        elem.update_position(lp, pp, cp)

    def register_segment(self, segment_id: str, position: Tuple[int, int, int], text: str = ""):
        """Insert new segment into registry"""
        sid = str(segment_id)
        elem = ContentSegment(sid, position, text or "")
        self.segments[sid] = elem

    def unregister_segment(self, segment_id: str):
        """Remove segment from registry"""
        sid = str(segment_id)
        if sid not in self.segments:
            return
        del self.segments[sid]

    def update_after_paragraph_position_insertion(self, segment_id: str, inserted_count: int = 1):
        """Record paragraph insertion delta for spatial recalculation

        Args:
            segment_id: Anchor segment identifier
            inserted_count: Paragraph count added
        """
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return

        lp, pp = elem.list_pos, elem.para_pos

        # Initialize tracker for this list if needed
        if lp not in self.insertion_tracker:
            self.insertion_tracker[lp] = {}

        # Accumulate insertion count at this position
        self.insertion_tracker[lp][pp] = self.insertion_tracker[lp].get(pp, 0) + inserted_count

        # Collect segments requiring update
        update_candidates = [
            (sid, s) for sid, s in self.segments.items()
            if s.list_pos == lp and s.para_pos > pp
        ]

        # Note: original position preserved, adjustment applied during retrieval

    def update_after_list_position_insertion(
        self, segment_id: str, list_pos_added: int, update_start_list_pos: int
    ):
        """Shift list positions after insertion operation

        Args:
            segment_id: Anchor segment identifier
            list_pos_added: Count of inserted positions
            update_start_list_pos: Insertion start coordinate
        """

        # Collect segments requiring position shift
        shift_candidates = [
            (sid, s) for sid, s in self.segments.items()
            if s.list_pos > 2 and s.list_pos >= update_start_list_pos
        ]

        # Apply list position offset
        for sid, elem in shift_candidates:
            elem.update_position(list_pos=elem.list_pos + list_pos_added)

        # Rebuild insertion tracker with shifted keys
        rebuilt_tracker = {}
        for lp, insertion_data in self.insertion_tracker.items():
            if lp > 0 and lp >= update_start_list_pos:
                # Shift positions at or after insertion point
                shifted_lp = lp + list_pos_added
                rebuilt_tracker[shifted_lp] = insertion_data
            else:
                # Preserve positions before insertion
                rebuilt_tracker[lp] = insertion_data

        self.insertion_tracker = rebuilt_tracker

    def update_after_list_position_removal(
        self, segment_id: str, list_pos_deleted: int, deleted_start_list_pos: int
    ):
        """Shift list positions after deletion operation

        Args:
            segment_id: Reference segment identifier
            list_pos_deleted: Count of deleted list positions
            deleted_start_list_pos: First deleted position
        """

        # Determine update threshold
        threshold = deleted_start_list_pos + list_pos_deleted

        # Collect segments beyond deleted range
        update_targets = [
            (sid, s) for sid, s in self.segments.items()
            if s.list_pos >= threshold
        ]

        # Apply list position shift
        for sid, elem in update_targets:
            elem.update_position(list_pos=elem.list_pos - list_pos_deleted)

        # Rebuild insertion tracker with adjusted keys
        rebuilt_tracker = {}
        for lp, insertions in self.insertion_tracker.items():
            if lp >= threshold:
                # Shift down positions beyond deletion
                rebuilt_tracker[lp - list_pos_deleted] = insertions
            elif lp < deleted_start_list_pos:
                # Preserve positions before deletion
                rebuilt_tracker[lp] = insertions
            # Positions in deleted range are dropped

        self.insertion_tracker = rebuilt_tracker

    def export_current_registry(self) -> Dict[str, Dict]:
        """Serialize registry state to dictionary format"""
        snapshot = {}
        for sid, elem in self.segments.items():
            snapshot[sid] = {
                "position": elem.position,
                "adjusted_position": self.retrieve_adjusted_location(sid),
                "text": elem.text,
            }
        return snapshot

    def export_registry_to_json(self, filepath: str):
        """Persist registry snapshot to JSON file"""
        snapshot = self.export_current_registry()
        with open(filepath, "w", encoding="utf-8") as output_file:
            json.dump(snapshot, output_file, ensure_ascii=False, indent=2)

    def display_debug_information(self):
        """Emit diagnostic snapshot of registry state"""
        sep = "=" * 60
        self.log_to_main(f"\n{sep}")
        self.log_to_main("[DIAGNOSTICS] Registry State Snapshot")
        self.log_to_main(sep)
        self.log_to_main(f"Element count: {len(self.segments)}")
        self.log_to_main("Insertion delta tracking:")
        for lp, delta_map in self.insertion_tracker.items():
            self.log_to_main(f"  List position {lp}: {delta_map}")
            aggregate = sum(delta_map.values())
            self.log_to_main(f"    Aggregate delta: {aggregate} paragraphs")
        self.log_to_main("\nSample elements (first 5):")
        for idx, (sid, elem) in enumerate(list(self.segments.items())[:5]):
            preview = elem.text[:30] if elem.text else ""
            self.log_to_main(f"  [{sid}] coords={elem.position}, preview={preview}...")
        self.log_to_main(f"{sep}\n")

    # Annotation query interface
    def retrieve_annotation_by_number(self, annotation_num: int) -> Optional[Dict]:
        """Lookup annotation metadata by numeric identifier"""
        key = str(annotation_num)
        return self.annotations.get(key)

    def retrieve_annotation_by_anchor_identifier(self, anchor_id: str) -> Optional[Dict]:
        """Resolve annotation via anchor element identifier"""
        num_key = self.annotation_anchors.get(str(anchor_id))
        if num_key is None:
            return None
        return self.annotations.get(num_key)

    def retrieve_annotation_by_content_identifier(self, content_id: str) -> Optional[Dict]:
        """Resolve annotation via content element identifier"""
        num_key = self.annotation_contents.get(str(content_id))
        if num_key is None:
            return None
        return self.annotations.get(num_key)

    def retrieve_all_annotations(self) -> Dict[str, Dict]:
        """Export complete annotation registry"""
        return dict(self.annotations)

    def is_annotation_anchor(self, segment_id: str) -> bool:
        """Test if segment is annotation anchor marker"""
        elem = self.retrieve_segment(str(segment_id))
        if elem is None:
            return False
        return (
            hasattr(elem, "segment_type")
            and elem.segment_type == "annotation_anchor"
        )

    def is_annotation_content(self, segment_id: str) -> bool:
        """Test if segment is annotation content block"""
        elem = self.retrieve_segment(str(segment_id))
        if elem is None:
            return False
        return (
            hasattr(elem, "segment_type")
            and elem.segment_type == "annotation_content"
        )

    def adjust_locations_after_removal(self, deleted_segment_id: str) -> None:
        """Compensate spatial coordinates after element deletion

        Args:
            deleted_segment_id: Identifier of removed element
        """
        victim = self.retrieve_segment(str(deleted_segment_id))
        if victim is None:
            return

        v_lp, v_pp = victim.list_pos, victim.para_pos

        # Collect downstream segments requiring position shift
        affected_elems = [
            (sid, s) for sid, s in self.segments.items()
            if s.list_pos == v_lp and s.para_pos > v_pp
        ]

        # Decrement paragraph positions
        for sid, elem in affected_elems:
            elem.update_position(para_pos=elem.para_pos - 1)

        # Purge deleted element
        self.unregister_segment(str(deleted_segment_id))

        # Rebuild insertion tracker for affected list
        if v_lp not in self.insertion_tracker:
            return

        adjusted_tracker = {}
        for ins_pp, ins_count in self.insertion_tracker[v_lp].items():
            if ins_pp > v_pp:
                adjusted_tracker[ins_pp - 1] = ins_count
            elif ins_pp < v_pp:
                adjusted_tracker[ins_pp] = ins_count
            # Drop entry at deleted position

        self.insertion_tracker[v_lp] = adjusted_tracker

    def adjust_locations_after_row_removal(self, deleted_segment_id: str) -> None:
        """Compensate table structure after row deletion

        Args:
            deleted_segment_id: Cell identifier of removed row
        """
        victim_cell = self.retrieve_segment(str(deleted_segment_id))
        if victim_cell is None:
            return

        # Verify table group membership
        group_id = victim_cell.table_group_id
        if group_id is None:
            return

        victim_lp = victim_cell.list_pos

        # Identify all cells in deleted row
        doomed_cells = [
            sid for sid, elem in self.segments.items()
            if getattr(elem, "table_group_id", None) == group_id
            and elem.list_pos == victim_lp
        ]

        # Purge row cells
        for sid in doomed_cells:
            self.unregister_segment(sid)

        # Collect cells in subsequent rows
        downstream_cells = [
            (sid, elem) for sid, elem in self.segments.items()
            if getattr(elem, "table_group_id", None) == group_id
            and elem.list_pos > victim_lp
        ]

        # Shift row positions upward
        for sid, elem in downstream_cells:
            elem.update_position(list_pos=elem.list_pos - 1)

    def update_after_modification(self, segment_id: str, old_length: int, new_length: int) -> None:
        """Hook for post-edit adjustments (reserved for future char_pos tracking)

        Args:
            segment_id: Modified element identifier
            old_length: Original text byte count
            new_length: Updated text byte count
        """
        # Placeholder for future character-level position tracking
        pass

    # ==================================================================================
    # Legacy API Bridge (ContentModifier compatibility layer)
    # ==================================================================================

    def get_adjusted_position(self, segment_id: str) -> Optional[Tuple[int, int, int]]:
        """Legacy bridge → retrieve_adjusted_location"""
        return self.retrieve_adjusted_location(segment_id)

    def get_table_group_id(self, segment_id: str) -> Optional[int]:
        """Legacy bridge → retrieve_table_group_identifier"""
        return self.retrieve_table_group_identifier(segment_id)

    def get_representative_pos_for_table_group(self, table_group_id: int) -> Optional[Tuple[int, int, int]]:
        """Legacy bridge → retrieve_representative_location_for_table_group"""
        return self.retrieve_representative_location_for_table_group(table_group_id)

    def get_text(self, segment_id: str) -> Optional[str]:
        """Legacy bridge → retrieve_content"""
        return self.retrieve_content(segment_id)

    def get_block(self, segment_id: str) -> Optional[ContentSegment]:
        """Legacy bridge → retrieve_segment"""
        return self.retrieve_segment(segment_id)

    def get_last_content_block_id_in_same_cell(self, segment_id: str) -> Optional[str]:
        """Legacy bridge → retrieve_final_content_segment_in_cell"""
        return self.retrieve_final_content_segment_in_cell(segment_id)

    def get_cell_text(self, list_pos: int) -> str:
        """Legacy bridge → retrieve_cell_content"""
        return self.retrieve_cell_content(list_pos)

    def get_td_block_id_by_list_pos(self, list_pos: int) -> Optional[str]:
        """Legacy bridge → retrieve_cell_segment_by_list_position"""
        return self.retrieve_cell_segment_by_list_position(list_pos)

    def update_text(self, segment_id: str, new_text: str):
        """Legacy bridge → update_content"""
        self.update_content(segment_id, new_text)

    def update_cell_text_blocks(self, list_pos: int, new_text: str) -> None:
        """Legacy bridge → update_cell_content_segments"""
        self.update_cell_content_segments(list_pos, new_text)

    def update_after_para_pos_append(self, segment_id: str, inserted_count: int = 1):
        """Legacy bridge → update_after_paragraph_position_insertion"""
        self.update_after_paragraph_position_insertion(segment_id, inserted_count)

    def update_after_list_pos_append(self, segment_id: str, list_pos_added: int, update_start_list_pos: int):
        """Legacy bridge → update_after_list_position_insertion"""
        self.update_after_list_position_insertion(segment_id, list_pos_added, update_start_list_pos)

    def update_after_list_pos_deletion(self, segment_id: str, list_pos_deleted: int, deleted_start_list_pos: int):
        """Legacy bridge → update_after_list_position_removal"""
        self.update_after_list_position_removal(segment_id, list_pos_deleted, deleted_start_list_pos)

    def get_table_group_id_by_list_pos(self, list_pos: int) -> Optional[int]:
        """Legacy bridge → retrieve_table_group_by_list_position"""
        return self.retrieve_table_group_by_list_position(list_pos)

    def get_position(self, segment_id: str) -> Optional[Tuple[int, int, int]]:
        """Legacy bridge → retrieve_location"""
        return self.retrieve_location(segment_id)

    def get_td_block_id_for_block(self, segment_id: str) -> Optional[str]:
        """Legacy bridge → retrieve_cell_segment_identifier"""
        return self.retrieve_cell_segment_identifier(segment_id)

    def update_after_edit(self, segment_id: str, old_length: int, new_length: int) -> None:
        """Legacy bridge → update_after_modification"""
        self.update_after_modification(segment_id, old_length, new_length)

    def update_block(self, segment_id: str, len_delta: int = 0) -> None:
        """Legacy bridge → update_after_modification (delta-based signature)"""
        if len_delta == 0:
            return

        # Simplified API: convert delta to old/new lengths
        length_old = 0  # Delta-based API doesn't track original length
        length_new = abs(len_delta) if len_delta > 0 else 0
        self.update_after_modification(segment_id, length_old, length_new)

    def get_offset(self, segment_id: str) -> int:
        """Extract paragraph offset for 이전 버전 호환"""
        elem = self.retrieve_segment(segment_id)
        if elem is None:
            return 0
        return elem.para_pos

    # ==================================================================================
    # Utility Methods for Advanced Operations
    # ==================================================================================

    def count_segments_by_type(self, segment_type: str) -> int:
        """Count elements matching specified type"""
        counter = 0
        for elem in self.segments.values():
            elem_category = getattr(elem, "segment_type", "")
            if elem_category == segment_type:
                counter += 1
        return counter

    def get_all_segment_types(self) -> set:
        """Extract unique set of element types in registry"""
        type_collection = set()
        for elem in self.segments.values():
            elem_category = getattr(elem, "segment_type", "text")
            type_collection.add(elem_category)
        return type_collection

    def find_segments_in_range(
        self, start_pos: Tuple[int, int, int], end_pos: Tuple[int, int, int]
    ) -> list:
        """Locate all elements within spatial bounds"""
        matching_elems = []
        for sid, elem in self.segments.items():
            if start_pos <= elem.position <= end_pos:
                matching_elems.append((sid, elem))
        return matching_elems

    def get_table_group_statistics(self) -> Dict[int, Dict]:
        """Generate statistics for each table group"""
        group_stats = {}
        for group_id in set(self._table_group_id_to_rep_pos.keys()):
            cell_count = sum(
                1 for elem in self.segments.values()
                if getattr(elem, "table_group_id", None) == group_id
                and getattr(elem, "segment_type", "") == "td"
            )
            group_stats[group_id] = {
                "cell_count": cell_count,
                "representative_position": self._table_group_id_to_rep_pos.get(group_id),
            }
        return group_stats

    def validate_registry_integrity(self) -> Dict[str, any]:
        """Perform integrity checks on registry state"""
        issues = []

        # Check for orphaned annotations
        for anno_key, anno_data in self.annotations.items():
            anchor_id = anno_data.get("anchor_id")
            content_id = anno_data.get("content_id")
            if anchor_id not in self.segments:
                issues.append(f"Orphaned anchor: {anchor_id}")
            if content_id not in self.segments:
                issues.append(f"Orphaned content: {content_id}")

        # Check for position conflicts
        position_map = {}
        for sid, elem in self.segments.items():
            pos = elem.position
            if pos in position_map:
                issues.append(f"Position conflict: {pos} used by {sid} and {position_map[pos]}")
            position_map[pos] = sid

        return {
            "valid": len(issues) == 0,
            "issue_count": len(issues),
            "issues": issues[:10],  # Limit to first 10
            "total_segments": len(self.segments),
        }

    def export_spatial_map(self) -> Dict[Tuple[int, int, int], str]:
        """Generate reverse mapping from position to segment ID"""
        spatial_index = {}
        for sid, elem in self.segments.items():
            spatial_index[elem.position] = sid
        return spatial_index

    def get_segments_at_list_position(self, list_pos: int) -> list:
        """Retrieve all elements at specified list coordinate"""
        matches = []
        for sid, elem in self.segments.items():
            if elem.list_pos == list_pos:
                matches.append((sid, elem))
        # Sort by paragraph position
        matches.sort(key=lambda pair: pair[1].para_pos)
        return matches

    def calculate_insertion_delta_total(self, list_pos: int) -> int:
        """Compute aggregate insertion count for list position"""
        if list_pos not in self.insertion_tracker:
            return 0
        return sum(self.insertion_tracker[list_pos].values())

    def clear_insertion_tracking(self, list_pos: Optional[int] = None):
        """Reset insertion tracker for specified or all positions"""
        if list_pos is None:
            self.insertion_tracker.clear()
        elif list_pos in self.insertion_tracker:
            del self.insertion_tracker[list_pos]


