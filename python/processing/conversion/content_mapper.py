"""
문서 요소 위치 매핑 및 추적 시스템

DocumentView HTML과 HWP 문서 내부 위치를 매핑하고 실시간으로 추적합니다.
"""

import json
import re
from operator import itemgetter
from typing import Callable, Dict, Optional, Tuple


class DocumentElement:
    """개별 문서 요소 정보를 저장하는 클래스"""

    def __init__(
        self,
        element_id: str,
        coordinates: Tuple[int, int, int],
        content: str = "",
        element_type: str = "text",
        table_cluster_id: Optional[int] = None,
    ):
        self.id = element_id
        self.section_idx = coordinates[0]  # list_pos → section_idx
        self.para_idx = coordinates[1]  # para_pos → para_idx
        self.char_idx = coordinates[2]  # char_pos → char_idx
        self.content = content  # text → content
        self.element_type = element_type  # block_type → element_type
        # 'text', 'list', 'td', 'footnote_anchor', 'footnote_content'
        self.table_cluster_id = table_cluster_id  # table_group_id → table_cluster_id

    @property
    def coordinates(self) -> Tuple[int, int, int]:
        """위치 좌표 반환 (position → coordinates)"""
        return (self.section_idx, self.para_idx, self.char_idx)

    def adjust_coordinates(
        self, section_idx: int = None, para_idx: int = None, char_idx: int = None
    ):
        """좌표 정보 조정 (update_position → adjust_coordinates)"""
        if section_idx is not None:
            self.section_idx = section_idx
        if para_idx is not None:
            self.para_idx = para_idx
        if char_idx is not None:
            self.char_idx = char_idx

    def __repr__(self):
        return f"DocumentElement(id={self.id}, type={self.element_type}, coordinates={self.coordinates}, cluster={self.table_cluster_id}, content={self.content[:30]}...)"


class FootnoteElement(DocumentElement):
    """각주 요소 정보를 저장하는 클래스"""

    def __init__(
        self,
        element_id: str,
        coordinates: Tuple[int, int, int],
        content: str = "",
        footnote_num: int = None,
        anchor_id: str = None,
        content_id: str = None,
    ):
        super().__init__(element_id, coordinates, content, element_type="footnote")
        self.footnote_num = footnote_num
        self.anchor_id = anchor_id
        self.content_id = content_id

    def __repr__(self):
        return f"FootnoteElement(id={self.id}, num={self.footnote_num}, anchor={self.anchor_id}, content={self.content_id}, coordinates={self.coordinates})"


class LocationTrackingSystem:
    """문서 요소 위치를 실시간으로 추적하고 관리하는 클래스 (PositionTracker → LocationTrackingSystem)"""

    def __init__(
        self,
        extracted_data: Tuple[str, Dict[int, Tuple[int, int, int]]],
        log_callback: Callable[[str, Optional[str]], None],
    ):
        text_format, id_to_coordinates = extracted_data  # hdml → text_format
        self.log_callback = (
            log_callback if log_callback else (lambda message, level: None)
        )
        self.elements: Dict[str, DocumentElement] = {}  # blocks → elements
        self.shift_registry: Dict[
            int, Dict[int, int]
        ] = {}  # insertion_tracker → shift_registry

        # 각주 관리
        self.footnotes: Dict[str, Dict] = {}
        self.footnote_anchors: Dict[str, str] = {}
        self.footnote_contents: Dict[str, str] = {}

        # section_idx → table_cluster_id 매핑
        self._section_to_cluster: Dict[int, int] = {}
        # table_cluster_id → 대표 coordinates 매핑
        self._cluster_to_rep_coordinates: Dict[int, Tuple[int, int, int]] = {}

        self.initialize_from_text(
            text_format, id_to_coordinates
        )  # initialize_from_hdml → initialize_from_text

    def initialize_from_text(self, text_format: str, id_to_coordinates: Dict):
        """텍스트 형식 데이터로 초기화"""

        # id_to_coordinates 키를 문자열로 변환
        str_id_to_coordinates = {}
        for k, v in id_to_coordinates.items():
            str_id_to_coordinates[str(k)] = v

        # 일반 텍스트 요소 파싱
        text_pattern = r"<([0-9]+)>(.*)"
        for line in text_format.split("\n"):
            match = re.search(text_pattern, line)
            if match:
                element_id = match.group(1)
                text = match.group(2).rstrip()

                # HTML 태그 제거
                if text.endswith("</td>"):
                    text = text[:-5].rstrip()
                elif text.endswith("</list>"):
                    text = text[:-10].rstrip()

                if element_id in str_id_to_coordinates:
                    coordinates = str_id_to_coordinates[element_id]
                    if element_id not in self.elements:
                        self.elements[element_id] = DocumentElement(
                            element_id,
                            tuple(coordinates),
                            text if text else "",
                            element_type="text",
                        )

        # 리스트 노드 파싱
        list_pattern = r"<list[^>]*>\s*<([0-9]+)>(.*)"
        for line in text_format.split("\n"):
            match = re.search(list_pattern, line)
            if match:
                node_id = match.group(1)
                text = match.group(2).rstrip()

                if node_id in str_id_to_coords:
                    coords = str_id_to_coords[node_id]
                    if node_id not in self.nodes:
                        self.nodes[node_id] = ContentNode(
                            node_id,
                            tuple(coords),
                            text if text else "",
                            node_category="list",
                        )
                    else:
                        # 기존 노드를 리스트로 승격
                        try:
                            if (
                                getattr(self.nodes[node_id], "node_category", "text")
                                != "list"
                            ):
                                self.nodes[node_id].node_category = "list"
                        except Exception:
                            pass

        # td 태그 노드 파싱
        td_tag_pattern = r"<td\s+([0-9]+)(?:\s+[^>]*)?>"
        for line in text_format.split("\n"):
            match = re.search(td_tag_pattern, line)
            if match:
                td_id = match.group(1)
                if td_id in str_id_to_coords:
                    td_coords = str_id_to_coords[td_id]
                    if td_id not in self.nodes:
                        first_text = ""
                        self.nodes[td_id] = ContentNode(
                            td_id,
                            tuple(td_coords),
                            first_text,
                            node_category="td",
                        )

        # 각주 노드 파싱
        footnote_pattern = r"^<각주([0-9]+)\s+([0-9]+)><([0-9]+)>\s*(.*)"
        for line in text_format.split("\n"):
            match = re.match(footnote_pattern, line)
            if match:
                footnote_num = int(match.group(1))
                anchor_id = match.group(2)
                content_id = match.group(3)
                text = match.group(4).rstrip()

                # 각주 앵커 노드
                if anchor_id in str_id_to_coords:
                    anchor_coords = str_id_to_coords[anchor_id]
                    anchor_node = FootnoteNode(
                        anchor_id,
                        tuple(anchor_coords),
                        f"[각주{footnote_num}]",
                        footnote_num=footnote_num,
                        anchor_id=anchor_id,
                        content_id=content_id,
                    )
                    anchor_node.node_category = "footnote_anchor"
                    self.nodes[anchor_id] = anchor_node

                # 각주 내용 노드
                if content_id in str_id_to_coords:
                    content_coords = str_id_to_coords[content_id]
                    content_node = FootnoteNode(
                        content_id,
                        tuple(content_coords),
                        text,
                        footnote_num=footnote_num,
                        anchor_id=anchor_id,
                        content_id=content_id,
                    )
                    content_node.node_category = "footnote_content"
                    self.nodes[content_id] = content_node

                # 각주 관리 데이터 구조 업데이트
                footnote_key = str(footnote_num)
                self.footnotes[footnote_key] = {
                    "anchor_id": anchor_id,
                    "content_id": content_id,
                    "text": text,
                    "footnote_num": footnote_num,
                }
                self.footnote_anchors[anchor_id] = footnote_key
                self.footnote_contents[content_id] = footnote_key

        # textbox 노드 파싱
        textbox_pattern = r"<textbox\s+([0-9]+)[^>]*>"
        for line in text_format.split("\n"):
            match = re.search(textbox_pattern, line)
            if match:
                textbox_id = match.group(1)
                if textbox_id in str_id_to_coords:
                    coords = str_id_to_coords[textbox_id]
                    if textbox_id in self.nodes:
                        try:
                            self.nodes[textbox_id].node_category = "textbox"
                        except Exception:
                            pass
                    else:
                        self.nodes[textbox_id] = ContentNode(
                            textbox_id,
                            tuple(coords),
                            "",
                            node_category="textbox",
                        )

        # 누락된 ID 토큰 보강
        try:
            all_token_ids = set(re.findall(r"<([0-9]+)>", text_format))
            for token_id in all_token_ids:
                if token_id in str_id_to_coords and token_id not in self.nodes:
                    coords = str_id_to_coords[token_id]
                    self.nodes[token_id] = ContentNode(
                        token_id,
                        tuple(coords),
                        "",
                        node_category="text",
                    )
        except Exception:
            pass

        # 테이블 클러스터 계산 (<table> 태그 기준으로 그룹 분할)
        try:
            td_id_to_cluster: Dict[str, int] = {}
            current_cluster = 0
            in_table = False

            for line in text_format.split("\n"):
                if re.search(r"^\s*<table>", line):
                    current_cluster += 1
                    in_table = True

                if in_table:
                    td_matches = re.findall(r"<td\s+([0-9]+)", line)
                    for td_id in td_matches:
                        td_id_to_cluster[td_id] = current_cluster

                if "</table>" in line:
                    in_table = False

            # td 노드에 table_cluster_id 할당
            for node in self.nodes.values():
                if getattr(node, "node_category", "") != "td":
                    continue
                cluster = td_id_to_cluster.get(node.id)
                if cluster is not None:
                    if (
                        node.section_idx
                        not in self._section_to_cluster
                    ):
                        self._section_to_cluster[node.section_idx] = cluster
                    node.table_cluster_id = cluster
                    rep = self._cluster_to_rep_coords.get(cluster)
                    if rep is None:
                        self._cluster_to_rep_coords[cluster] = node.coords
                    else:
                        if (node.section_idx, node.para_idx, node.char_idx) < (
                            rep[0],
                            rep[1],
                            rep[2],
                        ):
                            self._cluster_to_rep_coords[cluster] = node.coords

            # 다른 노드들에도 표 그룹 부여
            for node in self.nodes.values():
                if node.section_idx <= 2:
                    continue
                if getattr(node, "node_category", "") in (
                    "footnote_anchor",
                    "footnote_content",
                    "td",
                ):
                    continue
                cluster = self._section_to_cluster.get(node.section_idx)
                if cluster is not None:
                    node.table_cluster_id = cluster
                    rep = self._cluster_to_rep_coords.get(cluster)
                    if rep is None:
                        self._cluster_to_rep_coords[cluster] = node.coords
                    else:
                        if (node.section_idx, node.para_idx, node.char_idx) < (
                            rep[0],
                            rep[1],
                            rep[2],
                        ):
                            self._cluster_to_rep_coords[cluster] = node.coords
        except Exception as e:
            self.log_callback(f"[ERROR] 테이블 클러스터 계산 실패: {e}", "ERROR")

    def get_node(self, node_id: str) -> Optional[ContentNode]:
        """노드 ID로 노드 정보 가져오기 (get_block → get_node)"""
        return self.nodes.get(str(node_id))

    def get_coords(self, node_id: str) -> Optional[Tuple[int, int, int]]:
        """노드 ID로 좌표 정보 가져오기 (get_position → get_coords)"""
        node = self.get_node(node_id)
        return node.coords if node else None

    def get_content(self, node_id: str) -> Optional[str]:
        """노드 ID로 내용 가져오기 (get_text → get_content)"""
        node = self.get_node(node_id)
        return node.content if node else None

    def get_cell_content(self, section_idx: int) -> str:
        """section_idx로 셀 내 모든 콘텐츠 노드 텍스트 가져오기 (get_cell_text → get_cell_content)"""
        candidates = []
        for node in self.nodes.values():
            if node.section_idx != section_idx:
                continue
            category = getattr(node, "node_category", "text")
            if category in ("text", "list"):
                candidates.append((node.para_idx, node.content or ""))

        if not candidates:
            return ""

        candidates.sort(key=itemgetter(0))
        return "\n".join(text for _, text in candidates)

    def get_last_content_node_in_cell(self, node_id: str) -> Optional[str]:
        """같은 셀에서 마지막 콘텐츠 노드 ID 반환 (get_last_content_block_id_in_same_cell → get_last_content_node_in_cell)"""
        base = self.get_node(str(node_id))
        if not base:
            return None

        base_section = base.section_idx

        candidates = []
        for nid, node in self.nodes.items():
            if node.section_idx != base_section:
                continue
            category = getattr(node, "node_category", "text")
            if category in ("text", "list"):
                candidates.append((node.para_idx, nid))

        if not candidates:
            return None

        candidates.sort(key=itemgetter(0), reverse=False)
        return candidates[-1][1]

    def get_cluster_id(self, node_id: str) -> Optional[int]:
        """노드가 속한 테이블 클러스터 ID 반환 (get_table_group_id → get_cluster_id)"""
        node = self.get_node(node_id)
        if not node:
            return None
        return getattr(node, "table_cluster_id", None)

    def get_cluster_id_by_section(self, section_idx: int) -> Optional[int]:
        """section_idx로 테이블 클러스터 ID 조회 (get_table_group_id_by_list_pos → get_cluster_id_by_section)"""
        return self._section_to_cluster.get(section_idx)

    def get_rep_coords_for_cluster(
        self, cluster_id: int
    ) -> Optional[Tuple[int, int, int]]:
        """클러스터의 대표 좌표 반환 (get_representative_pos_for_table_group → get_rep_coords_for_cluster)"""
        return self._cluster_to_rep_coords.get(cluster_id)

    def get_shifted_coords(self, node_id: str) -> Optional[Tuple[int, int, int]]:
        """시프트 추적을 반영한 조정된 좌표 반환 (get_adjusted_position → get_shifted_coords)"""
        node = self.get_node(node_id)
        if not node:
            return None

        shifted_coords = list(node.coords)
        section_idx = node.section_idx
        para_idx = node.para_idx

        if section_idx in self.shift_registry:
            for inserted_para, count in self.shift_registry[section_idx].items():
                if inserted_para <= para_idx:
                    shifted_coords[1] += count

        return tuple(shifted_coords)

    def update_content(self, node_id: str, new_content: str):
        """노드의 내용 업데이트 (update_text → update_content)"""
        node = self.get_node(node_id)
        if node:
            node.content = new_content

    def update_coords(self, node_id: str, new_coords: Tuple[int, int, int]):
        """노드의 좌표 업데이트 (update_position → update_coords)"""
        node = self.get_node(node_id)
        if node:
            node.adjust_coords(new_coords[0], new_coords[1], new_coords[2])

    def add_node(
        self, node_id: str, coords: Tuple[int, int, int], content: str = ""
    ):
        """새 노드 추가 (add_block → add_node)"""
        self.nodes[node_id] = ContentNode(node_id, coords, content)

    def remove_node(self, node_id: str):
        """노드 제거 (remove_block → remove_node)"""
        if node_id in self.nodes:
            del self.nodes[node_id]

    def update_after_para_append(self, node_id: str, inserted_count: int = 1):
        """문단 삽입 후 위치 정보 업데이트 (update_after_para_pos_append → update_after_para_append)"""
        node = self.get_node(node_id)
        if not node:
            return

        section_idx = node.section_idx
        para_idx = node.para_idx

        if section_idx not in self.shift_registry:
            self.shift_registry[section_idx] = {}

        if para_idx not in self.shift_registry[section_idx]:
            self.shift_registry[section_idx][para_idx] = 0
        self.shift_registry[section_idx][para_idx] += inserted_count

        # 같은 섹션에서 para_idx가 더 큰 노드들은 자동으로 get_shifted_coords에서 조정됨

    def update_after_section_append(
        self, node_id: str, sections_added: int, update_start_section: int
    ):
        """섹션 추가 후 위치 정보 업데이트 (update_after_list_pos_append → update_after_section_append)"""
        nodes_to_update = []

        for nid, node in self.nodes.items():
            if node.section_idx > 2 and node.section_idx >= update_start_section:
                nodes_to_update.append((nid, node))

        for nid, node in nodes_to_update:
            node.adjust_coords(section_idx=node.section_idx + sections_added)

        # shift_registry도 업데이트
        new_shift_registry = {}
        for section_idx, insertions in self.shift_registry.items():
            if section_idx > 0 and section_idx >= update_start_section:
                new_section_idx = section_idx + sections_added
                new_shift_registry[new_section_idx] = insertions
            else:
                new_shift_registry[section_idx] = insertions

        self.shift_registry = new_shift_registry

    def update_after_section_deletion(
        self, node_id: str, sections_deleted: int, deleted_start_section: int
    ):
        """섹션 삭제 후 위치 정보 업데이트 (update_after_list_pos_deletion → update_after_section_deletion)"""
        nodes_to_update = []

        for nid, node in self.nodes.items():
            if node.section_idx >= deleted_start_section + sections_deleted:
                nodes_to_update.append((nid, node))

        for nid, node in nodes_to_update:
            node.adjust_coords(section_idx=node.section_idx - sections_deleted)

        # shift_registry도 업데이트
        new_shift_registry = {}
        for section_idx, insertions in self.shift_registry.items():
            if section_idx >= deleted_start_section + sections_deleted:
                new_section_idx = section_idx - sections_deleted
                new_shift_registry[new_section_idx] = insertions
            elif section_idx < deleted_start_section:
                new_shift_registry[section_idx] = insertions

        self.shift_registry = new_shift_registry

    def get_current_mapping(self) -> Dict[str, Dict]:
        """현재 모든 노드의 매핑 정보 반환"""
        result = {}
        for node_id, node in self.nodes.items():
            result[node_id] = {
                "coords": node.coords,
                "shifted_coords": self.get_shifted_coords(node_id),
                "content": node.content,
            }
        return result

    def export_to_json(self, filepath: str):
        """현재 매핑 정보를 JSON 파일로 저장"""
        mapping = self.get_current_mapping()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)

    def debug_print(self):
        """디버깅을 위한 현재 상태 출력"""
        self.log_callback("\n" + "=" * 60)
        self.log_callback("[STATS] PositionTracker 현재 상태")
        self.log_callback("=" * 60)
        self.log_callback(f"총 노드 수: {len(self.nodes)}")
        self.log_callback("시프트 추적 상세:")
        for section_idx, insertions in self.shift_registry.items():
            self.log_callback(f"  Section {section_idx}: {insertions}")
            total = sum(insertions.values())
            self.log_callback(f"    → 총 {total}개 문단 삽입됨")
        self.log_callback("\n최근 5개 노드:")
        for i, (node_id, node) in enumerate(list(self.nodes.items())[:5]):
            self.log_callback(
                f"  [{node_id}] coords={node.coords}, content={node.content[:30]}..."
            )
        self.log_callback("=" * 60 + "\n")

    # 각주 관련 헬퍼 메서드들
    def get_footnote_by_number(self, footnote_num: int) -> Optional[Dict]:
        """각주 번호로 각주 정보 가져오기"""
        return self.footnotes.get(str(footnote_num))

    def get_footnote_by_anchor_id(self, anchor_id: str) -> Optional[Dict]:
        """앵커 노드 ID로 각주 정보 가져오기"""
        footnote_num = self.footnote_anchors.get(str(anchor_id))
        if footnote_num:
            return self.footnotes.get(footnote_num)
        return None

    def get_footnote_by_content_id(self, content_id: str) -> Optional[Dict]:
        """내용 노드 ID로 각주 정보 가져오기"""
        footnote_num = self.footnote_contents.get(str(content_id))
        if footnote_num:
            return self.footnotes.get(footnote_num)
        return None

    def get_all_footnotes(self) -> Dict[str, Dict]:
        """모든 각주 정보 반환"""
        return self.footnotes.copy()

    def is_footnote_anchor(self, node_id: str) -> bool:
        """노드가 각주 앵커인지 확인"""
        node = self.get_node(str(node_id))
        return (
            node
            and hasattr(node, "node_category")
            and node.node_category == "footnote_anchor"
        )

    def is_footnote_content(self, node_id: str) -> bool:
        """노드가 각주 내용인지 확인"""
        node = self.get_node(str(node_id))
        return (
            node
            and hasattr(node, "node_category")
            and node.node_category == "footnote_content"
        )
