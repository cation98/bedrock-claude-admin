---
name: excel
description: DB 데이터를 조회하여 Excel(.xlsx) 파일을 생성합니다. 안전 점검, TBM, 현장 데이터 등을 엑셀 파일로 내보냅니다.
---

# 엑셀 파일 생성 스킬

사용자가 엑셀 파일 생성을 요청하면 아래 절차를 따릅니다.

## 절차

1. **데이터 조회**: `psql $DATABASE_URL`로 필요한 데이터를 조회
2. **Python 스크립트 생성**: openpyxl을 사용하여 엑셀 파일 생성
3. **파일 저장**: `~/workspace/exports/` 디렉토리에 저장

## 구현 방법

Python의 `openpyxl` 라이브러리를 사용합니다. 먼저 설치 확인 후 스크립트를 실행합니다.

```bash
pip3 install openpyxl --quiet
```

## 엑셀 생성 템플릿

```python
import subprocess
import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime
import os

# 1. DB 조회
result = subprocess.run(
    ['psql', os.environ['DATABASE_URL'], '-t', '-A', '-F', ',', '-c', 'SELECT 쿼리'],
    capture_output=True, text=True
)
rows = [line.split(',') for line in result.stdout.strip().split('\n') if line]

# 2. 엑셀 생성
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "데이터"

# 헤더 스타일
header_font = Font(bold=True, color="FFFFFF", size=11)
header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
header_alignment = Alignment(horizontal="center", vertical="center")
thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin")
)

# 헤더 작성
headers = ["컬럼1", "컬럼2", "컬럼3"]
for col, header in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=header)
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = header_alignment
    cell.border = thin_border

# 데이터 작성
for row_idx, row_data in enumerate(rows, 2):
    for col_idx, value in enumerate(row_data, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)
        cell.border = thin_border

# 열 너비 자동 조정
for col in range(1, len(headers) + 1):
    ws.column_dimensions[get_column_letter(col)].width = 20

# 3. 저장
os.makedirs(os.path.expanduser("~/workspace/exports"), exist_ok=True)
filename = f"~/workspace/exports/{datetime.now().strftime('%Y%m%d_%H%M%S')}_export.xlsx"
wb.save(os.path.expanduser(filename))
print(f"엑셀 파일 생성 완료: {filename}")
```

## 엑셀 스타일 가이드
- 헤더: 파란 배경 + 흰색 볼드 텍스트
- 데이터: 테두리 적용, 숫자는 오른쪽 정렬
- 날짜: YYYY-MM-DD 형식
- 금액: 천 단위 쉼표 포맷
- 시트명: 한국어 사용 가능

## DB 조회 규칙
- 항상 `psql $DATABASE_URL` 사용
- CSV 형식 출력: `-t -A -F ','` 플래그 사용
- 대량 데이터는 `LIMIT` 적용

## 출력
- 엑셀 파일: `~/workspace/exports/YYYYMMDD_HHMMSS_[주제].xlsx`
- 사용자에게 파일 경로와 내용 요약 제공
