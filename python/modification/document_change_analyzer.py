"""
DocumentDiffAnalyzer - 문서 차이점 분석 및 변경 추적 시스템

문서 간 차이점을 분석하고 변경 내용을 추적하는 고급 분석 시스템입니다.
XML 기반 파싱, 패턴 매칭, 변경 유형 분류 등의 기능을 제공합니다.

주요 특징:
- 다중 형식 지원 (HWPX, XML, 텍스트)
- 패턴 기반 변경 감지
- 변경 유형 분류 및 통계
- 변경 영향도 분석
- 실시간 변경 모니터링
"""

import os
import re
import json
import zipfile
import tempfile
import hashlib
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ChangeType(Enum):
    """변경 유형 열거형"""
    INSERTION = "insertion"
    DELETION = "deletion"
    MODIFICATION = "modification"
    MOVED = "moved"
    REORGANIZED = "reorganized"


class ChangeSeverity(Enum):
    """변경 중요도 열거형"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ChangeEvent:
    """개별 변경 이벤트"""
    change_type: ChangeType
    location: str  # XML 경로 또는 문서 내 위치
    content_before: Optional[str] = None
    content_after: Optional[str] = None
    line_number: Optional[int] = None
    severity: ChangeSeverity = ChangeSeverity.MEDIUM
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class DocumentAnalysisReport:
    """문서 분석 보고서"""
    document_path: str
    analysis_timestamp: float
    total_changes: int
    change_summary: Dict[ChangeType, int]
    severity_distribution: Dict[ChangeSeverity, int]
    change_events: List[ChangeEvent]
    processing_time_seconds: float
    file_metadata: Dict[str, Any] = field(default_factory=dict)


class DocumentDiffAnalyzer:
    """고급 문서 차이점 분석기

    문서의 변경 내용을 다양한 방식으로 분석하고 보고서를 생성합니다.
    """

    def __init__(self, logger: Optional[callable] = None):
        """DocumentDiffAnalyzer 초기화

        Args:
            logger: 로깅 콜백 함수
        """
        self.logger = logger or (lambda level, message: print(f"[{level}] {message}"))
        self._change_patterns = self._initialize_change_patterns()
        self._analysis_cache: Dict[str, DocumentAnalysisReport] = {}

    def _initialize_change_patterns(self) -> Dict[str, List[str]]:
        """변경 감지 패턴 초기화

        Returns:
            Dict[str, List[str]]: 변경 유형별 패턴 리스트
        """
        return {
            'insertion': [
                r'<ins\b[^>]*>',
                r'inserted="[^"]*"',
                r'\binsert\b\s*=',
                r'\binsertBegin\b',
                r'<addition\b',
                r'<new[^>]*>'
            ],
            'deletion': [
                r'<del\b[^>]*>',
                r'deleted="[^"]*"',
                r'\bdelete\b\s*=',
                r'\bdeleteBegin\b',
                r'<removal\b',
                r'<removed\b'
            ],
            'modification': [
                r'<modified\b',
                r'<change[^>]*>',
                r'<update\b',
                r'<revised\b'
            ],
            'tracking': [
                r'<trackChange\b',
                r'track-change',
                r'changeId="[^"]*"',
                r'<history\b',
                r'<revision[^>]*>'
            ]
        }

    def analyze_document_changes(self, document_path: str, reference_path: Optional[str] = None) -> DocumentAnalysisReport:
        """문서 변경 사항 분석

        Args:
            document_path: 분석할 문서 경로
            reference_path: 기준 문서 경로 (선택사항)

        Returns:
            DocumentAnalysisReport: 분석 보고서
        """
        start_time = time.time()

        self.logger("INFO", f"문서 분석 시작: {document_path}")

        try:
            # 캐시 확인
            cache_key = self._generate_cache_key(document_path, reference_path)
            if cache_key in self._analysis_cache:
                self.logger("INFO", "캐시된 분석 결과 사용")
                return self._analysis_cache[cache_key]

            # 문서 로드 및 전처리
            document_data = self._load_document_data(document_path)
            reference_data = self._load_document_data(reference_path) if reference_path else None

            # 변경 감지 및 분석
            change_events = self._detect_changes(document_data, reference_data, document_path)

            # 보고서 생성
            report = self._generate_analysis_report(
                document_path=document_path,
                change_events=change_events,
                processing_time=time.time() - start_time,
                file_metadata=self._extract_file_metadata(document_path)
            )

            # 캐시에 저장
            self._analysis_cache[cache_key] = report

            self.logger("INFO", f"문서 분석 완료: {len(change_events)}개 변경 사항 감지")
            return report

        except Exception as e:
            self.logger("ERROR", f"문서 분석 실패: {e}")
            raise

    def _load_document_data(self, file_path: Optional[str]) -> Optional[Dict[str, Any]]:
        """문서 데이터 로드

        Args:
            file_path: 파일 경로

        Returns:
            Optional[Dict[str, Any]]: 로드된 문서 데이터
        """
        if not file_path or not os.path.exists(file_path):
            return None

        try:
            file_ext = Path(file_path).suffix.lower()

            if file_ext == '.hwpx':
                return self._parse_hwpx_file(file_path)
            elif file_ext == '.xml':
                return self._parse_xml_file(file_path)
            elif file_ext in ['.txt', '.md']:
                return self._parse_text_file(file_path)
            else:
                self.logger("WARNING", f"지원하지 않는 파일 형식: {file_ext}")
                return None

        except Exception as e:
            self.logger("ERROR", f"문서 로드 실패 ({file_path}): {e}")
            return None

    def _parse_hwpx_file(self, hwpx_path: str) -> Dict[str, Any]:
        """HWPX 파일 파싱

        Args:
            hwpx_path: HWPX 파일 경로

        Returns:
            Dict[str, Any]: 파싱된 데이터
        """
        self.logger("DEBUG", f"HWPX 파일 파싱: {hwpx_path}")

        parsed_data = {
            'type': 'hwpx',
            'xml_files': {},
            'metadata': {},
            'text_content': []
        }

        try:
            with zipfile.ZipFile(hwpx_path, 'r') as zf:
                file_list = zf.namelist()
                xml_files = [f for f in file_list if f.lower().endswith('.xml')]

                for xml_file in xml_files:
                    try:
                        with zf.open(xml_file) as f:
                            content = f.read().decode('utf-8', errors='ignore')
                            parsed_data['xml_files'][xml_file] = content
                    except Exception as e:
                        self.logger("WARNING", f"XML 파일 파싱 실패 ({xml_file}): {e}")

                # 메타데이터 추출
                if 'DocInfo.xml' in file_list:
                    try:
                        with zf.open('DocInfo.xml') as f:
                            doc_info = f.read().decode('utf-8', errors='ignore')
                            parsed_data['metadata'] = self._extract_metadata_from_xml(doc_info)
                    except Exception as e:
                        self.logger("WARNING", f"메타데이터 추출 실패: {e}")

        except Exception as e:
            self.logger("ERROR", f"HWPX 파싱 실패: {e}")
            raise

        return parsed_data

    def _parse_xml_file(self, xml_path: str) -> Dict[str, Any]:
        """XML 파일 파싱

        Args:
            xml_path: XML 파일 경로

        Returns:
            Dict[str, Any]: 파싱된 데이터
        """
        self.logger("DEBUG", f"XML 파일 파싱: {xml_path}")

        try:
            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()

            return {
                'type': 'xml',
                'content': content,
                'metadata': self._extract_metadata_from_xml(content)
            }
        except Exception as e:
            self.logger("ERROR", f"XML 파싱 실패: {e}")
            raise

    def _parse_text_file(self, text_path: str) -> Dict[str, Any]:
        """텍스트 파일 파싱

        Args:
            text_path: 텍스트 파일 경로

        Returns:
            Dict[str, Any]: 파싱된 데이터
        """
        self.logger("DEBUG", f"텍스트 파일 파싱: {text_path}")

        try:
            with open(text_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            return {
                'type': 'text',
                'lines': lines,
                'line_count': len(lines),
                'content': ''.join(lines)
            }
        except Exception as e:
            self.logger("ERROR", f"텍스트 파싱 실패: {e}")
            raise

    def _extract_metadata_from_xml(self, xml_content: str) -> Dict[str, Any]:
        """XML에서 메타데이터 추출

        Args:
            xml_content: XML 콘텐츠

        Returns:
            Dict[str, Any]: 추출된 메타데이터
        """
        metadata = {}

        # 간단한 메타데이터 패턴 매칭
        patterns = {
            'title': r'<title[^>]*>(.*?)</title>',
            'author': r'<author[^>]*>(.*?)</author>',
            'created': r'<created[^>]*>(.*?)</created>',
            'modified': r'<modified[^>]*>(.*?)</modified>',
            'version': r'<version[^>]*>(.*?)</version>'
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, xml_content, re.IGNORECASE | re.DOTALL)
            if match:
                metadata[key] = match.group(1).strip()

        return metadata

    def _detect_changes(self, current_data: Dict[str, Any], reference_data: Optional[Dict[str, Any]], document_path: str) -> List[ChangeEvent]:
        """변경 사항 감지

        Args:
            current_data: 현재 문서 데이터
            reference_data: 기준 문서 데이터
            document_path: 문서 경로

        Returns:
            List[ChangeEvent]: 감지된 변경 이벤트 목록
        """
        change_events = []

        try:
            if reference_data is None:
                # 기준 문서가 없는 경우, 현재 문서의 추적 가능한 변경 사항만 감지
                change_events.extend(self._detect_tracked_changes(current_data))
            else:
                # 기준 문서와 비교
                change_events.extend(self._compare_documents(current_data, reference_data))

        except Exception as e:
            self.logger("ERROR", f"변경 감지 실패: {e}")

        return change_events

    def _detect_tracked_changes(self, document_data: Dict[str, Any]) -> List[ChangeEvent]:
        """추적된 변경 사항 감지

        Args:
            document_data: 문서 데이터

        Returns:
            List[ChangeEvent]: 변경 이벤트 목록
        """
        change_events = []

        if document_data.get('type') == 'hwpx':
            # HWPX 파일의 XML에서 변경 태그 검색
            for xml_file, content in document_data.get('xml_files', {}).items():
                change_events.extend(self._scan_xml_for_changes(content, xml_file))

        elif document_data.get('type') == 'xml':
            # XML 파일 직접 스캔
            change_events.extend(self._scan_xml_for_changes(document_data['content'], document_data.get('path', 'unknown')))

        return change_events

    def _scan_xml_for_changes(self, xml_content: str, file_path: str) -> List[ChangeEvent]:
        """XML에서 변경 태그 스캔

        Args:
            xml_content: XML 콘텐츠
            file_path: 파일 경로

        Returns:
            List[ChangeEvent]: 변경 이벤트 목록
        """
        change_events = []

        try:
            # 변경 유형별 패턴 매칭
            for change_type, patterns in self._change_patterns.items():
                for pattern in patterns:
                    matches = re.finditer(pattern, xml_content, re.IGNORECASE)
                    for match in matches:
                        # 라인 번호 계산
                        line_number = xml_content[:match.start()].count('\n') + 1

                        change_event = ChangeEvent(
                            change_type=ChangeType(change_type),
                            location=f"{file_path}:{line_number}",
                            content_before=None,  # XML에서는 이전 내용 추출이 어려움
                            content_after=match.group(0),
                            line_number=line_number,
                            severity=self._determine_change_severity(change_type),
                            metadata={
                                'pattern': pattern,
                                'match_start': match.start(),
                                'match_end': match.end()
                            }
                        )

                        change_events.append(change_event)

        except Exception as e:
            self.logger("ERROR", f"XML 스캔 실패 ({file_path}): {e}")

        return change_events

    def _compare_documents(self, current_data: Dict[str, Any], reference_data: Dict[str, Any]) -> List[ChangeEvent]:
        """두 문서 비교

        Args:
            current_data: 현재 문서 데이터
            reference_data: 기준 문서 데이터

        Returns:
            List[ChangeEvent]: 변경 이벤트 목록
        """
        # 단순한 텍스트 기반 비교 (향후 확장 가능)
        change_events = []

        try:
            current_text = self._extract_text_content(current_data)
            reference_text = self._extract_text_content(reference_data)

            if current_text == reference_text:
                return change_events

            # 간단한 차이점 계산 (실제 구현에서는 더 sophisticated한 알고리즘 사용)
            current_lines = current_text.split('\n')
            reference_lines = reference_text.split('\n')

            max_lines = max(len(current_lines), len(reference_lines))

            for i in range(max_lines):
                current_line = current_lines[i] if i < len(current_lines) else ""
                reference_line = reference_lines[i] if i < len(reference_lines) else ""

                if current_line != reference_line:
                    if i >= len(reference_lines):
                        # 삽입
                        change_events.append(ChangeEvent(
                            change_type=ChangeType.INSERTION,
                            location=f"line:{i+1}",
                            content_before=None,
                            content_after=current_line,
                            line_number=i+1,
                            severity=ChangeSeverity.MEDIUM
                        ))
                    elif i >= len(current_lines):
                        # 삭제
                        change_events.append(ChangeEvent(
                            change_type=ChangeType.DELETION,
                            location=f"line:{i+1}",
                            content_before=reference_line,
                            content_after=None,
                            line_number=i+1,
                            severity=ChangeSeverity.MEDIUM
                        ))
                    else:
                        # 수정
                        change_events.append(ChangeEvent(
                            change_type=ChangeType.MODIFICATION,
                            location=f"line:{i+1}",
                            content_before=reference_line,
                            content_after=current_line,
                            line_number=i+1,
                            severity=ChangeSeverity.HIGH
                        ))

        except Exception as e:
            self.logger("ERROR", f"문서 비교 실패: {e}")

        return change_events

    def _extract_text_content(self, document_data: Dict[str, Any]) -> str:
        """문서에서 텍스트 콘텐츠 추출

        Args:
            document_data: 문서 데이터

        Returns:
            str: 텍스트 콘텐츠
        """
        if document_data.get('type') == 'text':
            return document_data.get('content', '')

        elif document_data.get('type') == 'xml':
            # XML 태그 제거 (단순화)
            content = document_data.get('content', '')
            return re.sub(r'<[^>]+>', '', content)

        elif document_data.get('type') == 'hwpx':
            # HWPX에서 모든 XML의 텍스트 추출
            text_parts = []
            for xml_content in document_data.get('xml_files', {}).values():
                # XML 태그 제거
                clean_text = re.sub(r'<[^>]+>', '', xml_content)
                text_parts.append(clean_text)
            return '\n'.join(text_parts)

        return ''

    def _determine_change_severity(self, change_type: str) -> ChangeSeverity:
        """변경 중요도 결정

        Args:
            change_type: 변경 유형

        Returns:
            ChangeSeverity: 중요도
        """
        severity_map = {
            'insertion': ChangeSeverity.MEDIUM,
            'deletion': ChangeSeverity.HIGH,  # 삭제는 보통 더 중요
            'modification': ChangeSeverity.HIGH,
            'tracking': ChangeSeverity.LOW
        }

        return severity_map.get(change_type, ChangeSeverity.MEDIUM)

    def _extract_file_metadata(self, file_path: str) -> Dict[str, Any]:
        """파일 메타데이터 추출

        Args:
            file_path: 파일 경로

        Returns:
            Dict[str, Any]: 메타데이터
        """
        try:
            stat = os.stat(file_path)
            return {
                'file_size': stat.st_size,
                'modified_time': stat.st_mtime,
                'created_time': stat.st_ctime,
                'file_hash': self._calculate_file_hash(file_path)
            }
        except Exception as e:
            self.logger("WARNING", f"메타데이터 추출 실패 ({file_path}): {e}")
            return {}

    def _calculate_file_hash(self, file_path: str) -> str:
        """파일 해시 계산

        Args:
            file_path: 파일 경로

        Returns:
            str: SHA256 해시
        """
        try:
            hasher = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _generate_cache_key(self, document_path: str, reference_path: Optional[str]) -> str:
        """캐시 키 생성

        Args:
            document_path: 문서 경로
            reference_path: 기준 문서 경로

        Returns:
            str: 캐시 키
        """
        key_data = f"{document_path}:{reference_path or 'none'}"
        return hashlib.md5(key_data.encode()).hexdigest()

    def _generate_analysis_report(self, document_path: str, change_events: List[ChangeEvent], processing_time: float, file_metadata: Dict[str, Any]) -> DocumentAnalysisReport:
        """분석 보고서 생성

        Args:
            document_path: 문서 경로
            change_events: 변경 이벤트 목록
            processing_time: 처리 시간
            file_metadata: 파일 메타데이터

        Returns:
            DocumentAnalysisReport: 분석 보고서
        """
        # 변경 유형별 요약
        change_summary = {}
        for change_type in ChangeType:
            change_summary[change_type] = sum(1 for event in change_events if event.change_type == change_type)

        # 중요도별 분포
        severity_distribution = {}
        for severity in ChangeSeverity:
            severity_distribution[severity] = sum(1 for event in change_events if event.severity == severity)

        return DocumentAnalysisReport(
            document_path=document_path,
            analysis_timestamp=time.time(),
            total_changes=len(change_events),
            change_summary=change_summary,
            severity_distribution=severity_distribution,
            change_events=change_events,
            processing_time_seconds=processing_time,
            file_metadata=file_metadata
        )

    def export_analysis_report(self, report: DocumentAnalysisReport, output_path: str, format: str = 'json') -> bool:
        """분석 보고서 내보내기

        Args:
            report: 분석 보고서
            output_path: 출력 파일 경로
            format: 출력 형식 ('json', 'txt', 'html')

        Returns:
            bool: 내보내기 성공 여부
        """
        try:
            if format.lower() == 'json':
                self._export_json_report(report, output_path)
            elif format.lower() == 'txt':
                self._export_text_report(report, output_path)
            elif format.lower() == 'html':
                self._export_html_report(report, output_path)
            else:
                raise ValueError(f"지원하지 않는 형식: {format}")

            self.logger("INFO", f"분석 보고서 내보내기 완료: {output_path}")
            return True

        except Exception as e:
            self.logger("ERROR", f"보고서 내보내기 실패: {e}")
            return False

    def _export_json_report(self, report: DocumentAnalysisReport, output_path: str) -> None:
        """JSON 형식으로 보고서 내보내기

        Args:
            report: 분석 보고서
            output_path: 출력 경로
        """
        report_dict = {
            'document_path': report.document_path,
            'analysis_timestamp': report.analysis_timestamp,
            'total_changes': report.total_changes,
            'change_summary': {k.value: v for k, v in report.change_summary.items()},
            'severity_distribution': {k.value: v for k, v in report.severity_distribution.items()},
            'change_events': [
                {
                    'change_type': event.change_type.value,
                    'location': event.location,
                    'content_before': event.content_before,
                    'content_after': event.content_after,
                    'line_number': event.line_number,
                    'severity': event.severity.value,
                    'timestamp': event.timestamp
                }
                for event in report.change_events
            ],
            'processing_time_seconds': report.processing_time_seconds,
            'file_metadata': report.file_metadata
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report_dict, f, indent=2, ensure_ascii=False)

    def _export_text_report(self, report: DocumentAnalysisReport, output_path: str) -> None:
        """텍스트 형식으로 보고서 내보내기

        Args:
            report: 분석 보고서
            output_path: 출력 경로
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"문서 변경 분석 보고서\n")
            f.write(f"=" * 50 + "\n\n")
            f.write(f"문서 경로: {report.document_path}\n")
            f.write(f"분석 시각: {datetime.fromtimestamp(report.analysis_timestamp)}\n")
            f.write(f"총 변경 사항: {report.total_changes}개\n")
            f.write(f"처리 시간: {report.processing_time_seconds:.2f}초\n\n")

            f.write("변경 유형별 요약:\n")
            f.write("-" * 30 + "\n")
            for change_type, count in report.change_summary.items():
                f.write(f"{change_type.value}: {count}개\n")

            f.write("\n중요도별 분포:\n")
            f.write("-" * 30 + "\n")
            for severity, count in report.severity_distribution.items():
                f.write(f"{severity.value}: {count}개\n")

            if report.change_events:
                f.write("\n상세 변경 내용:\n")
                f.write("-" * 30 + "\n")
                for i, event in enumerate(report.change_events, 1):
                    f.write(f"{i}. {event.change_type.value} - {event.location}\n")
                    if event.content_before:
                        f.write(f"   이전: {event.content_before[:100]}...\n")
                    if event.content_after:
                        f.write(f"   이후: {event.content_after[:100]}...\n")
                    f.write(f"   중요도: {event.severity.value}\n\n")

    def _export_html_report(self, report: DocumentAnalysisReport, output_path: str) -> None:
        """HTML 형식으로 보고서 내보내기

        Args:
            report: 분석 보고서
            output_path: 출력 경로
        """
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>문서 변경 분석 보고서</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .header {{ background-color: #f5f5f5; padding: 20px; border-radius: 5px; }}
                .summary {{ margin: 20px 0; }}
                .changes {{ margin: 20px 0; }}
                .change-item {{ border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px; }}
                .severity-low {{ background-color: #d4edda; }}
                .severity-medium {{ background-color: #fff3cd; }}
                .severity-high {{ background-color: #f8d7da; }}
                .severity-critical {{ background-color: #d1ecf1; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>문서 변경 분석 보고서</h1>
                <p><strong>문서:</strong> {report.document_path}</p>
                <p><strong>분석 시각:</strong> {datetime.fromtimestamp(report.analysis_timestamp)}</p>
                <p><strong>총 변경 사항:</strong> {report.total_changes}개</p>
                <p><strong>처리 시간:</strong> {report.processing_time_seconds:.2f}초</p>
            </div>
        """

        html_content += """
            <div class="summary">
                <h2>변경 유형별 요약</h2>
                <ul>
        """

        for change_type, count in report.change_summary.items():
            html_content += f"<li>{change_type.value}: {count}개</li>"

        html_content += """
                </ul>
            </div>
        """

        if report.change_events:
            html_content += '<div class="changes"><h2>상세 변경 내용</h2>'
            for event in report.change_events:
                html_content += f"""
                <div class="change-item severity-{event.severity.value}">
                    <h3>{event.change_type.value} - {event.location}</h3>
                    <p><strong>중요도:</strong> {event.severity.value}</p>
                    {f'<p><strong>이전:</strong> {event.content_before[:200]}...</p>' if event.content_before else ''}
                    {f'<p><strong>이후:</strong> {event.content_after[:200]}...</p>' if event.content_after else ''}
                </div>
                """
            html_content += "</div>"

        html_content += "</body></html>"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

    def clear_cache(self) -> None:
        """분석 캐시 초기화"""
        self._analysis_cache.clear()
        self.logger("INFO", "분석 캐시 초기화 완료")

    def get_cache_info(self) -> Dict[str, Any]:
        """캐시 정보 조회

        Returns:
            Dict[str, Any]: 캐시 정보
        """
        return {
            'cached_reports': len(self._analysis_cache),
            'cache_keys': list(self._analysis_cache.keys())
        }


# 편의 함수들
def analyze_document_changes(document_path: str, reference_path: Optional[str] = None, logger: Optional[callable] = None) -> DocumentAnalysisReport:
    """문서 변경 분석 편의 함수

    Args:
        document_path: 분석할 문서 경로
        reference_path: 기준 문서 경로 (선택사항)
        logger: 로거 (선택사항)

    Returns:
        DocumentAnalysisReport: 분석 보고서
    """
    analyzer = DocumentDiffAnalyzer(logger)
    return analyzer.analyze_document_changes(document_path, reference_path)

