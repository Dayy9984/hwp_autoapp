# -*- coding: utf-8 -*-
"""
Config Service
전역 설정 싱글톤
⭐ 규칙 O3: userDataPath 주입
⭐ 규칙 B1: 전역 싱글톤으로 사용 (self.config 금지)
"""

import os


class Config:
    """전역 설정 싱글톤"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.user_data_path = None
            cls._instance.initialized = False
        return cls._instance
    
    def set_user_data_path(self, path: str) -> None:
        """
        userData 경로 설정
        Electron Main에서 app.getPath('userData') 주입
        """
        self.user_data_path = path
        self.initialized = True
    
    def get_project_path(self, project_id: str) -> str:
        """프로젝트 기본 경로"""
        if not self.user_data_path:
            raise ValueError("user_data_path not set. Call set_user_data_path first.")
        return os.path.join(self.user_data_path, 'projects', project_id)
    
    def get_template_pair_path(self, project_id: str, pair_id: str) -> str:
        """Template Pair 디렉토리 경로"""
        return os.path.join(self.get_project_path(project_id), 'template_pairs', pair_id)
    
    def get_project_files_path(self, project_id: str) -> str:
        """프로젝트 파일 디렉토리"""
        return os.path.join(self.get_project_path(project_id), 'files')
    
    def get_project_chroma_path(self, project_id: str) -> str:
        """프로젝트별 Chroma 경로"""
        return os.path.join(self.get_project_path(project_id), 'vector_store', 'chroma')
    
    def get_global_chroma_path(self) -> str:
        """전역 Chroma 경로 (프로젝트 바인딩 없는 chat용)"""
        if not self.user_data_path:
            raise ValueError("user_data_path not set.")
        return os.path.join(self.user_data_path, 'vector_store', 'global_chroma')
    
    def get_chat_files_path(self, chat_id: str) -> str:
        """Chat 파일 디렉토리"""
        if not self.user_data_path:
            raise ValueError("user_data_path not set.")
        return os.path.join(self.user_data_path, 'chat_files', chat_id)


# ⭐ 규칙 B1: 전역 싱글톤으로 export
# 사용 시: from services.config import config
# 절대: self.config 사용 금지!
config = Config()
