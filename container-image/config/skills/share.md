---
name: share
description: 현재 작업 중인 스킬이나 유용한 프롬프트를 다른 사용자와 공유합니다.
---

# 스킬 공유

사용자가 만든 유용한 스킬, 프롬프트, 코드 조각을 팀과 공유합니다.

## 공유 방법

```python
import urllib.request
import json
import os

def share_skill(title: str, description: str, content: str, category: str = "skill"):
    """스킬을 중앙 저장소에 제출 (관리자 승인 후 전사 배포)"""
    url = "http://auth-gateway.platform:8000/api/v1/skills/submit"
    data = json.dumps({
        "title": title,
        "description": description,
        "content": content,
        "category": category  # skill, claude-md, prompt, snippet
    }).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ.get('AUTH_TOKEN', '')}"
    })
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    return result
```

## 카테고리
- `skill` -- Claude Code 슬래시 명령어 (.md 형식)
- `claude-md` -- CLAUDE.md 설정/정책
- `prompt` -- 유용한 프롬프트 템플릿
- `snippet` -- 재사용 가능한 코드 조각

## 주의사항
- 제출된 내용은 관리자 검토 후 승인되어야 공유됩니다
- 비밀번호, API 키, 개인정보를 포함하지 마세요
- 외부 URL이나 curl 명령이 포함된 스킬은 자동 거부됩니다
