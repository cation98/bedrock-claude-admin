# Bedrock API 요금 최적화 분석 보고서

**작성일**: 2026-04-24  
**작성자**: Claude Code (cation98)  
**세션**: 본 프로젝트 bedrock api 요금 최적화 방안 수립

---

## 목차

1. [현재 시스템 아키텍처 요약](#1-현재-시스템-아키텍처-요약)
2. [사용자별 커스텀 시스템 프롬프트 정책](#2-사용자별-커스텀-시스템-프롬프트-정책)
3. [실제 사용량 데이터 (30일)](#3-실제-사용량-데이터-30일)
4. [AWS Bedrock vs 어드민 금액 차이 분석](#4-aws-bedrock-vs-어드민-금액-차이-분석)
5. [Output 토큰 27배 편중 현상 분석](#5-output-토큰-27배-편중-현상-분석)
6. [Top 3 사용자 작업 패턴 정밀 분석](#6-top-3-사용자-작업-패턴-정밀-분석)
7. [미국 리전 전용 모델 도입 검토](#7-미국-리전-전용-모델-도입-검토)
8. [비용 최적화 권고 로드맵](#8-비용-최적화-권고-로드맵)

---

## 1. 현재 시스템 아키텍처 요약

### Bedrock 호출 경로 (2026-04-24 기준)

```
Console Pod (Claude Code CLI)
  → IRSA (직접)
  → ap-northeast-2 Bedrock endpoint
  → global.anthropic.claude-sonnet-4-6 (cross-region inference)

OnlyOffice AI Plugin
  → auth-gateway /api/v1/ai/chat/completions
  → bedrock_adapter.py
  → IRSA (직접)
  → Bedrock (추적 없음 ⚠️)

텔레그램 봇
  → auth-gateway
  → Bedrock (추적 여부 불명확 ⚠️)
```

### T20 Proxy 상태 (미배포)

`infra/k8s/pod-template.yaml:71` — `ANTHROPIC_BASE_URL`이 주석 처리된 상태:
```yaml
# - name: ANTHROPIC_BASE_URL
#   value: "http://auth-gateway.platform.svc.cluster.local/v1"
```

T20 proxy(`auth-gateway/app/routers/bedrock_proxy.py`)는 개발 완료되었으나 Console Pod에 연결되지 않음. Console Pod는 모두 Bedrock 직접 호출 중.

### 운영 모델 설정

| 위치 | 설정값 |
|------|--------|
| auth-gateway pod env | `BEDROCK_REGION=ap-northeast-2` |
| config.py | `bedrock_region = "us-east-1"` (불일치 ⚠️) |
| Console Pod Sonnet | `global.anthropic.claude-sonnet-4-6` |
| Console Pod Haiku | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |

---

## 2. 사용자별 커스텀 시스템 프롬프트 정책

모든 Console Pod 기동 시 `container-image/entrypoint.sh`가 `~/.claude/CLAUDE.md`를 동적으로 조합합니다.

### 공통 섹션 (모든 사용자)

| 섹션 | 내용 |
|------|------|
| `00-header.md` | 사내 플랫폼 안내, 보안 3대 금지(외부 전송/자격증명 노출/시스템 변경), `Always respond in Korean` |
| `10-security-rules.md` | 보안 등급(`{SECURITY_LEVEL}` 런타임 치환), 대용량 파일 SQLite 변환 강제 |
| `45-large-file-rules.md` | 대용량 파일 처리 보완 규칙 |
| `60-web-terminal.md` | 웹앱 보안(자체 인증 금지, API-only 서비스 금지, 데이터 우회 허브 금지), security_middleware 필수, 포트 3000 강제 |
| 사용자 프로필 | 사번, 이름, 직책, 부서 — 모든 세션 개인화 주입 |

### DB 권한에 따른 조건부 섹션

| 조건 | 추가 섹션 | 내용 |
|------|-----------|------|
| `DATABASE_URL` 설정 | `30-safety-db.md` | 안전관리 DB 접속법 + 주요 테이블 + 샘플 쿼리 |
| `TANGO_DB_PASSWORD` 설정 | `20-tango-db.md` + `25-opark-db.md` | TANGO 알람 DB + Opark 업무일지 DB |
| `DOCULOG_DB_PASSWORD` 설정 | `35-doculog-db.md` | Docu-Log 문서활동 분석 DB |
| SECURITY_LEVEL ≠ `basic` | `50-db-common-rules.md` | DB 공통 규칙 (psql-tango 전용 명령 강제 등) |
| 허용 DB 기준 필터링 | `40-keyword-mapping.md` | 업무 키워드 → DB 라우팅 매핑 |

### 사전 설치 커스텀 스킬

| 스킬 | 목적 |
|------|------|
| `/db` | DB 쿼리 가이드 |
| `/excel` | Excel/CSV → SQLite 변환 분석 |
| `/notify` | 플랫폼 알림 발송 |
| `/report` | 보고서 생성 |
| `/safety-auth-app` | 안전인증 앱 연동 |
| `/share` | EFS API 기반 파일 공유 |
| `/sms` | SMS 발송 (일일 10건 한도) |
| `/telegram` | 텔레그램 봇 연동 |
| `/webapp` | 웹앱 개발 가이드 (인증 헤더, 포트 3000) |

### 사전 설치 플러그인 (claude-plugins-official)

- `feature-dev`: code-architect, code-explorer, code-reviewer 에이전트
- `frontend-design`: UI 설계 스킬
- `superpowers`: brainstorming, systematic-debugging, TDD, using-git-worktrees, writing-plans 등

---

## 3. 실제 사용량 데이터 (30일)

**데이터 기간**: 2026-03-26 ~ 2026-04-24 (전체 서비스 기간과 동일)

### 30일 일별 추이

| 날짜 | Input 토큰 | Output 토큰 | 비용 USD | 비용 KRW | 활성 사용자 |
|------|-----------|------------|---------|---------|-----------|
| 2026-04-24 | 2,422 | 759,829 | $11.40 | ₩17,449 | 1 |
| 2026-04-23 | 64,701 | 3,912,671 | $58.88 | ₩90,090 | 10 |
| 2026-04-22 | 63,574 | 4,030,195 | $60.64 | ₩92,742 | 17 |
| 2026-04-21 | 80,748 | 5,039,674 | $75.84 | ₩115,991 | 27 |
| 2026-04-20 | 223,407 | 5,655,051 | $85.50 | ₩130,754 | 27 |
| 2026-04-19 | 167,910 | 1,883,752 | $28.75 | ₩43,976 | 5 |
| 2026-04-18 | 6,574 | 311,592 | $4.69 | ₩7,181 | 1 |
| 2026-04-17 | 50,922 | 2,838,939 | $42.74 | ₩65,387 | 25 |
| 2026-04-16 | 171,354 | 4,578,679 | $69.19 | ₩105,858 | 35 |
| 2026-04-15 | 162,684 | 3,837,854 | $58.06 | ₩88,760 | 22 |
| 2026-04-14 | 229,071 | 4,380,123 | $66.20 | ₩100,825 | 43 |
| 2026-04-13 | 145,708 | 3,313,928 | $50.13 | ₩76,380 | 31 |
| 2026-04-12 | 21,437 | 1,091,562 | $16.44 | ₩25,149 | 6 |
| 2026-04-11 | 21,043 | 946,842 | $14.27 | ₩21,826 | 3 |
| 2026-04-10 | 149,599 | 2,976,837 | $45.10 | ₩69,005 | 23 |
| 2026-04-09 | 154,903 | 3,589,029 | $54.30 | ₩83,078 | 24 |
| 2026-04-08 | 147,010 | 4,568,893 | $68.97 | ₩105,530 | 21 |
| 2026-04-07 | 127,732 | 3,731,466 | $56.36 | ₩86,223 | 19 |
| 2026-04-06 | 137,654 | 2,459,404 | $37.30 | ₩57,077 | 15 |
| 2026-04-05 | 24,439 | 513,719 | $7.78 | ₩11,902 | 2 |
| 2026-04-04 | 30,000 | 571,181 | $8.66 | ₩13,246 | 3 |
| 2026-04-03 | 144,598 | 2,416,594 | $36.68 | ₩56,122 | 19 |
| 2026-04-02 | 37,976 | 1,357,158 | $20.47 | ₩31,324 | 24 |
| 2026-04-01 | 98,257 | 1,497,950 | $22.76 | ₩34,831 | 18 |
| 2026-03-31 | 4,891 | 421,600 | $6.34 | ₩9,695 | 9 |
| 2026-03-30 | 327 | 88,835 | $1.33 | ₩2,039 | 3 |
| 2026-03-27 | 7,155 | 779,247 | $11.71 | ₩17,912 | 15 |

**전체 합계**: Input 2,477,178 토큰 / Output 67,562,544 토큰 / **$1,020.65 / ₩1,560,582** / 누적 사용자 122명

### Top 15 사용자 (전체 기간)

| 순위 | 사용자 | 비용 USD | 비용 KRW | Input | Output | 활성일 |
|------|--------|---------|---------|-------|--------|-------|
| 1 | N1001063 | $147.14 | ₩225,120 | 1,294,254 | 9,550,369 | 16일 |
| 2 | N1101943 | $143.41 | ₩219,397 | 326,616 | 9,496,122 | 18일 |
| 3 | N1102055 | $103.29 | ₩158,028 | 14,282 | 6,882,924 | 13일 |
| 4 | N1101050 | $81.42 | ₩124,573 | 123,715 | 5,403,231 | 17일 |
| 5 | N1103203 | $80.94 | ₩123,838 | 42,092 | 5,387,615 | 17일 |
| 6 | N1102359 | $75.61 | ₩115,685 | 110,785 | 5,018,662 | 26일 |
| 7 | N1001064 | $64.54 | ₩98,711 | 30,955 | 4,296,157 | 16일 |
| 8 | N1102099 | $62.37 | ₩95,381 | 7,703 | 4,156,137 | 9일 |
| 9 | N1101698 | $36.45 | ₩55,768 | 28,188 | 2,424,348 | 9일 |
| 10 | N1101464 | $32.52 | ₩49,754 | 235,246 | 2,120,826 | 11일 |

**Pareto 분석**: Top 3 사용자가 전체 비용의 **38.6%** ($393.84/$1,020.65) 차지

### 비용 구성 분석 (어드민 기준)

```
Input 비용:  2,477,178 토큰 × $3/MTok  =    $7.43  (0.7%)
Output 비용: 67,562,544 토큰 × $15/MTok = $1,013.44 (99.3%)
────────────────────────────────────────────────────────
총계:                                      $1,020.65
```

비용의 **99.3%가 output 토큰**에서 발생.

---

## 4. AWS Bedrock vs 어드민 금액 차이 분석

### 금액 현황

| 항목 | 금액 |
|------|------|
| AWS Bedrock 실제 청구 | ~₩3,000,000 |
| 어드민 대시보드 집계 | ₩1,560,582 |
| **미집계 차이** | **~₩1,439,418 (약 $940 USD)** |

### 원인 분석

#### 원인 1: OnlyOffice/bedrock_adapter 완전 미추적 (가장 큰 원인)

`/api/v1/ai/chat/completions` → `bedrock_adapter.py` → Bedrock 호출 경로에는 `token_usage_daily` 기록 코드가 **전혀 없음**.

```python
# auth-gateway/app/services/bedrock_adapter.py
usage = anthropic_resp.get("usage") or {}
# ← usage는 반환값 구성에만 사용, DB 저장 없음
```

OnlyOffice 사용자 전원의 AI 사용량이 AWS에서는 청구되지만 어드민에서는 0으로 표시됨.

#### 원인 2: Pod 삭제 시 마지막 Snapshot 이후 토큰 소실

`do_snapshot` 로직:
- 10분마다 **현재 Running 상태인 Pod**의 JSONL만 읽음
- 사용자 로그아웃(Pod 삭제) 시 마지막 snapshot 이후 최대 10분 데이터 소실
- 고강도 코드 생성 세션에서 10분 = 수십만 토큰에 해당

#### 원인 3: cache_read/creation 토큰 미집계

현재 grep 패턴:
```python
# admin.py L68-70
'for m in re.finditer(r\'"input_tokens":(\\d+)\',c):ti+=int(m.group(1))\n'
```

Bedrock이 청구하는 프롬프트 캐싱 토큰 필드가 누락됨:
- `cache_creation_input_tokens` — 캐시 생성 시 1.25× 요금
- `cache_read_input_tokens` — 캐시 읽기 시 0.1× 요금

#### 원인 4: KRW 환율 불일치

| 위치 | KRW 환율 |
|------|---------|
| `admin.py` | 1530 |
| `bedrock_proxy.py` | 1400 |

USD 환산 시 ~8.5% 차이 발생. USD 기준 gap이 더 크므로 환율은 부분적 원인에 불과.

#### 원인 5: 가격 정책 미분화

`admin.py`는 **모든 모델에 Sonnet 가격**을 적용:
```python
# admin.py L58-60
INPUT_PRICE = 3.0 / 1_000_000   # Sonnet 기준 고정
OUTPUT_PRICE = 15.0 / 1_000_000  # Sonnet 기준 고정
KRW_RATE = 1530
```

Haiku를 사용하는 경우(OnlyOffice 기본): 실제 청구($4/MTok output)와 추정($15/MTok output) 간 3.75× 차이.

---

## 5. Output 토큰 27배 편중 현상 분석

### 현상

```
전체 토큰 비율:
  Input:   2,477,178 토큰
  Output: 67,562,544 토큰
  비율: output이 27.3배 더 많음

극단적 사례:
  N1102055: input 14,282 vs output 6,882,924 → 482배
  N1102099: input  7,703 vs output 4,156,137 → 540배
```

정상적인 LLM 사용에서는 input >> output이 기본값 (입력 컨텍스트가 누적 증가).

### 원인 1: 설계 특성 — 아젠틱 코딩의 파일 Write

Claude Code 아젠틱 흐름에서 파일 Write는 output 토큰으로 청구됨:

```
사용자: "버튼 색상 바꿔줘"  (5 토큰)
Claude:
  1. Read file (4,692줄) → input 컨텍스트에 포함
  2. Write file (4,692줄 전체) → output 토큰으로 청구
  3. 응답 메시지 → output 토큰으로 청구

한 번의 수정 = ~58,650 output 토큰
하루 15회 반복 = ~879,750 output 토큰/일
```

이것은 코드 에디터 플랫폼의 **구조적 특성**이며 문제가 아님.

### 원인 2: JSONL grep의 Input 과소 집계 (측정 오류)

```python
# admin.py L66-72 — _collect_tokens_from_pod
script = (
    'for f in glob.glob("/home/node/.claude/projects/-home-node/*.jsonl"):\n'
    ' c=open(f).read()\n'
    ' for m in re.finditer(r\'"input_tokens":(\\d+)\',c):ti+=int(m.group(1))\n'
    ' for m in re.finditer(r\'"output_tokens":(\\d+)\',c):to+=int(m.group(1))\n'
    'print(f"{ti},{to}")'
)
```

JSONL의 스트리밍 이벤트 구조상 `"input_tokens"` 등장 횟수 < `"output_tokens"` 등장 횟수로 추정됨. 결과적으로 input이 실제보다 훨씬 작게 집계됨.

### 비용 계산에 미치는 영향

Input이 실제보다 30배 더 크다고 가정할 때:
```
추가 input 비용: (2.5M × 30) × $3/MTok = $217
현재 총계: $1,020
보정 후: $1,237

여전히 AWS 실제 $1,961과 $724 차이
→ input 집계 오류보다 OnlyOffice 미추적이 더 큰 원인
```

### 핵심 결론

input 집계 오류가 있지만, **input은 $3/MTok(저렴)이라 비용 영향이 제한적**. 정작 위험한 것은 측정 자체를 신뢰할 수 없어 쿼터 정책 수립과 Haiku/Sonnet 분기 판단이 불가능해지는 것.

---

## 6. Top 3 사용자 작업 패턴 정밀 분석

### N1001063 — 안전관리 서명앱 개발 ($147.14)

**작업 유형**: FastAPI + HTML/JS 안전관리 서명부 웹앱 반복 개발 (2FA SMS 인증 포함)

**주요 요청 패턴**:
- "접속QR코드 우측에 신규작성 버트 추가해줘" → 파일 전체 Read + Write
- "mms로 보내도록 수정해줘" → 파일 전체 Read + Write
- "sms워커 동작중인가요" → 디버깅

**현황**:
- 파일 규모: `signature_app.py` 4,692줄 (v0.7)
- 활성일: 16일, 평균 매일 작업
- 추정 하루 파일 Write 횟수: 10~20회 × 58,650 토큰 = 587,000~1,170,000 output 토큰/일

**비용 최적화 레버**: 4,692줄 파일의 시스템 프롬프트 캐싱 적용 시 input 비용 90% 절감 가능.

### N1101943 — 예산관리 대시보드 개발 ($143.41)

**작업 유형**: N/W팀 예산관리 웹 대시보드 (FastAPI + Excel eAcc.xlsx 연동, 다중 팀 탭)

**주요 요청 패턴**:
- "팀별 현황(공유용)에서 엑셀 다운로드 받을때 팀별 현황처럼..." → UI 수정
- "전체 현황과 전체 현황(공유용)에 월별 잔여금액도..." → 로직 추가
- "대시보드 재 시작 해주고, 주소를 보여줘" → 반복 재시작

**카테고리 분포** (4일 합산):
```
system_message: 194회  webapp: 167회  business_analysis: 100회
file_ops: 103회  coding: 88회   ui_ux: 90회
```

### N1102055 — NW 혁신팀 인력산출 모델링 ($103.29)

**작업 유형**: 조직 적정 인력 산출 분석 모델 개발 (Excel 입력 → 분석 → Excel 결과 출력)

**주요 요청 패턴**:
- "@(분석) NW혁신팀_업무현황_업로드용.xlsx 화일을 확인만 하고 나에게 어떻게 할지 물어봐"
- "종합적으로 정리해주는데 자세하게 해주고 표와 함께 그래프도 같이 있으면 좋겠어"
- "분석결과를 다시 보여줘" → 동일 분석 반복 출력

**카테고리 분포**:
```
data_analysis: 44회  business_analysis: 35회
webapp: 21회  coding: 18회  reporting: 18회
```

### 전체 카테고리 분포 (플랫폼 전체)

| 카테고리 | 건수 | 비고 |
|----------|------|------|
| system_message | 433 | 내부 메시지 (비용에 직접 기여) |
| data_analysis | 262 | Excel/DB 분석 → 대형 output |
| ui_ux | 260 | 화면 수정 → 파일 Write |
| webapp | 259 | 웹앱 개발 → 파일 Write |
| file_ops | 226 | 파일 읽기/쓰기 |
| coding | 191 | 코드 생성/수정 |
| business_analysis | 167 | 데이터 분석 보고서 |
| reporting | 147 | 결과 출력 → 대형 output |
| gis_mapping | 143 | GIS/지도 관련 |
| safety_mgmt | 132 | 안전관리 업무 |
| database | 121 | DB 쿼리 |
| fault_analysis | 102 | 고장 분석 |

---

## 7. 미국 리전 전용 모델 도입 검토

### 현재 구조

```
Console Pod → IRSA → ap-northeast-2 endpoint
  → global.anthropic.claude-sonnet-4-6 (cross-region inference)
  → 실제 추론: us-east-1 / us-west-2 / eu-west-1 중 AWS 자동 선택
```

`global.*` prefix는 이미 cross-region inference이므로 실제 처리는 미국/유럽에서 발생 중.

### US 리전 전용 모델 도입 시 비교

| 항목 | 현재 (`global.anthropic.*`) | US 전용 모델 |
|------|------|------|
| 호출 리전 | ap-northeast-2 | us-east-1 직접 지정 |
| 실제 추론 리전 | AWS 자동 (US/EU/APAC) | us-east-1 고정 |
| 레이턴시 | 이미 cross-region (~100~200ms) | 동일하거나 소폭 증가 |
| 데이터 주권 | AWS cross-region 이용약관 적용 | 미국 리전 한정 |
| 모델 품질 | Claude (코딩 최적화) | 모델별 상이 |

### 위험 요소 평가

| 위험 | 수준 | 상세 |
|------|------|------|
| 데이터 주권/컴플라이언스 | 🔴 높음 | SKO 직원 데이터, TANGO/Safety DB 쿼리 결과가 미국 리전 통과. 개인정보보호법 검토 필요 |
| 인프라 불일치 | 🟡 중간 | 현재 config.py(us-east-1) vs pod env(ap-northeast-2) 불일치 존재. us-east-1 전용 모델은 BEDROCK_REGION=us-east-1 강제 필요 + IRSA 권한 확장 |
| 모델 적합성 | 🟡 중간 | Claude Code CLI의 도구 호출, 장문 컨텍스트, 파일 편집 형식에 최적화 미검증 |
| 마이그레이션 복잡도 | 🟡 중간 | EKS, RDS, VPC 모두 ap-northeast-2. 리전 이전 없이 Bedrock 리전만 변경 가능하나 RDS 접근 지연 증가 |

### 결론

현 단계에서 US 전용 모델 도입보다 **Haiku 4.5 선택적 분기**가 더 안전하고 효과적:

```
Sonnet 4.6: $3 input / $15 output per MTok
Haiku 4.5:  $0.80 input / $4 output per MTok (output 73% 절감)

현재 output 비용 $1,013의 73% = $740 절감 가능
(Haiku로 분기 가능한 경량 작업 비율에 따라 달라짐)
```

---

## 8. 비용 최적화 권고 로드맵

### 즉시 조치 (1주 이내)

| 항목 | 내용 | 예상 효과 |
|------|------|---------|
| KRW 환율 통일 | `admin.py` 1530 → `bedrock_proxy.py` 1400과 일치 | 표시 정확도 개선 |
| config.py vs pod env 불일치 해소 | `BEDROCK_REGION=ap-northeast-2` → config.py 반영 | 추적 정확도 개선 |
| bedrock_adapter 토큰 추적 추가 | OnlyOffice 경로에 `token_usage_daily` INSERT 추가 | ~$940 미집계 가시화 |

### 단기 (2~4주)

| 항목 | 내용 | 예상 효과 |
|------|------|---------|
| Pod 삭제 시 Final Snapshot | Pod 종료 hook에서 마지막 토큰 집계 강제 실행 | 10분 gap 소실 방지 |
| `token_usage_daily`에 model_id 컬럼 추가 | Sonnet vs Haiku 분리 집계 | 모델별 비용 가시성 확보 |
| Haiku 분기 로직 구현 (T20 proxy) | 단순 질문/DB 쿼리는 Haiku, 코딩은 Sonnet | output 비용 40~70% 절감 가능 |
| T20 proxy 활성화 (`ANTHROPIC_BASE_URL` 주석 해제) | Console Pod를 auth-gateway proxy 경유 | 중앙 집계 + 쿼터 적용 가능 |

### 중장기 (1~2개월)

| 항목 | 내용 | 예상 효과 |
|------|------|---------|
| 프롬프트 캐싱 구현 (T20 proxy) | `bedrock_proxy.py`의 `bedrock_body` 구성에 `cache_control` 복원 | input 비용 90% 절감 (대형 파일 반복 Read 시) |
| CloudWatch 연동 | Bedrock API별 token 메트릭 → 어드민 연동 | 실시간 정확한 집계 |
| 사용자별 Haiku 비율 리포트 | 어드민에 모델 분기 현황 시각화 | 최적화 진행 모니터링 |
| Budget Alert 설정 | AWS Budgets로 일/주/월 비용 임계값 알림 | 비용 폭발 사전 차단 |

### 비용 최적화 우선순위 요약

```
현재 측정 기준 월 추정 비용: ~$1,020 (실제 AWS: ~$1,961)

1순위 — 측정 정확도 개선 (비용 절감 전 가시성 확보 필수)
  → bedrock_adapter 추적, model_id 분리, Final Snapshot hook

2순위 — Haiku 분기 (가장 큰 절감 레버)
  → output 비용 $1,013의 40~70% 절감 목표

3순위 — 프롬프트 캐싱 (대형 파일 반복 작업 유저 대상)
  → N1001063 패턴: 4,692줄 파일 반복 Read → input 90% 절감
  → T20 proxy의 cache_control 구현 필요

4순위 — 쿼터 정책 강화
  → T20 proxy 활성화 후 _check_user_quota 실제 enforcement
  → 현재는 proxy 미활성으로 쿼터 체크 우회 가능
```

---

## 부록: 핵심 파일 위치

| 파일 | 역할 |
|------|------|
| `auth-gateway/app/routers/admin.py` | 토큰 스냅샷 + 어드민 API. `_collect_tokens_from_pod`, `do_snapshot`, 가격 상수 |
| `auth-gateway/app/routers/bedrock_proxy.py` | T20 draft proxy. 모델별 가격 책정, prompt caching 미구현 |
| `auth-gateway/app/services/bedrock_adapter.py` | OnlyOffice AI 경로. 토큰 추적 없음 |
| `auth-gateway/app/core/scheduler.py` | `token_snapshot_loop` (600s), `prompt_audit_loop` (1800s) |
| `auth-gateway/app/models/token_usage.py` | `TokenUsageDaily` (model_id 컬럼 없음), `TokenUsageHourly` |
| `infra/k8s/pod-template.yaml` | Console Pod 환경변수. `ANTHROPIC_BASE_URL` 주석처리 상태 |
| `container-image/entrypoint.sh` | CLAUDE.md 동적 조합 + T20 proxy JWT 교환 |
| `container-image/config/claude-md-sections/` | 00~60 섹션 파일 (보안/DB/웹앱 정책) |
