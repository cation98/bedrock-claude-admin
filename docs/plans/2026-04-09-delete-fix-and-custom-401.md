# 앱 삭제 502 수정 + 401 커스텀 에러 페이지

**Goal:** (1) delete-project 502 해결, (2) 미인증/미인가 접근 시 한국어 커스텀 에러 페이지 표시

---

## Task 1: delete-project 502 수정
- `_handle_apps_delete_project`에 try-except 래핑
- `_kill_port` 실패 시 무시, `shutil.rmtree` 실패 시 500 JSON 응답

## Task 2: 401/403 커스텀 에러 페이지
- `auth-gateway/app/static/files-unauthorized.html` 생성
- `file_share.py`에 에러 페이지 엔드포인트 추가
- files Ingress에 `custom-http-errors` + `default-backend` 어노테이션 추가
