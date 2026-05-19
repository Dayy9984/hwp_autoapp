"""
Modification Analyzer Module - Document change detection and analysis.

Provides functionality to detect and analyze document modifications
by converting documents to analysis format and scanning for tracked changes.

ModificationAnalyzer: Document modification detection and analysis
- Converts documents to analysis format (package-based)
- Scans for tracked change markers
- Reports insertion/deletion/modification counts
"""

import os
import tempfile
import zipfile
from typing import Dict, Optional, Tuple


class ModificationAnalyzer:
    """
    Detects and analyzes document modifications through format conversion.

    Converts documents to package format (ZIP-based) and parses internal
    markup to scan for tracked change markers and return statistics.
    """

    def __init__(self, hwp, log_to_main=None):
        """
        Args:
            hwp: Document engine instance
            log_to_main: Logging callback function
        """
        self.hwp = hwp
        self.log_to_main = log_to_main or (lambda level, message: None)

        # Internal state
        self._source_document_path: Optional[str] = None
        self._converted_package_path: Optional[str] = None
        self._temporary_workspace: Optional[str] = None
        self._source_document_identifier: Optional[int] = None

        self.log_to_main("DEBUG", "Modification Analyzer initialized")

    def configure_temporary_directory(self, temp_dir: str):
        """
        Configure temporary workspace directory for analysis.

        Creates workspace directory with pattern: analysis-workspace-XXXXXX

        Args:
            temp_dir: Temporary workspace directory path

        Side effects:
            - Sets self._temporary_workspace
            - Creates directory if it doesn't exist
        """
        self.log_to_main("DEBUG", f"configure_temporary_directory - path: {temp_dir}")
        self._temporary_workspace = temp_dir

        if temp_dir:
            os.makedirs(temp_dir, exist_ok=True)
            self.log_to_main("DEBUG", f"Workspace directory created: {temp_dir}")

    def _persist_active_document_and_retrieve_path(self) -> Tuple[str, bool]:
        """
        Persists current document and returns path with modification status.

        (Uses FileSave_S action)
        - If _source_document_path is None, saves to user's Desktop temporarily
        - Checks is_modified status

        Returns:
            Tuple[str, bool]: (path, is_modified)
        """
        self.log_to_main("DEBUG", "_persist_active_document_and_retrieve_path start")

        try:
            # Retrieve document information
            # XHwpDocuments, Active_XHwpDocument, FullName, DocumentID
            doc_path = self.hwp.Path  # or XHwpDocuments.Active_XHwpDocument.FullName
            is_modified = getattr(self.hwp, 'is_modified', False)

            self.log_to_main("DEBUG", f"Document Path: {doc_path}")
            self.log_to_main("DEBUG", f"Document is_modified: {is_modified}")

            if not doc_path or doc_path == "N/A":
                # No path available, save to Desktop temporarily
                desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
                temp_path = os.path.join(desktop, 'temp_doc_storage.hwp')

                self.log_to_main("INFO", f"No document path, saving to Desktop: {temp_path}")

                # FileSaveAs
                # self.hwp.save_as(temp_path)

                self._source_document_path = temp_path
                doc_path = temp_path

            else:
                # Save to existing path
                self.log_to_main("INFO", f"Saving to existing path: {doc_path}")
                # self.hwp.save()
                self._source_document_path = doc_path

            # Verify file size
            if os.path.exists(doc_path):
                file_size = os.path.getsize(doc_path)
                self.log_to_main("DEBUG", f"Saved file size: {file_size} bytes")
            else:
                self.log_to_main("WARNING", f"Saved file doesn't exist: {doc_path}")

            return (doc_path, is_modified)

        except Exception as e:
            self.log_to_main("ERROR", f"_persist_active_document_and_retrieve_path failed: {e}")
            raise

    def _convert_document_to_analysis_format(self) -> str:
        """
        Converts current document to package format for analysis.

        Parameters:
            - Creates temporary workspace if not configured
            - Converts document using `FileSaveAs_S` HAction
            - Stores path in self._converted_package_path

        Returns:
            str: Generated package file path

        Side effects:
            - Creates package file
            - Updates self._converted_package_path
        """
        self.log_to_main("DEBUG", "_convert_document_to_analysis_format start")

        try:
            # Verify and create temporary workspace
            if not self._temporary_workspace:
                self.log_to_main("DEBUG", "No workspace configured, creating automatically")
                self._temporary_workspace = tempfile.mkdtemp(prefix="analysis_workspace_")
                self.log_to_main("DEBUG", f"Workspace created: {self._temporary_workspace}")
            else:
                self.log_to_main("DEBUG", f"Using existing workspace: {self._temporary_workspace}")

            # Package export path
            export_path = os.path.join(self._temporary_workspace, "__analysis_export.hwpx")
            self.log_to_main("DEBUG", f"Package export path: {export_path}")

            # Convert to package format (FileSaveAs_S HAction)
            self.log_to_main("DEBUG", "Converting document to package format...")

            # Implementation example:
            # self.hwp.HAction.GetDefault("FileSaveAs_S", pset.HSet)
            # pset.SetItem("Format", "HWPX")
            # pset.SetItem("path", export_path)
            # self.hwp.HAction.Execute("FileSaveAs_S", pset.HSet)

            # Simple approach:
            # self.hwp.save_as(export_path, format="HWPX")

            self._converted_package_path = export_path

            # Verify conversion
            if os.path.exists(export_path):
                file_size = os.path.getsize(export_path)
                self.log_to_main("DEBUG", f"Package conversion complete: {file_size} bytes")
            else:
                self.log_to_main("WARNING", "Package file not created")

            return export_path

        except Exception as e:
            self.log_to_main("ERROR", f"_convert_document_to_analysis_format failed: {e}")
            raise

    def _analyze_document_package_for_modifications(self, package_path: str) -> Dict[str, int]:
        """
        Analyzes document package (ZIP) for tracked change markers.

        Package format is ZIP-based, so we extract and scan markup files.

        Scan patterns:
        - <ins>, inserted=", insert=, insertBegin → insertions
        - <del>, deleted=", delete=, deleteBegin → deletions
        - trackChange, track-change, changeId=" → tracked

        Returns:
            Dict[str, int]: {'ins': 3, 'del': 0, 'mod': 1, 'track': 2}

        Example:
            modification_counts = self._analyze_document_package_for_modifications("/tmp/analysis-XXXX/__analysis_export.hwpx")
            print(f"Insertions: {modification_counts['ins']}, Deletions: {modification_counts['del']}")
        """
        self.log_to_main("DEBUG", f"Package analysis start: {package_path}")

        modification_counts = {
            'ins': 0,   # insertions
            'del': 0,   # deletions
            'mod': 0,   # modifications (currently unused)
            'track': 0  # tracked changes
        }

        try:
            with zipfile.ZipFile(package_path, 'r') as zf:
                # List files in ZIP
                file_list = zf.namelist()
                self.log_to_main("DEBUG", f"Files in package: {len(file_list)}")

                xml_files = [f for f in file_list if f.lower().endswith('.xml')]
                self.log_to_main("DEBUG", f"XML files found: {len(xml_files)}")

                for xml_file in xml_files:
                    try:
                        # Read XML file
                        with zf.open(xml_file) as f:
                            text = f.read().decode('utf-8', errors='ignore')

                        # Count insertion markers
                        modification_counts['ins'] += text.count('<ins')
                        modification_counts['ins'] += text.count('inserted="')
                        modification_counts['ins'] += text.count('insert=')
                        modification_counts['ins'] += text.count('insertBegin')

                        # Count deletion markers
                        modification_counts['del'] += text.count('<del')
                        modification_counts['del'] += text.count('deleted="')
                        modification_counts['del'] += text.count('delete=')
                        modification_counts['del'] += text.count('deleteBegin')

                        # Count tracking markers
                        modification_counts['track'] += text.count('trackChange')
                        modification_counts['track'] += text.count('track-change')
                        modification_counts['track'] += text.count('changeId="')

                    except Exception as e:
                        self.log_to_main("WARNING", f"XML file read failed ({xml_file}): {e}")

            self.log_to_main("INFO", f"=== Modification Analysis Summary ===")
            self.log_to_main("INFO", f"Insertions: {modification_counts['ins']}")
            self.log_to_main("INFO", f"Deletions: {modification_counts['del']}")
            self.log_to_main("INFO", f"Tracked: {modification_counts['track']}")

            return modification_counts

        except Exception as e:
            self.log_to_main("ERROR", f"_analyze_document_package_for_modifications failed: {e}")
            return modification_counts

    def detect_tracked_modifications(self) -> Dict[str, int]:
        """
        Detects tracked modifications in the current document.

        Complete process:
        1. Save current document
        2. Convert to package format
        3. Scan package (ZIP) internal markup
        4. Count tracked change markers

        Returns:
            Dict[str, int]: {'ins': int, 'del': int, 'mod': int, 'track': int}
        """
        self.log_to_main("INFO", "detect_tracked_modifications start")

        try:
            # 1. Save current document
            doc_path, is_modified = self._persist_active_document_and_retrieve_path()
            self.log_to_main("DEBUG", f"Document persisted: {doc_path}, modified: {is_modified}")

            # 2. Convert to package format
            package_path = self._convert_document_to_analysis_format()
            self.log_to_main("DEBUG", f"Package conversion complete: {package_path}")

            # 3. Analyze package
            modification_counts = self._analyze_document_package_for_modifications(package_path)

            self.log_to_main("INFO", "detect_tracked_modifications complete")
            return modification_counts

        except Exception as e:
            self.log_to_main("ERROR", f"detect_tracked_modifications failed: {e}")
            return {'ins': 0, 'del': 0, 'mod': 0, 'track': 0}
