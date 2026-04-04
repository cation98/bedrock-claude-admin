# Office Hours Session: App Factory Design (2026-04-03) — UPDATED

## 3단계 배포 아키텍처 (확정)

1. **개발 모드**: 사용자 Pod 멀티포트 (기존 app_proxy.py)
2. **공유 모드** (Phase 1): 사용자 Pod에서 실행 + 포트 등록 + ACL/visibility + 조회수 카운팅
3. **상용 모드** (Phase 2): 경량 Pod 빈패킹 (node:alpine, python:slim, 앱당 50-100MB)

## 핵심 발견 (Eng Review)
- 디자인 문서 제안의 ~70%가 이미 코드에 구현됨
- apps.py(배포/ACL), app_proxy.py(프록시), app.py 모델, admin dashboard 모두 존재
- 두 프록시 경로는 의도적 분리: 개발(/app/) vs 배포(/apps/)

## Phase 1 실제 남은 작업 (4개)
1. DB 마이그레이션 (visibility + app_port 컬럼, app_views 테이블)
2. auth-check에 visibility='company' 분기
3. app_proxy.py 멀티포트 (포트 3000 하드코딩 제거)
4. 조회수 카운팅 (비동기 INSERT + 정적자산 필터)
+ pytest 설정 + 16개 테스트

## 앱 종류 (서버+정적 혼합)
서버: DB 실시간 대시보드, 엑셀 분석, 현장 모바일 웹앱, 공공API, 사내문서 연계
정적: 카드뉴스, 빌드된 대시보드

## Design Doc Location
~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260403-044504.md
