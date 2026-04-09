# Session: 2026-04-04 — Token Aggregation Bugfix

## 발견
token_usage_daily와 token_usage_hourly에 저장되는 값이 **누적 스냅샷**(세션 시작 이후 전체 토큰)임을 확인.
월별/트렌드 집계에서 SUM을 사용하여 누적값을 중복 합산 → 실제의 7~17배 과대계상.

### 증거
- 호정원: SUM=2,026,313 vs MAX=119,189 (17x 과대)
- 최종언: SUM=1,783,180 vs MAX=254,740 (7x), 실제 오늘 사용량=0 (전일 대비 증분 없음)

## 수정 (ed7a1f1)
- 월별 사용자: `func.sum` → `func.max - func.min` (월간 증분)
- 일별 트렌드: `func.sum` → `func.max` (사용자당 최종 누적)
- 월별 트렌드: `func.sum` → `func.max - func.min`
- 일별 상세: 누적임을 명시하는 note 필드 추가

## 이미지
auth-gateway: token-fix-20260404-1948 (sha256:7e80386d...)

## 교훈
_collect_tokens_from_pod가 Pod jsonl에서 전체 토큰을 합산하여 반환 → 이것이 DB에 그대로 저장됨.
집계 시 SUM이 아닌 MAX-MIN(증분) 또는 MAX(최종값)를 사용해야 함.
