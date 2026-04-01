# Web App Deploy Platform Design

**Date**: 2026-04-01
**Status**: Implementation
**Session**: webapp-deploy-platform

## Overview

사용자가 Claude Code 터미널(개인 Pod)에서 웹앱을 개발하고, `/deploy` 명령으로 별도 상시 App Pod에 배포하여 팀원에게 공유하는 플랫폼.

## Architecture

```
[개발] 개인 Pod (claude-sessions)
  ~/apps/my-app/ (.git 버전관리)
  → 포트 3000에서 테스트
  → /deploy 명령 실행

[배포] App Pod (claude-apps)
  EFS deployed/{app}/current/ 마운트 (읽기전용)
  EFS deployed/{app}/data/ 마운트 (읽기+쓰기, 업로드파일)
  → claude.skons.net/apps/{username}/{app-name}/

[접근] 팀원 브라우저
  → SSO 인증
  → ACL 검증 (Ingress auth-url → auth-gateway)
  → App Pod로 프록시
```

## Key Decisions

1. **배포 모델**: 개인 Pod 개발 → 별도 상시 Pod 배포 (C 모델)
2. **스케일**: 팀 공유(5-20명) + 사내 보안 수준
3. **인증**: 앱별 ACL (개별 사용자 지정) + 배포자 DB 권한 상속
4. **리소스**: CPU 0.5코어 / Memory 1Gi per App Pod
5. **버전관리**: 로컬 Git (외부 서버 불필요), /deploy가 자동 commit/tag
6. **배포 권한**: Admin이 사용자별 can_deploy_apps 승인

## Data Model

### deployed_apps
| Column | Type | Description |
|--------|------|-------------|
| id | serial PK | |
| owner_username | varchar(50) | 배포자 사번 |
| app_name | varchar(100) | 앱 이름 |
| app_url | varchar(255) | /apps/{username}/{app-name}/ |
| pod_name | varchar(100) | app-{username}-{app-name} |
| status | varchar(20) | running/stopped |
| version | varchar(50) | git tag or auto-generated |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### app_acl
| Column | Type | Description |
|--------|------|-------------|
| id | serial PK | |
| app_id | int FK | → deployed_apps |
| granted_username | varchar(50) | 접근 허용 사번 |
| granted_by | varchar(50) | 권한 부여자 |
| granted_at | timestamptz | |
| revoked_at | timestamptz | NULL=활성 |

### Users table extension
| Column | Type | Description |
|--------|------|-------------|
| can_deploy_apps | boolean | 배포 권한 (default: false) |

## API Endpoints

### App Deployment
- `POST /api/v1/apps/deploy` — 앱 배포
- `DELETE /api/v1/apps/{app_name}` — 앱 삭제
- `POST /api/v1/apps/{app_name}/redeploy` — 재배포
- `POST /api/v1/apps/{app_name}/rollback` — 롤백
- `GET /api/v1/apps/my` — 내 배포 앱 목록
- `GET /api/v1/apps/shared` — 나에게 공유된 앱 목록

### ACL Management
- `GET /api/v1/apps/{app_name}/acl` — 허용 사용자 목록
- `POST /api/v1/apps/{app_name}/acl` — 사용자 추가
- `DELETE /api/v1/apps/{app_name}/acl/{username}` — 사용자 회수

### Auth Middleware
- `GET /api/v1/apps/auth-check` — Ingress auth-url (SSO + ACL)

### User Search
- `GET /api/v1/users/search?q=` — 승인된 사용자 검색

### Admin
- `PATCH /api/v1/users/{username}` — can_deploy_apps 설정 (기존 API 확장)
- `GET /api/v1/admin/apps` — 전체 배포 앱 목록 (관리자)

## Security

### 4-Layer Isolation
1. **Network**: NetworkPolicy — App Pod간 통신 차단, ingress-nginx만 허용
2. **Storage**: EFS subPath — 사용자별 격리, 커널 레벨 마운트
3. **Auth**: SSO + ACL — 앱별 접근 사용자 제어
4. **Data**: security_policy — 배포자 기준 DB 자격증명 주입

### claude-apps NetworkPolicy
- Ingress: ingress-nginx → port 3000 only
- Egress: DNS + RDS(5432) + auth-gateway(8000) only
- Pod-to-Pod: 차단

## App Runtime Container

- Base: node:22-bookworm-slim (~200MB)
- Python3 + FastAPI + pandas + openpyxl + psycopg2
- Auto-detect: main.py → uvicorn, package.json → npm start, start.sh → bash
- Resources: requests(250m/512Mi), limits(500m/1Gi)

## Implementation Phases

### Phase 1: Infrastructure (선행)
1. claude-apps namespace + NetworkPolicy
2. app-runtime container image
3. Users.can_deploy_apps column
4. deployed_apps + app_acl tables

### Phase 2: Deploy Engine (핵심)
5. AppDeployService
6. Deploy API endpoints
7. Dynamic Service + Ingress creation
8. Snapshot management (copy + symlink + rollback)

### Phase 3: Auth/ACL
9. /apps/auth-check middleware
10. ACL CRUD API
11. User search API

### Phase 4: Frontend
12. Admin: can_deploy_apps toggle
13. Admin: deployed apps monitoring
14. Hub: app management + ACL UI + shared apps

### Phase 5: Pod CLI
15. /deploy script (git auto + API call)
16. /undeploy, /deploy --rollback
17. entrypoint.sh integration
