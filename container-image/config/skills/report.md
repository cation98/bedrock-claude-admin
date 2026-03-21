---
name: report
description: DB 데이터를 분석하여 한국어 보고서를 생성합니다. 안전 점검, TBM, 현장 데이터 등을 조회하고 마크다운 보고서로 작성합니다.
---

# 보고서 생성 스킬

사용자가 보고서 생성을 요청하면 아래 절차를 따릅니다.

## 절차

1. **데이터 소스 확인**: `psql $DATABASE_URL`로 관련 테이블과 데이터를 조회
2. **데이터 분석**: 기간별, 항목별, 부서별 등 요청된 기준으로 집계
3. **보고서 작성**: 마크다운 형식으로 ~/workspace/reports/ 디렉토리에 저장

## 보고서 형식

```markdown
# [보고서 제목]

**작성일**: YYYY-MM-DD
**작성자**: Claude Code (AI 자동 생성)
**데이터 기간**: YYYY-MM-DD ~ YYYY-MM-DD

## 1. 요약 (Executive Summary)
- 핵심 수치 3~5개를 bullet point로 정리

## 2. 상세 분석
### 2.1 [분석 항목 1]
- 표, 수치, 비교 데이터 포함

### 2.2 [분석 항목 2]
- 추세, 변화율, 전월 대비 등

## 3. 시각화
- Python matplotlib/pandas로 차트 생성 코드 제공
- 차트 이미지 파일로 저장

## 4. 제언 및 권고사항
- 데이터 기반 개선점 제시
```

## DB 조회 규칙
- 항상 `psql $DATABASE_URL -c "쿼리"` 사용
- 대량 데이터는 `LIMIT` 적용
- 날짜 필터 우선 적용으로 쿼리 최적화
- 결과를 `~/workspace/reports/` 에 저장

## 출력
- 보고서 파일: `~/workspace/reports/YYYY-MM-DD_[주제].md`
- 차트 코드: 필요 시 Python 스크립트 포함
- 다운로드 안내: "파일 다운로드: http://[현재호스트]:8080/reports/파일명.md"
  (포트 8080에서 파일 서버가 ~/workspace/ 를 서빙합니다)
