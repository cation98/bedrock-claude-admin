---
name: mms
description: 사내 구성원에게 MMS를 발송합니다. 장문 메시지, 보고서 요약, 긴급 안내 등에 사용합니다.
---

# MMS 발송 스킬

## 사용 방법

MMS를 발송하려면 Auth Gateway API를 호출합니다.

## 발송 규칙
- **일일 10건** 발송 한도
- **사내 구성원 번호만** 발송 가능 (010-XXXX-XXXX)
- **2000자 이내** 메시지 (SMS 80자 대비 장문 가능)
- **제목 40자 이내** (선택사항)
- 발송 전 반드시 **사용자에게 수신번호와 내용을 확인**받을 것
- 메시지 앞에 [Claude Code] 태그가 자동 추가됨
- **관리자 승인 필요**: `can_send_mms` 권한이 없으면 403 오류 발생

## 발송 코드

```python
import urllib.request
import json
import os

def send_mms(phone_number: str, message: str, subject: str = ""):
    """MMS 발송 — Auth Gateway 경유"""
    token = os.environ.get("AUTH_TOKEN", "")
    url = "http://auth-gateway.platform:8000/api/v1/mms/send"

    body = {
        "phone_number": phone_number,
        "message": message,
    }
    if subject:
        body["subject"] = subject

    data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    })

    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())
```

## 사용량 확인

```python
def check_mms_usage():
    """오늘의 MMS 발송량 확인"""
    token = os.environ.get("AUTH_TOKEN", "")
    url = "http://auth-gateway.platform:8000/api/v1/mms/usage"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}"
    })
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())
```

## SMS vs MMS 선택 기준

| 항목 | SMS | MMS |
|------|-----|-----|
| 최대 글자수 | 80자 | 2000자 |
| 제목 | 없음 | 선택 (40자 이내) |
| 용도 | 짧은 알림 | 장문 보고서, 상세 안내 |
| 권한 | `can_send_sms` | `can_send_mms` |

## 중요
- 반드시 발송 전에 사용자에게 확인을 받으세요
- 스팸/대량 발송은 금지됩니다
- 모든 발송 내역은 감사 로그에 기록됩니다
- 권한이 없으면 관리자(플랫폼 담당)에게 `can_send_mms` 권한 부여를 요청하세요
