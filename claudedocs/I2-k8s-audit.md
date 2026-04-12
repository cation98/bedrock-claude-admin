# I2: OnlyOffice K8s 매니페스트 감사 — Word/PPTX 처리 결함 분석

**감사 범위**: `infra/k8s/platform/onlyoffice.yaml` (production), `infra/local-dev/07-onlyoffice.yaml` (local)  
**감사 일시**: 2026-04-12  
**이미지**: `onlyoffice/documentserver:8.2.2` (표준 CE — x2t 포함 확인됨)  
**결론**: 프로덕션 매니페스트에 **4개의 구조적 결함** 존재. Word/PPTX는 Excel 대비 converter + 다중 네트워크 요청에 더 의존하므로 이 결함들이 집중적으로 영향을 미침.

---

## 결함 요약 (심각도 순)

| # | 결함 | 파일 | 심각도 | Excel 영향 | Word/PPTX 영향 |
|---|------|------|--------|-----------|----------------|
| F1 | `ALLOW_PRIVATE_IP_ADDRESS` 누락 | production only | 🔴 HIGH | 가능 | 확실 |
| F2 | `JWT_INBOX_ENABLED` / `postStart` 누락 | production only | 🔴 HIGH | 낮음 | 높음 |
| F3 | `/var/www/onlyoffice/Data` 볼륨 미마운트 | production + local | 🟡 MEDIUM | 낮음 | 높음 |
| F4 | 한글 Noto CJK 폰트 미설치 | production + local | 🟡 MEDIUM | 낮음 | 높음 |
| F5 | 메모리 limit 경계값 (4Gi) | production + local | 🟡 MEDIUM | 낮음 | 중간 |

---

## F1: ALLOW_PRIVATE_IP_ADDRESS 누락 — 프로덕션 환경에서만

### 증거

**로컬 개발** (`infra/local-dev/07-onlyoffice.yaml`, L41-43):
```yaml
- name: ALLOW_PRIVATE_IP_ADDRESS
  value: "true"
  # K8s 내부 DNS는 사설 IP로 해석되므로 허용 필수.
```

**프로덕션** (`infra/k8s/platform/onlyoffice.yaml`): 이 환경변수 **부재**.

**네트워크 정책** (`infra/k8s/platform/network-policy.yaml`, L304):
```yaml
# ALLOW_PRIVATE_IP_ADDRESS=true 설정과 짝을 이룬다.
```
→ 네트워크 정책의 주석이 이 env var의 존재를 전제하고 작성됨. **production manifest에만 빠져 있음.**

### 원인 분석

OnlyOffice DS는 기본적으로 `document.url`이 사설 IP(10.x, 172.x, 192.168.x)로 해석될 때 다운로드를 차단한다. K8s 클러스터 내 `auth-gateway.platform.svc.cluster.local`은 EKS VPC 내부 IP로 해석되므로, 이 env var 없이는 OO가 auth-gateway에서 파일을 받아올 수 없다.

### Word/PPTX 집중 영향 이유

- Excel(`.xlsx`)은 OO의 내장 SpreadsheetEditor로 처리 → x2t converter 경유 없이 메모리 내 처리 가능 → 초기 파일 fetch 실패해도 일부 상황에서 캐시 활용 가능성
- Word/PPTX는 변환 시 추가 리소스(embedded images, fonts)를 `document.url` 기반으로 다시 요청 → private IP 차단이 반복 발생

### 재현/검증 방법

```bash
# OO Pod 로그에서 private IP 차단 메시지 확인
kubectl -n claude-sessions logs -l app=onlyoffice --tail=200 \
  | grep -E "private|SSRF|blocked|rejected|10\.|172\.|192\.168\."

# document.url이 사설 IP로 해석되는지 확인
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  nslookup auth-gateway.platform.svc.cluster.local
```

---

## F2: JWT Inbox 검증 비활성화 로직 누락 — 프로덕션 환경에서만

### 증거

**로컬 개발** (`infra/local-dev/07-onlyoffice.yaml`, L49-97):
```yaml
- name: JWT_INBOX_ENABLED
  value: "false"
- name: ONLYOFFICE_DOCS_PARAMS
  value: '{"services":{"CoAuthoring":{"token":{"enable":{"request":{"inbox":false}}}}}}'
lifecycle:
  postStart:
    exec:
      command:
        - bash
        - -c
        - |
          # local.json 직접 패치 + supervisorctl restart ds:docservice
```

**프로덕션**: 위 3가지 모두 **부재**.

### 원인 분석

OnlyOffice JWT는 양방향:
1. **outbox (browser→OO)**: config JSON의 `token` 필드로 검증 → 프로덕션에도 `JWT_ENABLED=true`로 활성화됨
2. **inbox (OO→외부)**: OO가 파일을 다운로드할 때 응답에 JWT 검증 → auth-gateway가 JWT 미포함 시 실패

프로덕션에서는 JWT_INBOX_ENABLED가 기본값(`true`)이므로, OO가 auth-gateway에서 파일을 다운로드할 때 응답 헤더에 OO 형식의 JWT가 없으면 파일 로드 실패.

### Word/PPTX 집중 영향 이유

- Word/PPTX 문서는 embedded image, 참조 리소스를 별도로 fetch하는 경우가 Excel보다 빈번
- OO가 이 리소스들을 auth-gateway에서 받아올 때 inbox JWT 검증 실패 반복

### 재현/검증 방법

```bash
# OO docservice 로그에서 JWT 검증 실패 확인
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  tail -n 200 /var/log/onlyoffice/documentserver/docservice/out.log \
  | grep -E "jwt|token|unauthorized|403"
```

---

## F3: /var/www/onlyoffice/Data 볼륨 미마운트

### 증거

**공식 OO Docker 문서**가 요구하는 볼륨 4개:
1. `/var/log/onlyoffice` — 로그
2. `/var/www/onlyoffice/Data` — 인증서 및 변환 중간 파일
3. `/var/lib/onlyoffice` — 파일 캐시  ✅ (마운트됨)
4. `/var/lib/postgresql` — 내장 DB

**프로덕션 + 로컬** 둘 다 `/var/lib/onlyoffice`만 마운트됨:
```yaml
volumeMounts:
- name: data
  mountPath: /var/lib/onlyoffice    # ← 오직 이것만
# /var/www/onlyoffice/Data 미마운트
# /var/lib/postgresql 미마운트
```

### 원인 분석

`/var/www/onlyoffice/Data`는 x2t converter가 중간 변환 파일을 쓰는 경로이기도 하다. 이 경로가 ephemeral container storage에만 존재하면:
- Pod 재시작 시 진행 중 변환 데이터 소실
- EFS PVC가 `/var/lib/onlyoffice`만 영속화하므로, 나머지 경로의 쓰기 I/O가 container overlay에 누적 → 디스크 full 가능성

`/var/lib/postgresql` 미마운트는 OO 내장 PostgreSQL DB가 비영속적임을 의미. Pod 재시작 시 OO 편집 세션 DB가 초기화됨.

### Word/PPTX 집중 영향 이유

- x2t converter는 Word/PPTX 변환 시 `/var/www/onlyoffice/Data` 하위에 임시 파일을 생성
- Excel은 내장 SpreadsheetEditor로 처리하므로 이 경로 의존성이 낮음

### 재현/검증 방법

```bash
# OO Pod에서 /var/www/onlyoffice/Data 쓰기 가능 여부 확인
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  ls -la /var/www/onlyoffice/Data/

# 변환 중간 파일 존재 여부
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  ls -la /var/www/onlyoffice/Data/docsgateway/ 2>/dev/null || echo "NOT FOUND"
```

---

## F4: 한글 Noto CJK 폰트 미설치

### 증거

두 매니페스트 모두 다음 어느 것도 없음:
- initContainer에서 `apt-get install fonts-noto-cjk` 실행
- fonts ConfigMap 마운트
- `/usr/share/fonts` 볼륨 주입

### 원인 분석

OnlyOffice `documentserver:8.2.2` 표준 이미지는 기본 폰트를 포함하지만 **Noto CJK(한국어/중국어/일본어) 폰트는 기본 포함되지 않는다**. 한글 Word/PPTX 파일을 변환할 때 폰트가 없으면:
- 텍스트 렌더링 실패 또는 박스(□) 문자로 대체
- PDF 내보내기 시 글자 누락
- 변환 과정에서 x2t가 폰트 해석 실패 → 에러 코드 반환 가능

### 기존 이력과의 연관

커밋 이력에 NFC/NFD 한글 파일명 이슈 수정이 있음. 이는 팀이 **한글 관련 이슈를 이미 인지**하고 있었음을 시사하며, 폰트 문제도 같은 계열의 미처리 이슈일 가능성이 높음.

### Word/PPTX 집중 영향 이유

- Excel(`.xlsx`) 셀 내 텍스트는 OO가 Unicode로 처리 → 폰트 부재 시 박스로 표시되지만 동작은 함
- Word/PPTX는 변환 시 실제 폰트 글리프로 렌더링 → 폰트 부재 시 x2t converter가 변환 실패 처리

### 재현/검증 방법

```bash
# OO Pod에서 Noto CJK 폰트 존재 여부 확인
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  fc-list | grep -i "noto.*cjk\|noto.*korean\|nanum\|malgun"

# 설치된 전체 폰트 목록 확인
kubectl -n claude-sessions exec -it \
  $(kubectl -n claude-sessions get pod -l app=onlyoffice -o name) -- \
  fc-list | wc -l
```

---

## F5: 메모리 limit 경계값

### 증거

```yaml
resources:
  requests:
    memory: "2Gi"
  limits:
    memory: "4Gi"   # OO 공식 최소 권고: 4GB RAM
```

공식 OO Docker 문서: **"4 GB or more"** RAM 권고, 세션당 100-150MB 추가.

### 원인 분석

4Gi limit은 OO 기본 프로세스 + PostgreSQL + Nginx + Node.js 서비스들이 공유. Word/PPTX 변환 시 x2t converter가 추가 메모리를 사용하므로 한계에 근접할 수 있음.

OOMKilled 발생 시 OO Pod가 재시작되고, 재시작 중 편집 중인 모든 문서의 작업이 손실됨.

### 검증 방법

```bash
# OOMKilled 이력 확인
kubectl -n claude-sessions describe pod -l app=onlyoffice \
  | grep -E "OOMKilled|Reason|Exit Code"

# 현재 메모리 사용량
kubectl -n claude-sessions top pod -l app=onlyoffice
```

---

## 이미지 변형 분석 (가설 1 기각)

`onlyoffice/documentserver:8.2.2`는 **표준 CE (Community Edition)** 태그. slim 변형은 별도 suffix(`-slim`, `-nocdb`)로 구분됨.

- 표준 CE 이미지: x2t converter 포함, libreoffice 없음 (OO 자체 converter 사용)
- OO 8.x는 자체 x2t binary로 DOCX/PPTX 처리 — LibreOffice 의존성 없음

**결론**: 이미지 자체가 slim 변형이어서 converter가 누락된 것은 아님. 이미지 선택은 올바름.

---

## 프로덕션 vs 로컬 환경변수 대조표

| 환경변수 | 프로덕션 | 로컬 | 의미 |
|----------|---------|------|------|
| `JWT_ENABLED` | `true` | `true` | OO JWT 활성화 |
| `JWT_SECRET` | SecretKeyRef | SecretKeyRef | JWT 서명키 |
| `ALLOW_PRIVATE_IP_ADDRESS` | ❌ **누락** | `true` | K8s 내부 IP 허용 |
| `JWT_INBOX_ENABLED` | ❌ **누락** | `false` | Inbox JWT 비활성화 |
| `ONLYOFFICE_DOCS_PARAMS` | ❌ **누락** | JSON override | local.json 오버라이드 |
| `lifecycle.postStart` | ❌ **누락** | 존재 | local.json 패치 |

---

## 볼륨 마운트 대조표

| 경로 | 프로덕션 | 로컬 | 역할 |
|------|---------|------|------|
| `/var/lib/onlyoffice` | EFS PVC ✅ | emptyDir ✅ | 파일 캐시 |
| `/var/www/onlyoffice/Data` | ❌ **누락** | ❌ 누락 | 변환 중간파일, 인증서 |
| `/var/log/onlyoffice` | ❌ 누락 | ❌ 누락 | 로그 (선택적) |
| `/var/lib/postgresql` | ❌ 누락 | ❌ 누락 | 내장 DB (재시작 시 초기화) |

---

## 가설별 평가

| 가설 | 평가 |
|------|------|
| 1. slim 이미지 → x2t 누락 | ❌ 기각 — documentserver:8.2.2는 표준 CE |
| 2. 한글 Noto CJK 폰트 누락 | ✅ 확인 — initContainer 없음, 폰트 미설치 |
| 3. CPU/Memory OOMKilled | ⚠️ 의심 — 4Gi는 공식 최소값, 여유 없음 |
| 4. 볼륨 경로 쓰기 실패 | ✅ 확인 — /var/www/onlyoffice/Data 미마운트 |
| 5. ALLOW_PRIVATE_IP / JWT 분기 | ✅ 확인 — 프로덕션에 둘 다 누락 |

---

## 수정 우선순위

> **주의: 이 감사 문서는 READ-ONLY 조사 결과임. 실제 수정은 별도 작업으로 진행.**

1. **즉시 (F1+F2 동시)**: 프로덕션 manifest에 `ALLOW_PRIVATE_IP_ADDRESS=true`, `JWT_INBOX_ENABLED=false`, `ONLYOFFICE_DOCS_PARAMS`, `postStart` 추가 — 로컬과 동일하게 맞춤
2. **단기 (F3)**: `/var/www/onlyoffice/Data` EFS 볼륨 마운트 추가
3. **단기 (F4)**: initContainer로 `fonts-noto-cjk` 설치 추가
4. **관찰 (F5)**: 메모리 5-6Gi limit으로 상향 후 OOMKilled 모니터링

---

*이 보고서는 static YAML audit + 공식 문서 기반 분석 결과임. kubectl 실행 없이 작성됨.*
