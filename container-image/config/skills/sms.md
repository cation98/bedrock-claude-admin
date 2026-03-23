---
name: sms
description: 사내 구성원에게 SMS를 발송합니다. 알람 분석 후 통보, 긴급 연락 등에 사용합니다.
---

# SMS 발송 스킬

## 사용 방법

SMS를 발송하려면 Auth Gateway API를 호출합니다.

## 발송 규칙
- **일일 10건** 발송 한도
- **사내 구성원 번호만** 발송 가능 (010-XXXX-XXXX)
- **80자 이내** 메시지
- 발송 전 반드시 **사용자에게 수신번호와 내용을 확인**받을 것
- 메시지 앞에 [Claude Code] 태그가 자동 추가됨

## 발송 코드

```python
import urllib.request
import json
import os

def send_sms(phone_number: str, message: str):
    """SMS 발송 — Auth Gateway 경유"""
    token = os.environ.get("AUTH_TOKEN", "")
    url = "http://auth-gateway.platform:8000/api/v1/sms/send"

    data = json.dumps({
        "phone_number": phone_number,
        "message": message
    }).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    })

    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())
```

## 사용량 확인

```python
def check_sms_usage():
    """오늘의 SMS 발송량 확인"""
    token = os.environ.get("AUTH_TOKEN", "")
    url = "http://auth-gateway.platform:8000/api/v1/sms/usage"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}"
    })
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())
```

## 중요
- 반드시 발송 전에 사용자에게 확인을 받으세요
- 스팸/대량 발송은 금지됩니다
- 모든 발송 내역은 감사 로그에 기록됩니다
