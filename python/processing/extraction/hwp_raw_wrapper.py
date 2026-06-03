"""Raw HWP COM 객체 래퍼"""


class HwpRawWrapper:
    """COM 객체 직접 접근 래퍼"""

    def __init__(self, raw_com):
        """
        Args:
            raw_com: ROT에서 가져온 raw HWP COM 객체
        """
        self.hwp = raw_com
        self._raw = raw_com

    @property
    def PageCount(self):
        return self._raw.PageCount

    @property
    def CharShape(self):
        return self._raw.CharShape

    @property
    def ParaShape(self):
        return self._raw.ParaShape

    @property
    def Version(self):
        """HWP 버전 정보"""
        return self._raw.Version

    @property
    def CurrentPage(self):
        """현재 페이지 번호 (pyhwpx 동일: XHwpDocuments 기반, 1-indexed)"""
        try:
            return self._raw.XHwpDocuments.Active_XHwpDocument.XHwpDocumentInfo.CurrentPage + 1
        except Exception:
            try:
                return self._raw.CurrentPage
            except Exception:
                return 1

    @property
    def HeadCtrl(self):
        """첫 번째 컨트롤"""
        try:
            return self._raw.HeadCtrl
        except Exception:
            return None

    @property
    def LastCtrl(self):
        """마지막 컨트롤"""
        try:
            return self._raw.LastCtrl
        except Exception:
            return None

    @property
    def CurSelectedCtrl(self):
        """현재 선택된 컨트롤"""
        try:
            return self._raw.CurSelectedCtrl
        except Exception:
            return None

    @property
    def SelectionMode(self):
        """현재 선택 모드 (pyhwpx 호환)

        Returns:
            int: 선택 모드
                - 0: 선택 없음
                - 1: 일반 블록 선택
                - 2: 열 선택
                - 4: 표 셀 선택
                - 8: 컨트롤 선택
        """
        try:
            return self._raw.SelectionMode
        except Exception:
            return 0

    @property
    def Path(self):
        """문서 경로 (pyhwpx 호환)"""
        try:
            return self._raw.Path
        except Exception:
            return None

    @property
    def lsTrackChange(self):
        """Track Changes 활성화 상태 (pyhwpx 호환)"""
        try:
            return self._raw.lsTrackChange
        except Exception:
            return None

    @lsTrackChange.setter
    def lsTrackChange(self, value):
        """Track Changes 활성화 상태 설정"""
        try:
            self._raw.lsTrackChange = bool(value)
        except Exception:
            pass

    def GetPos(self):
        """현재 위치 반환 (COM 결과를 tuple로 정규화, 실패 시 (0,0,0) fallback)"""
        try:
            result = self._raw.GetPos()
            if isinstance(result, (list, tuple)):
                return tuple(result)
            return (0, 0, 0)
        except Exception:
            return (0, 0, 0)

    def GetPosBySet(self):
        """위치 조회 (ParameterSet 기반, pyhwpx 호환)

        Returns:
            ParameterSet: ListParaPos 형식의 위치 정보 또는 None
        """
        try:
            return self._raw.GetPosBySet()
        except Exception:
            return None

    def MovePos(self, move_id, para=0, pos=0):
        """위치 이동 (pyhwpx 동일: keyword args)"""
        return self._raw.MovePos(moveID=move_id, Para=para, pos=pos)

    def SetPos(self, list_id, para_id, char_pos):
        """위치 설정 (pyhwpx 동일: keyword args + 검증)"""
        try:
            self._raw.SetPos(List=list_id, Para=para_id, pos=char_pos)
            current = self.GetPos()
            if (list_id, para_id) == current[:2]:
                return True
            else:
                return False
        except Exception:
            return False

    def InitScan(self, option=0x07, Range=0x77, spara=0, spos=0, epara=-1, epos=-1):
        """스캔 초기화 (pyhwpx 동일: keyword args로 COM 호출)"""
        return self._raw.InitScan(option=option, Range=Range, spara=spara, spos=spos, epara=epara, epos=epos)

    def GetText(self):
        """텍스트 가져오기 (COM 결과를 tuple로 정규화, 실패 시 (0,'') fallback)"""
        try:
            result = self._raw.GetText()
            if isinstance(result, tuple) and len(result) >= 2:
                return result
            elif result is not None:
                return (1, str(result))
            else:
                return (0, "")
        except Exception:
            return (0, "")

    def ReleaseScan(self):
        """스캔 해제"""
        return self._raw.ReleaseScan()

    def GetHeadingString(self):
        """헤딩 문자열 가져오기"""
        try:
            return self._raw.GetHeadingString()
        except:
            return ""

    # pyhwpx 호환 메서드 (소문자)
    @property
    def current_page(self):
        """현재 페이지 번호 (소문자)"""
        return self.CurrentPage

    @property
    def ctrl_list(self):
        """컨트롤 리스트 (pyhwpx 호환)"""
        controls = []
        try:
            # HeadCtrl.Next.Next부터 시작 (secd, cold 제외)
            ctrl = self._raw.HeadCtrl
            if ctrl:
                ctrl = ctrl.Next
                if ctrl:
                    ctrl = ctrl.Next

            # ctrl.Next로 순회
            while ctrl:
                controls.append(ctrl)
                ctrl = ctrl.Next
        except Exception:
            pass

        return controls

    def get_pos(self):
        """현재 위치 반환"""
        return self.GetPos()

    def move_pos(self, move_id, para=0, pos=0):
        """위치 이동"""
        return self.MovePos(move_id, para, pos)

    def set_pos(self, List, para, pos):
        """위치 설정 (pyhwpx 동일: 키워드 인자 + 검증)"""
        return self.SetPos(List, para, pos)

    def init_scan(self, option=0x07, range=0x77, spara=0, spos=0, epara=-1, epos=-1):
        """스캔 초기화"""
        return self.InitScan(option=option, Range=range, spara=spara, spos=spos, epara=epara, epos=epos)

    def get_text(self):
        """텍스트 가져오기"""
        return self.GetText()

    def release_scan(self):
        """스캔 해제"""
        return self.ReleaseScan()

    def get_heading_string(self):
        """헤딩 문자열 가져오기"""
        return self.GetHeadingString()

    def goto_page(self, page_index):
        """페이지 이동 (pyhwpx 동일 로직: goto_printpage → MovePageDown/Up 보정)"""
        try:
            if int(page_index) > self._raw.PageCount:
                return False
            elif int(page_index) < 1:
                return False

            # pyhwpx: goto_printpage() — HAction Goto 대화상자로 정확한 페이지 점프
            try:
                pset = self._raw.HParameterSet.HGotoE
                self._raw.HAction.GetDefault("Goto", pset.HSet)
                pset.HSet.SetItem("DialogResult", page_index)
                pset.SetSelectionIndex = 1
                self._raw.HAction.Execute("Goto", pset.HSet)
            except Exception:
                # HGotoE 실패 시 — 문서 시작으로 이동 후 보정 루프로 처리
                try:
                    self._raw.MovePos(moveID=2, Para=0, pos=0)
                except Exception:
                    pass

            # MovePageDown/Up으로 미세 보정
            cur_page = self.current_page
            if page_index < cur_page:
                for _ in range(cur_page - page_index):
                    self.MovePageUp()
            elif page_index > cur_page:
                for _ in range(page_index - cur_page):
                    self.MovePageDown()

            return self.current_page
        except Exception:
            return False

    def MovePageDown(self):
        """페이지 다운 이동 (pyhwpx 동일: 위치 변경 여부 반환)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MovePageDown")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MovePageUp(self):
        """페이지 업 이동 (pyhwpx 동일: 위치 변경 여부 반환)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MovePageUp")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveSelPageDown(self):
        """선택 영역 페이지 다운 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveSelPageDown")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveSelNextParaBegin(self):
        """다음 문단 시작으로 선택 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveSelNextParaBegin")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveSelLineDown(self):
        """선택 영역 줄 아래로 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveSelLineDown")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveSelRight(self):
        """선택 영역 오른쪽으로 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveSelRight")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def get_ctrl_pos(self, ctrl=None, option=0):
        """컨트롤 위치 가져오기 (pyhwpx 동일: positional arg로 GetAnchorPos 호출)"""
        try:
            if ctrl is None:
                ctrl = self._raw.CurSelectedCtrl
            return (
                ctrl.GetAnchorPos(option).Item("List"),
                ctrl.GetAnchorPos(option).Item("Para"),
                ctrl.GetAnchorPos(option).Item("Pos"),
            )
        except Exception:
            return (0, 0, 0)

    def get_into_nth_table(self, n=0, select_cell=False):
        """N번째 테이블로 이동 (pyhwpx 동일 로직: 항상 SelCell → 조건부 Cancel)"""
        try:
            if n >= 0:
                idx = 0
                ctrl = self._raw.HeadCtrl
            else:
                idx = -1
                ctrl = self._raw.LastCtrl

            while ctrl:
                if getattr(ctrl, "UserDesc", None) == "표":
                    if n in (0, -1):
                        self.SetPosBySet(ctrl.GetAnchorPos(0))
                        self._raw.FindCtrl()
                        self.ShapeObjTableSelCell()
                        if not select_cell:
                            self.Cancel()
                        return ctrl
                    else:
                        if idx == n:
                            self.SetPosBySet(ctrl.GetAnchorPos(0))
                            self._raw.FindCtrl()
                            self.ShapeObjTableSelCell()
                            if not select_cell:
                                self.Cancel()
                            return ctrl
                        if n >= 0:
                            idx += 1
                        else:
                            idx -= 1
                if n >= 0:
                    ctrl = ctrl.Next
                else:
                    ctrl = ctrl.Prev
            return False
        except Exception:
            return False

    def TableCellBlock(self):
        """테이블 셀 블록 선택 (pyhwpx: HAction.Run)"""
        try:
            return self._raw.HAction.Run("TableCellBlock")
        except Exception:
            return False

    def MoveDown(self):
        """아래로 이동 (pyhwpx 동일: 위치 변경 여부 반환)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveDown")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def TableColBegin(self):
        """테이블 열 시작 (pyhwpx: HAction.Run)"""
        try:
            return self._raw.HAction.Run("TableColBegin")
        except Exception:
            return False

    def TableColPageUp(self):
        """테이블 열 페이지 업 (pyhwpx: HAction.Run)"""
        try:
            return self._raw.HAction.Run("TableColPageUp")
        except Exception:
            return False

    def GetTextFile(self, format, option=None):
        """텍스트 파일 가져오기 (pyhwpx 동일: keyword args)"""
        try:
            if option is not None:
                return self._raw.GetTextFile(Format=format, option=option)
            else:
                return self._raw.GetTextFile(Format=format, option="")
        except Exception:
            return None

    def SetPosBySet(self, pos_set):
        """위치 설정 (pyhwpx 동일: keyword arg dispVal=)"""
        try:
            return self._raw.SetPosBySet(dispVal=pos_set)
        except Exception:
            return False

    def FindCtrl(self):
        """컨트롤 찾기"""
        try:
            return self._raw.FindCtrl()
        except Exception:
            return False

    def ShapeObjTableSelCell(self):
        """테이블 셀 선택"""
        try:
            return self._raw.HAction.Run("ShapeObjTableSelCell")
        except Exception:
            return False

    def Cancel(self):
        """취소/선택 해제"""
        try:
            return self._raw.HAction.Run("Cancel")
        except Exception:
            return False

    def is_cell(self):
        """현재 커서가 표 셀 내부인지 확인 (pyhwpx 호환)"""
        try:
            list_pos, _, _ = self.get_pos()
            # list_pos > 2이면 표/글상자 등 특수 영역
            return list_pos > 2
        except Exception:
            return False

    def GetTableCellAddr(self, addr_type=0):
        """표 셀 주소 가져오기 (pyhwpx 호환)

        Args:
            addr_type: 주소 타입 (0=행, 1=열)

        Returns:
            int: 셀 주소 (표 밖이면 -1)
        """
        try:
            return self._raw.GetTableCellAddr(addr_type)
        except Exception:
            return -1

    def find(self, text, direction="Forward", regex=False):
        """텍스트 찾기 (pyhwpx 호환).

        HAction.Execute("RepeatFind") 가 매칭 실패 시 "문서의 처음/끝까지 찾았습니다" dialog 노출.
        호출 전후 SetMessageBoxMode 으로 dialog 차단 + caller 의 prev_mode 복원.
        """
        prev_mode = None
        try:
            try:
                prev_mode = self._raw.SetMessageBoxMode(0x2FFF1)
            except Exception:
                prev_mode = None
            try:
                pset = self._raw.HParameterSet.HFindReplace
                self._raw.HAction.GetDefault("RepeatFind", pset.HSet)
                pset.FindString = text
                pset.Direction = 0 if direction == "Forward" else 1
                pset.UseWildCards = regex
                pset.FindRegExp = regex
                return self._raw.HAction.Execute("RepeatFind", pset.HSet)
            except Exception:
                return False
        finally:
            if prev_mode is not None:
                try:
                    self._raw.SetMessageBoxMode(prev_mode)
                except Exception:
                    pass

    def TableRightCell(self):
        """테이블 오른쪽 셀로 이동"""
        try:
            return self._raw.HAction.Run("TableRightCell")
        except Exception:
            return False

    def MoveParentList(self):
        """상위 리스트로 이동"""
        try:
            return self._raw.HAction.Run("MoveParentList")
        except Exception:
            return False

    # ========== 이동 메서드 (Movement Methods) ==========

    def MoveParaBegin(self):
        """문단 시작으로 이동"""
        try:
            return self._raw.HAction.Run("MoveParaBegin")
        except Exception:
            return False

    def MoveParaEnd(self):
        """문단 끝으로 이동"""
        try:
            return self._raw.HAction.Run("MoveParaEnd")
        except Exception:
            return False

    def MoveUp(self):
        """위로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveUp")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveRight(self):
        """오른쪽으로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveRight")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveNextChar(self):
        """다음 글자로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveNextChar")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MovePrevChar(self):
        """이전 글자로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MovePrevChar")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveLineBegin(self):
        """줄 시작으로 이동"""
        try:
            return self._raw.HAction.Run("MoveLineBegin")
        except Exception:
            return False

    def MoveLineEnd(self):
        """줄 끝으로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveLineEnd")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveLineUp(self):
        """줄 위로 이동 (pyhwpx 동일: HAction.Run + 위치변경 감지)"""
        try:
            cwd = self.GetPos()
            self._raw.HAction.Run("MoveLineUp")
            new_pos = self.GetPos()
            if new_pos[0] != cwd[0] or new_pos[1:] != cwd[1:]:
                return True
            else:
                return False
        except Exception:
            return False

    def MoveDocBegin(self):
        """문서 처음으로 이동"""
        try:
            return self._raw.HAction.Run("MoveDocBegin")
        except Exception:
            return False

    # ========== 선택 메서드 (Selection Methods) ==========

    def MoveSelParaEnd(self):
        """문단 끝까지 선택"""
        try:
            return self._raw.HAction.Run("MoveSelParaEnd")
        except Exception:
            return False

    def MoveSelParaBegin(self):
        """문단 시작까지 선택"""
        try:
            return self._raw.HAction.Run("MoveSelParaBegin")
        except Exception:
            return False

    def MoveSelLineEnd(self):
        """줄 끝까지 선택"""
        try:
            return self._raw.HAction.Run("MoveSelLineEnd")
        except Exception:
            return False

    def SelectAll(self):
        """전체 선택 (셀 내부면 셀 내용 전체, 아니면 문서 전체)"""
        try:
            return self._raw.HAction.Run("SelectAll")
        except Exception:
            return False

    # ========== 편집 메서드 (Editing Methods) ==========

    def DeleteBack(self):
        """백스페이스 삭제"""
        try:
            return self._raw.HAction.Run("DeleteBack")
        except Exception:
            return False

    def Delete(self):
        """Delete 키 삭제"""
        try:
            return self._raw.HAction.Run("Delete")
        except Exception:
            return False

    def InsertText(self, text):
        """텍스트 삽입"""
        try:
            return self._raw.HAction.Run("InsertText", text)
        except Exception:
            return False

    def BreakPara(self):
        """문단 나누기 (Enter)"""
        try:
            return self._raw.HAction.Run("BreakPara")
        except Exception:
            return False

    def insert_text(self, text):
        """텍스트 삽입 (pyhwpx 호환)"""
        try:
            act = self._raw.CreateAction("InsertText")
            pset = act.CreateSet()
            act.GetDefault(pset)
            pset.SetItem("Text", text)
            return act.Execute(pset)
        except Exception:
            return False

    # ========== 텍스트/폰트 메서드 (Text/Font Methods) ==========

    def select_text(self, *args, **kwargs):
        """텍스트 범위 선택 (pyhwpx 호환 - 두 가지 시그니처 지원)

        사용법 1: select_text(start_para, start_pos, end_para, end_pos, list_id=None)
        사용법 2: select_text(start_pos: tuple, end_pos: tuple)  # document_connector 호환

        SelectText COM 메서드 사용 (HWP 2020~2024 모두 지원)

        중요: list_id가 제공되면 현재 list와 다를 경우 먼저 해당 list로 이동
        """
        # 시그니처 감지
        list_id = None
        if len(args) == 2 and isinstance(args[0], (tuple, list)) and isinstance(args[1], (tuple, list)):
            # 튜플 버전: select_text((list, para, char), (list, para, char))
            start_pos, end_pos = args
            if len(start_pos) < 3 or len(end_pos) < 3:
                return False
            list_id = start_pos[0]
            start_para, start_char = start_pos[1], start_pos[2]
            end_para, end_char = end_pos[1], end_pos[2]
        elif len(args) >= 4:
            # 개별 파라미터 버전: select_text(start_para, start_pos, end_para, end_pos, list_id=None)
            start_para, start_char, end_para, end_char = args[0], args[1], args[2], args[3]
            if len(args) >= 5:
                list_id = args[4]
        else:
            return False

        try:
            # list_id가 제공되고 현재 위치와 다르면 먼저 이동
            if list_id is not None:
                try:
                    current_pos = self._raw.GetPos()
                    if isinstance(current_pos, (list, tuple)) and len(current_pos) >= 3:
                        current_list = current_pos[0]
                        if current_list != list_id:
                            # 해당 list로 이동
                            self._raw.SetPos(List=list_id, Para=start_para, pos=start_char)
                except Exception:
                    pass  # 이동 실패해도 SelectText 시도

            # SelectText COM 메서드 사용
            result = self._raw.SelectText(spara=start_para, spos=start_char, epara=end_para, epos=end_char)
            return bool(result)
        except Exception:
            return False

    def set_font(self, **kwargs):
        """글꼴 설정 (pyhwpx 호환)"""
        try:
            act = self._raw.CreateAction("CharShape")
            pset = act.CreateSet()
            act.GetDefault(pset)
            for key, value in kwargs.items():
                pset.SetItem(key, value)
            return act.Execute(pset)
        except Exception:
            return False

    def markpen_on_selection(self, r=255, g=255, b=0):
        """선택 영역에 형광펜 적용 (pyhwpx 호환)

        b820072의 document_connector.py 구현과 동일:
        RGB 값을 HWP 색상 형식(BGR)으로 변환하여 ShadeColor 설정

        Args:
            r: Red (0-255)
            g: Green (0-255)
            b: Blue (0-255)

        Returns:
            bool: 성공 여부
        """
        try:
            act = self._raw.CreateAction("CharShape")
            pset = act.CreateSet()
            act.GetDefault(pset)

            # RGB → BGR 변환
            color_value = (b << 16) | (g << 8) | r
            pset.SetItem("ShadeColor", color_value)

            return act.Execute(pset)
        except Exception:
            return False

    def set_para(self, **kwargs):
        """문단 설정 (pyhwpx 호환)"""
        try:
            act = self._raw.CreateAction("ParagraphShape")
            pset = act.CreateSet()
            act.GetDefault(pset)
            for key, value in kwargs.items():
                pset.SetItem(key, value)
            return act.Execute(pset)
        except Exception:
            return False

    def get_selected_text(self, keep_select=True):
        """선택된 텍스트 가져오기 (pyhwpx 호환)"""
        try:
            text = self._raw.GetTextFile(Format="TEXT", option="saveblock")
            if not keep_select:
                self._raw.HAction.Run("Cancel")
            return text.strip() if text else ""
        except Exception:
            return ""

    # ========== 테이블 메서드 (Table Methods) ==========

    def TableColEnd(self):
        """테이블 열 끝으로 이동"""
        try:
            return self._raw.HAction.Run("TableColEnd")
        except Exception:
            return False

    def TableColPageDown(self):
        """테이블 열 페이지 다운"""
        try:
            return self._raw.HAction.Run("TableColPageDown")
        except Exception:
            return False

    def TableLeftCell(self):
        """테이블 왼쪽 셀로 이동"""
        try:
            return self._raw.HAction.Run("TableLeftCell")
        except Exception:
            return False

    def TableAppendRow(self):
        """테이블 행 추가"""
        try:
            return self._raw.HAction.Run("TableAppendRow")
        except Exception:
            return False

    def TableSubtractRow(self):
        """테이블 행 삭제"""
        try:
            return self._raw.HAction.Run("TableSubtractRow")
        except Exception:
            return False

    # ========== 속성 (Properties) ==========

    @property
    def HParameterSet(self):
        """HParameterSet 속성 (TrackChange, Config 등 설정 접근)"""
        return self._raw.HParameterSet

    @property
    def HAction(self):
        """HAction 속성"""
        return self._raw.HAction

    def IsActionEnable(self, action_id):
        """액션 활성화 상태 확인 (pyhwpx 호환)

        Args:
            action_id: 액션 ID (문자열 또는 숫자)

        Returns:
            bool: 액션 실행 가능 여부
        """
        try:
            return bool(self._raw.IsActionEnable(action_id))
        except Exception:
            return False

    def undo(self):
        """실행 취소 (pyhwpx 호환)"""
        try:
            return self._raw.HAction.Run("Undo")
        except Exception:
            return False

    def redo(self):
        """다시 실행 (pyhwpx 호환)"""
        try:
            return self._raw.HAction.Run("Redo")
        except Exception:
            return False

    @property
    def ParentCtrl(self):
        """상위 컨트롤"""
        try:
            return self._raw.ParentCtrl
        except Exception:
            return None

    @property
    def XHwpDocuments(self):
        """문서 컬렉션"""
        try:
            return self._raw.XHwpDocuments
        except Exception:
            return None

    # ========== 유틸리티 메서드 (Utility Methods) ==========

    def HwpUnitToPoint(self, hwp_unit):
        """HWP 단위를 포인트로 변환"""
        try:
            return self._raw.HwpUnitToPoint(hwp_unit)
        except Exception:
            return 0

    def MiliToHwpUnit(self, mili):
        """mm를 HWP 단위로 변환 (pyhwpx 호환)

        Args:
            mili: mm 값

        Returns:
            int: HWP 단위 값
        """
        try:
            return self._raw.MiliToHwpUnit(mili)
        except Exception:
            return 0

    def FontType(self, font_type_name):
        """폰트 타입 플래그 반환"""
        try:
            return self._raw.FontType(font_type_name)
        except Exception:
            return 0

    def BrushType(self, brush_type_name):
        """브러시 타입 플래그 반환"""
        try:
            return self._raw.BrushType(brush_type_name)
        except Exception:
            return 0

    def RGBColor(self, r, g, b):
        """RGB 색상 값 반환"""
        try:
            return self._raw.RGBColor(r, g, b)
        except Exception:
            return 0

    def HatchStyle(self, style_name):
        """해치 스타일 플래그 반환"""
        try:
            return self._raw.HatchStyle(style_name)
        except Exception:
            return 0

    def HwpLineWidth(self, width):
        """선 너비 값 반환"""
        try:
            return self._raw.HwpLineWidth(width)
        except Exception:
            return 0

    def TableBreak(self, mode):
        """테이블 나누기 모드 반환"""
        try:
            return self._raw.TableBreak(mode)
        except Exception:
            return 0

    def select_ctrl(self, ctrl):
        """컨트롤 선택"""
        try:
            return self._raw.SelectCtrl(ctrl)
        except Exception:
            return False

    def delete_ctrl(self, ctrl):
        """컨트롤 삭제"""
        try:
            return self._raw.DeleteCtrl(ctrl)
        except Exception:
            return False

    def CharShapeSpacingDecrease(self):
        """자간 줄이기"""
        try:
            return self._raw.HAction.Run("CharShapeSpacingDecrease")
        except Exception:
            return False

    def set_track_change_colors(self, insert_color=11, delete_color=6, format_color=11,
                               insert_shape=0, format_shape=0):
        """
        Track Changes 색상 및 표시 방식 설정

        매크로 패턴:
            HAction.GetDefault("TrackChangeOption", HParameterSet.HTrackChange.HSet)
            HParameterSet.HTrackChange.InsertShape = 1
            HParameterSet.HTrackChange.InsertColor = 11
            HParameterSet.HTrackChange.DeleteColor = 6
            HParameterSet.HTrackChange.FormatColor = 11
            HAction.Execute("TrackChangeOption", HParameterSet.HTrackChange.HSet)

        Args:
            insert_color: 삽입 색상 (HWP color index, 기본값 11=녹색)
            delete_color: 삭제 색상 (HWP color index, 기본값 6=빨강)
            format_color: 서식 색상 (HWP color index, 기본값 11=녹색)
            insert_shape: 삽입 표시 방식 (0=색만, 1=밑줄, ...)
            format_shape: 서식 표시 방식 (0=색만, 1=밑줄, ...)

        Returns:
            bool: 성공 여부
        """
        try:
            pset = self._raw.HParameterSet.HTrackChange
            self._raw.HAction.GetDefault("TrackChangeOption", pset.HSet)

            # 색상 설정
            pset.InsertColor = insert_color
            pset.DeleteColor = delete_color
            pset.FormatColor = format_color

            # 표시 방식 설정 (DeleteShape는 존재하지 않음 - 매크로 확인)
            pset.InsertShape = insert_shape
            pset.FormatShape = format_shape

            # 적용
            result = self._raw.HAction.Execute("TrackChangeOption", pset.HSet)
            return bool(result)
        except Exception:
            return False

    def ParagraphShapeIndentAtCaret(self):
        """커서 위치에서 문단 들여쓰기"""
        try:
            return self._raw.HAction.Run("ParagraphShapeIndentAtCaret")
        except Exception:
            return False

    def InsertFootnote(self):
        """각주 삽입"""
        try:
            return self._raw.HAction.Run("InsertFootnote")
        except Exception:
            return False

    def create_table(self, rows, cols, width_type=0, height_type=1, treat_as_char=True, **kwargs):
        """테이블 생성 (pyhwpx 호환)"""
        try:
            act = self._raw.CreateAction("TableCreate")
            pset = act.CreateSet()
            act.GetDefault(pset)
            pset.SetItem("Rows", rows)
            pset.SetItem("Cols", cols)
            pset.SetItem("WidthType", width_type)
            pset.SetItem("HeightType", height_type)
            pset.SetItem("CreateItemArray", "CellCount", rows * cols)
            pset.SetItem("TreatAsChar", treat_as_char)
            for key, value in kwargs.items():
                pset.SetItem(key, value)
            return act.Execute(pset)
        except Exception:
            return False

    def CreateAction(self, action_name):
        """액션 생성"""
        try:
            return self._raw.CreateAction(action_name)
        except Exception:
            return None

    def CreateSet(self, set_name):
        """파라미터셋 생성"""
        try:
            return self._raw.CreateSet(set_name)
        except Exception:
            return None

    def GetSelectedPosBySet(self, sset, eset):
        """선택 영역의 시작/끝 위치 가져오기 (pyhwpx 호환)

        Args:
            sset: 시작 위치를 받을 ParameterSet (ListParaPos)
            eset: 끝 위치를 받을 ParameterSet (ListParaPos)

        Returns:
            bool: 성공 여부 (선택 영역이 있으면 True, 없으면 False)
        """
        try:
            return self._raw.GetSelectedPosBySet(sset, eset)
        except Exception:
            return False

    def GetSelectionPos(self):
        """선택 영역 위치 가져오기 (COM 스타일, pyhwpx 호환)

        Returns:
            tuple: ((start_list, start_para, start_pos), (end_list, end_para, end_pos)) 또는 None
        """
        try:
            result = self._raw.GetSelectionPos()
            if result:
                return result
            return None
        except Exception:
            return None

    def get_selection_pos(self):
        """선택 영역 위치 가져오기 (pyhwpx 스타일)

        Returns:
            tuple: ((start_list, start_para, start_pos), (end_list, end_para, end_pos)) 또는 None
        """
        try:
            result = self._raw.GetSelectionPos()
            if result:
                return result
            return None
        except Exception:
            return None

    def SetMessageBoxMode(self, mode):
        """메시지박스 모드 설정 (COM 스타일, pyhwpx 호환)

        Args:
            mode: 메시지박스 모드 (0=표시 안함, 1=표시)

        Returns:
            int: 이전 모드 값
        """
        try:
            return self._raw.SetMessageBoxMode(mode)
        except Exception:
            return None

    def set_message_box_mode(self, mode):
        """메시지박스 모드 설정 (pyhwpx 스타일)

        Args:
            mode: 메시지박스 모드 (0=표시 안함, 1=표시)

        Returns:
            int: 이전 모드 값
        """
        try:
            return self._raw.SetMessageBoxMode(mode)
        except Exception:
            return None
