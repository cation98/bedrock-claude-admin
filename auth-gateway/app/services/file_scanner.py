"""민감정보 자동 분류 서비스 — 파일명, 조직, 콘텐츠 기반."""

import re
import os
import logging
from enum import Enum

logger = logging.getLogger(__name__)

# ── Classification patterns ──

SENSITIVE_FILENAME_PATTERNS = [
    re.compile(r"(?i)(salary|급여|연봉|임금)"),
    re.compile(r"(?i)(hr_|인사|인적자원|employee)"),
    re.compile(r"(?i)(personal|개인정보|주민|resident)"),
    re.compile(r"(?i)(finance|재무|회계|accounting)"),
    re.compile(r"(?i)(payroll|급여대장|성과급)"),
    re.compile(r"(?i)(appraisal|인사평가|성과평가)"),
]

SENSITIVE_TEAMS = {"HR팀", "인사팀", "재무관리팀", "ER팀", "경영기획팀"}

PII_PATTERNS = [
    re.compile(r"\d{6}-[1-4]\d{6}"),           # 주민등록번호
    re.compile(r"01[016789]-?\d{3,4}-?\d{4}"),  # 휴대폰번호
    re.compile(r"[가-힣]{2,4}\s*\d{6,8}"),      # 이름+사번 패턴
]

MAX_CONTENT_SCAN_SIZE = 50 * 1024 * 1024  # 50MB


class ClassificationResult:
    def __init__(self, classification: str, reason: str):
        self.classification = classification  # "sensitive", "normal", "unknown"
        self.reason = reason


def classify_file(filename: str, file_path: str, file_size: int = 0,
                  team_name: str = None, content: str = None) -> ClassificationResult:
    """파일을 분류한다. 우선순위: 파일명 → 조직 → 콘텐츠 → 확장자 기반 기본값."""

    # 1. 파일명 패턴 매칭
    for pattern in SENSITIVE_FILENAME_PATTERNS:
        if pattern.search(filename):
            return ClassificationResult("sensitive", f"filename_match:{pattern.pattern}")

    # 2. 조직 기반
    if team_name and team_name in SENSITIVE_TEAMS:
        return ClassificationResult("sensitive", f"team_match:{team_name}")

    # 3. 콘텐츠 기반 PII 검사 (50MB 이하만)
    if content and file_size <= MAX_CONTENT_SCAN_SIZE:
        for pattern in PII_PATTERNS:
            if pattern.search(content):
                return ClassificationResult("sensitive", f"pii_match:{pattern.pattern}")

    # 4. 50MB 초과 파일은 unknown (관리자 수동 분류 필요)
    if file_size > MAX_CONTENT_SCAN_SIZE:
        return ClassificationResult("unknown", "file_too_large_for_content_scan")

    return ClassificationResult("normal", "no_sensitive_patterns_found")


def extract_text_content(file_path: str, max_size: int = MAX_CONTENT_SCAN_SIZE) -> str | None:
    """파일에서 텍스트 내용을 추출한다. CSV/TXT는 직접, XLSX/DOCX는 라이브러리 사용."""
    try:
        size = os.path.getsize(file_path)
        if size > max_size:
            return None

        ext = os.path.splitext(file_path)[1].lower()

        if ext in ('.csv', '.txt', '.tsv'):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read(max_size)

        if ext == '.xlsx':
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
                texts = []
                for ws in wb.worksheets[:3]:  # 최대 3개 시트
                    for row in ws.iter_rows(max_row=100, values_only=True):  # 최대 100행
                        texts.extend(str(cell) for cell in row if cell is not None)
                wb.close()
                return ' '.join(texts)
            except Exception as e:
                logger.warning(f"XLSX extraction failed for {file_path}: {e}")
                return None

        if ext == '.docx':
            try:
                import docx
                doc = docx.Document(file_path)
                return ' '.join(p.text for p in doc.paragraphs[:200])  # 최대 200 단락
            except Exception as e:
                logger.warning(f"DOCX extraction failed for {file_path}: {e}")
                return None

        # HWP and other binary formats: classify by filename only
        return None

    except Exception as e:
        logger.warning(f"Text extraction failed for {file_path}: {e}")
        return None
