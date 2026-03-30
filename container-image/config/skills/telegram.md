---
name: telegram
description: 텔레그램 봇을 통해 특정 사용자에게 메시지를 발송합니다. 텔레그램 알림, 보고서 전달 등에 사용합니다.
---

# 텔레그램 메시지 발송 스킬

## 사용 방법

텔레그램 봇을 통해 등록된 사용자에게 메시지를 발송합니다.
사용자는 텔레그램에서 @sko_claude_bot 에게 사번을 등록해야 합니다.

## 발송 방법

```python
import urllib.request, json, os

def send_telegram(username, message):
    """사번으로 텔레그램 메시지 발송."""
    # Auth Gateway API를 통해 발송
    token = os.environ.get("CLAUDE_TOKEN", "")
    data = json.dumps({
        "username": username,
        "message": message
    }).encode()

    req = urllib.request.Request(
        "https://claude.skons.net/api/v1/telegram/send",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

# 사용 예시
send_telegram("N1102359", "보고서가 준비되었습니다. 확인해주세요.")
```

## 주의사항

- 수신자가 텔레그램 봇(@sko_claude_bot)에 사번을 등록해야 발송 가능
- 봇 주소: t.me/sko_claude_bot
- 대량 발송은 자제 (일일 한도 적용 가능)
