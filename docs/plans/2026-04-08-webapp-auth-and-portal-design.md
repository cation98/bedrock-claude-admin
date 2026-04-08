# Webapp Authentication & Management Portal Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 웹앱 접근 시 SSO+2FA 인증을 강제하고, 앱 소유자가 개인/팀/지역/직책/전체 단위로 접근 권한을 관리하며, DAU/MAU 통계를 확인할 수 있는 포털을 제공한다.

**Architecture:** 기존 SSO+2FA 백엔드를 재사용하는 경량 로그인 페이지 + AppACL 모델을 5-type grant로 확장 + static HTML 기반 관리 포털.

**Tech Stack:** FastAPI, vanilla JS (static HTML), PostgreSQL, 기존 SSO/2FA/JWT 인프라

---

## 1. 문제 정의

현재 `/apps/*` 경로에 JWT 없이 접근하면 401 JSON만 반환되어 사용자가 빈 화면을 보게 됨. 또한 접근제어가 개인(ACL) 또는 전체(company) 두 가지만 지원되어 팀/지역/직책 단위 관리가 불가능함.

## 2. 경량 로그인 페이지 (`/webapp-login`)

### 흐름

```
접근자가 /apps/{owner}/{app}/ 요청
    -> NGINX auth-check -> JWT 없음
    -> 302 Redirect: /webapp-login?return_url=/apps/{owner}/{app}/
    -> 로그인 페이지 렌더링
       |- "OOO의 앱에 접근하려면 로그인이 필요합니다"
       |- 사번 + 비밀번호 입력
       +- SSO 인증 -> 2FA SMS -> 코드 입력
    -> JWT 발급 (claude_token 쿠키 설정)
    -> return_url로 자동 복귀
    -> auth-check 재검증 -> ACL 통과 -> 웹앱 표시
```

### 기존 코드 재사용

| 구성요소 | 재사용 | 신규 |
|---------|--------|------|
| SSO 인증 API (`/api/v1/auth/login`) | O | |
| 2FA 발송/검증 API | O | |
| JWT 발급 (`create_access_token`) | O | |
| 로그인 HTML 페이지 | | `webapp-login.html` 신규 |
| 세션 생성 (`POST /sessions`) | 호출 안 함 | |

기존 `login.html`은 로그인 성공 후 `POST /api/v1/sessions`를 호출하여 Pod을 생성. 새 `webapp-login.html`은 JWT 쿠키만 설정하고 `return_url`로 리다이렉트. 백엔드 API는 동일하고 프론트엔드 HTML만 다름.

### auth-check 수정

현재: JWT 없으면 `401 JSON` 반환
변경: JWT 없으면 `302 /webapp-login?return_url={original_url}` 리다이렉트

## 3. ACL 모델 확장 (5-Type Grant)

### 스키마 변경

```sql
-- 기존: granted_username (개인 전용)
-- 변경: grant_type + grant_value (5가지 타입 지원)

ALTER TABLE app_acl ADD COLUMN grant_type VARCHAR(10) NOT NULL DEFAULT 'user';
ALTER TABLE app_acl ADD COLUMN grant_value VARCHAR(100) NOT NULL DEFAULT '';
UPDATE app_acl SET grant_type = 'user', grant_value = granted_username;
ALTER TABLE app_acl DROP COLUMN granted_username;
CREATE INDEX ix_app_acl_grant ON app_acl(app_id, grant_type, grant_value, revoked_at);
```

### Grant Types

| grant_type | grant_value 예시 | auth-check 검증 로직 |
|------------|-----------------|---------------------|
| `user` | `N1102359` | `request_user.username == grant_value` |
| `team` | `안전기술팀` | `request_user.team_name == grant_value` |
| `region` | `서울본사` | `request_user.region_name == grant_value` |
| `job` | `팀장` | `request_user.job_name == grant_value` |
| `company` | `*` | 인증된 사용자면 무조건 통과 |

### 조직 구조 및 포함 관계

```
company > region > team > user
                   job (조직 횡단, 독립 축)
```

- region을 지정하면 해당 지역의 모든 team 포함
- job은 모든 조직을 관통 (예: "팀장"이면 전 조직의 팀장)

### auth-check 검증 순서

```
1. 관리자(admin) -> 허용
2. 소유자(owner) -> 허용
3. ACL 순회 (하나라도 매칭되면 허용):
   - company 타입 존재? -> 허용
   - region 매칭? -> 허용
   - team 매칭? -> 허용
   - job 매칭? -> 허용
   - user 매칭? -> 허용
4. 모두 불일치 -> 403
```

### 기존 visibility 필드와의 통합

`visibility="company"`는 ACL에 `{grant_type: company, grant_value: *}` 레코드를 자동 생성.
`visibility` 컬럼은 유지하되 ACL과 동기화.

### 권한 매트릭스 예시

접근자 프로필:
- 김부장: N1001063, 안전기술팀, 서울본사, 부장
- 이팀장: N1101698, 안전기술팀, 서울본사, 팀장
- 박대리: N1102359, 안전관리팀, 서울본사, 대리
- 최과장: N1103906, 생산기술팀, 울산공장, 과장
- 정팀장: N1104002, 생산관리팀, 울산공장, 팀장

복합 ACL: `[{team: 안전기술팀}, {job: 팀장}, {user: N1103906}]`
- 김부장: 허용 (team 매칭)
- 이팀장: 허용 (team 매칭)
- 박대리: 차단
- 최과장: 허용 (user 매칭)
- 정팀장: 허용 (job 매칭)

## 4. 웹앱 관리 포털 (`/portal`)

### 페이지 구조

```
/portal/
|- 내 앱 목록 (카드 형태)
|   |- 앱 이름, URL, 상태(running/stopped), 버전
|   |- 오늘 DAU / 이번 달 MAU 뱃지
|   +- [ACL 관리] [통계] [삭제] 버튼
|
+- 앱 상세 -> /portal/apps/{app_name}/
    |- ACL 관리 탭
    |   |- 현재 허용 목록 (grant_type 아이콘 + 값)
    |   |- 추가: 타입 선택 드롭다운
    |   |   |- user -> 사번 검색 자동완성
    |   |   |- team -> 팀 목록 드롭다운
    |   |   |- region -> 지역 목록 드롭다운
    |   |   |- job -> 직책 목록 드롭다운
    |   |   +- company -> 확인만
    |   +- 삭제: X 버튼 (revoke)
    |
    +- 통계 탭
        |- DAU 차트 (최근 30일 일별 순방문자)
        |- MAU 표시 (이번 달 순방문자)
        +- 최근 접속자 목록 (사번, 이름, 팀, 시간)
```

### 기술 구현

auth-gateway의 `static/` 디렉토리에 단일 HTML + vanilla JS.
기존 `login.html`과 동일한 패턴. 별도 빌드 도구 없음.

### DAU/MAU 계산

기존 `AppView` 테이블에서 집계. 별도 테이블 불필요.

```sql
-- DAU
SELECT COUNT(DISTINCT viewer_user_id) FROM app_views
WHERE app_id = ? AND DATE(viewed_at) = ?

-- MAU
SELECT COUNT(DISTINCT viewer_user_id) FROM app_views
WHERE app_id = ? AND viewed_at >= DATE_TRUNC('month', NOW())
```

## 5. API 설계

### 신규 API

| 엔드포인트 | 메서드 | 용도 | 인증 |
|-----------|--------|------|------|
| `/api/v1/apps/{name}/stats` | GET | DAU/MAU + 최근 접속자 | JWT (소유자/관리자) |
| `/api/v1/apps/acl-options` | GET | team/region/job 드롭다운 목록 | JWT |
| `/portal/` | GET | 포털 HTML 페이지 | JWT (리다이렉트) |
| `/webapp-login` | GET | 경량 로그인 HTML | 없음 |

### 수정 API

| 엔드포인트 | 변경 내용 |
|-----------|----------|
| `GET /api/v1/apps/auth-check` | 401 -> 302 리다이렉트. ACL 검증 5-type 확장 |
| `GET /api/v1/apps/my` | DAU/MAU 뱃지 데이터 추가 |
| `POST /api/v1/apps/{name}/acl` | `grant_type` + `grant_value` 수용 |
| `GET /api/v1/apps/{name}/acl` | grant_type별 그룹핑 반환 |
| `DELETE /api/v1/apps/{name}/acl/{id}` | ACL ID 기반 삭제 (username 대신) |

### acl-options 응답 예시

```json
{
  "teams": ["안전기술팀", "안전관리팀", "생산기술팀", ...],
  "regions": ["서울본사", "울산공장", ...],
  "jobs": ["부장", "팀장", "과장", "대리", ...]
}
```

User 테이블에서 DISTINCT 집계.

## 6. 파일 변경 범위

### 신규 파일

| 파일 | 용도 |
|------|------|
| `auth-gateway/app/static/webapp-login.html` | 경량 로그인 |
| `auth-gateway/app/static/portal.html` | 웹앱 관리 포털 |
| `auth-gateway/app/routers/portal.py` | 포털 API |

### 수정 파일

| 파일 | 변경 |
|------|------|
| `auth-gateway/app/models/app.py` | AppACL: grant_type, grant_value 추가, granted_username 제거 |
| `auth-gateway/app/routers/apps.py` | auth-check 302 리다이렉트, ACL 5-type 검증, /my DAU/MAU |
| `auth-gateway/app/main.py` | portal 라우터 등록 |

### 변경하지 않는 것

- SSO/2FA 백엔드 API
- JWT 발급/검증
- app_proxy
- admin-dashboard
- 기존 /login 페이지 (Pod 사용자용 유지)
