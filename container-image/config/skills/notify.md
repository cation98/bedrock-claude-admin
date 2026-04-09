---
name: notify
description: 다른 사용자의 터미널에 실시간 메시지를 전송합니다. 작업 공지, 긴급 안내 등에 사용합니다.
---

# 터미널 실시간 알림 스킬

## 사용 방법

다른 사용자의 터미널에 실시간으로 메시지를 표시합니다.

```bash
# 특정 사용자에게 메시지 전송
/notify N1101512 "파일 공유 요청이 있습니다"

# 제목 + 메시지
/notify N1101512 --subject "긴급 안내" "시스템 점검 예정입니다"
```

## 구현 방법

Auth Gateway의 broadcast API를 호출합니다:

```bash
curl -sf -X POST "${AUTH_GATEWAY_URL}/api/v1/admin/broadcast" \
  -H "Authorization: Bearer ${CLAUDE_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "제목",
    "message": "메시지 내용",
    "targets": ["N1101512"],
    "channels": ["websocket"]
  }'
```

## 발송 채널

| 채널 | 설명 | 사용 시점 |
|------|------|---------|
| `websocket` | 터미널에 실시간 표시 | 상대방이 접속 중일 때 |
| `mms` | MMS 문자 발송 | 상대방이 오프라인일 때 |

두 채널을 동시에 사용할 수 있습니다: `"channels": ["websocket", "mms"]`

## 대상 지정

- `"targets": ["N1101512"]` — 특정 사용자
- `"targets": ["N1101512", "N1102055"]` — 여러 사용자
- `"targets": []` — 현재 활성 세션의 모든 사용자

## 터미널 표시 형태

```
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📢 관리자 공지  제목
  메시지 내용
  🕐 2026-04-09 15:55 UTC | Otto AI Platform
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 주의사항

- admin 권한이 있는 사용자만 broadcast API 호출 가능
- 일반 사용자는 이 스킬을 사용할 수 없습니다
- MMS 채널은 일일 발송 한도가 적용됩니다
