"""프롬프트 감사 서비스 — Pod 대화 분석 + 분류 + 보안 탐지."""

import json
import re
import logging
from datetime import date, datetime, timezone
from collections import Counter
from typing import Optional

from kubernetes import client, config
from kubernetes.stream import stream
from sqlalchemy.orm import Session as DbSession

from app.models.prompt_audit import PromptAuditSummary, PromptAuditFlag

logger = logging.getLogger(__name__)

# ── Pod 내부에서 실행할 프롬프트 추출 스크립트 ──
EXTRACT_SCRIPT = '''
import json, glob, sys
prompts = []
for f in glob.glob("/home/node/.claude/projects/-home-node/*.jsonl"):
    try:
        for line in open(f):
            try:
                obj = json.loads(line.strip())
                msg = obj.get("message", {})
                if obj.get("type") == "user" or obj.get("type") == "human" or obj.get("role") == "user":
                    content = ""
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text","") for c in content if isinstance(c, dict))
                    elif isinstance(msg, str):
                        content = msg
                    if content and len(content) > 5:
                        ts = obj.get("timestamp", "")
                        prompts.append({"text": content[:500], "ts": ts})
            except:
                pass
    except:
        pass
import json as j
print(j.dumps(prompts[-200:]))
'''

# ── 카테고리 분류 키워드 ──
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "data_analysis": [
        "분석", "통계", "차트", "그래프", "pandas", "데이터", "시각화",
        "matplotlib", "plot", "csv", "엑셀", "excel", "집계", "추이",
        "평균", "합계", "최대", "최소", "상관", "분포",
    ],
    "coding": [
        "함수", "클래스", "구현", "코드", "리팩터", "모듈", "패키지",
        "import", "def ", "class ", "변수", "타입", "인터페이스",
        "에러", "버그", "수정", "디버그", "테스트", "단위테스트",
    ],
    "database": [
        "SQL", "쿼리", "테이블", "psql", "SELECT", "INSERT", "UPDATE",
        "DELETE", "JOIN", "WHERE", "인덱스", "스키마", "마이그레이션",
        "데이터베이스", "DB", "postgres", "RDS",
    ],
    "reporting": [
        "보고서", "리포트", "report", "요약", "정리", "문서화",
        "발표", "슬라이드", "표", "양식", "서식",
    ],
    "webapp": [
        "FastAPI", "웹", "대시보드", "deploy", "배포", "서버",
        "API", "엔드포인트", "라우터", "프론트엔드", "React",
        "HTML", "CSS", "JavaScript", "포트", "앱",
    ],
    "infra": [
        "쿠버네티스", "K8s", "Pod", "Docker", "컨테이너",
        "AWS", "terraform", "인프라", "노드", "클러스터",
        "배포", "CI/CD", "파이프라인",
    ],
    "documentation": [
        "설명", "알려줘", "무엇", "어떻게", "왜", "의미",
        "이해", "개념", "아키텍처", "구조", "흐름",
    ],
}

# ── 보안 위반 패턴 ──
SECURITY_PATTERNS: dict[str, dict] = {
    "personal_info_request": {
        "severity": "high",
        "patterns": [
            r"주민등록번호|주민번호|resident.?number",
            r"비밀번호.{0,10}(알려|보여|출력|확인)",
            r"신용카드|카드번호|card.?number",
            r"계좌번호|bank.?account",
        ],
        "reason": "개인정보 요청 시도",
    },
    "system_escape": {
        "severity": "critical",
        "patterns": [
            r"sudo\s+",
            r"rm\s+-rf\s+/",
            r"chmod\s+777",
            r"/etc/(passwd|shadow|sudoers)",
            r"(reverse|bind)\s*shell",
            r"nc\s+-[le]",
        ],
        "reason": "시스템 탈출/권한 상승 시도",
    },
    "data_exfiltration": {
        "severity": "high",
        "patterns": [
            r"외부.{0,10}(전송|업로드|보내)",
            r"curl.{0,30}(upload|post|put).{0,30}(http|ftp)",
            r"(유출|반출|외부전송)",
            r"base64.{0,20}(encode|decode).{0,30}(send|post|curl)",
        ],
        "reason": "데이터 유출 시도 의심",
    },
    "policy_violation": {
        "severity": "medium",
        "patterns": [
            r"(해킹|크래킹|익스플로잇|exploit)",
            r"(악성코드|malware|ransomware|trojan)",
            r"(피싱|phishing|스미싱)",
            r"(불법|탈법).{0,10}(접근|침입|우회)",
        ],
        "reason": "보안 정책 위반 키워드",
    },
}

# 보안 패턴 사전 컴파일 (re.IGNORECASE)
_COMPILED_SECURITY: dict[str, dict] = {}
for _cat, _info in SECURITY_PATTERNS.items():
    _COMPILED_SECURITY[_cat] = {
        "severity": _info["severity"],
        "reason": _info["reason"],
        "compiled": [re.compile(p, re.IGNORECASE) for p in _info["patterns"]],
    }


class PromptAuditService:
    """프롬프트 감사 서비스."""

    def __init__(self):
        # K8s 클라이언트 초기화 (admin.py 모듈 레벨에서 이미 로드됨)
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.v1 = client.CoreV1Api()

    # ── 공개 메서드 ──

    def collect_and_analyze(self, db: DbSession, namespace: str = "claude-sessions") -> dict:
        """모든 실행 중인 Pod의 프롬프트를 수집·분석·저장.

        Returns:
            {"pods_scanned": int, "prompts_analyzed": int, "flags_created": int}
        """
        pods = self.v1.list_namespaced_pod(
            namespace=namespace,
            label_selector="app=claude-terminal",
            field_selector="status.phase=Running",
        )

        total_prompts = 0
        total_flags = 0
        pods_scanned = 0
        today = date.today()

        for pod in pods.items:
            pod_name = pod.metadata.name
            username = pod_name.replace("claude-terminal-", "").upper()

            # Pod에서 프롬프트 추출
            prompts = self._extract_prompts_from_pod(pod_name, namespace)
            if not prompts:
                continue

            pods_scanned += 1

            # 분류 + 보안 검사
            category_counter: Counter = Counter()
            char_total = 0
            flag_count = 0

            for prompt_data in prompts:
                text = prompt_data.get("text", "")
                char_total += len(text)

                # 카테고리 분류
                categories = self._classify_prompt(text)
                for cat in categories:
                    category_counter[cat] += 1

                # 미분류 프롬프트도 카운트
                if not categories:
                    category_counter["other"] += 1

                # 보안 위반 검사
                violations = self._check_security(text)
                for violation in violations:
                    flag_count += 1
                    total_flags += 1
                    # 프라이버시 보호: 200자로 잘라서 저장
                    excerpt = text[:200] if len(text) > 200 else text
                    flag = PromptAuditFlag(
                        username=username,
                        category=violation["category"],
                        severity=violation["severity"],
                        prompt_excerpt=excerpt,
                        reason=violation["reason"],
                    )
                    db.add(flag)

            total_prompts += len(prompts)

            # 일별 요약 upsert
            existing = db.query(PromptAuditSummary).filter(
                PromptAuditSummary.username == username,
                PromptAuditSummary.audit_date == today,
            ).first()

            if existing:
                existing.total_prompts = len(prompts)
                existing.total_chars = char_total
                existing.category_counts = dict(category_counter)
                existing.flagged_count = flag_count
                existing.updated_at = datetime.now(timezone.utc)
            else:
                summary = PromptAuditSummary(
                    username=username,
                    audit_date=today,
                    total_prompts=len(prompts),
                    total_chars=char_total,
                    category_counts=dict(category_counter),
                    flagged_count=flag_count,
                )
                db.add(summary)

        db.commit()

        return {
            "pods_scanned": pods_scanned,
            "prompts_analyzed": total_prompts,
            "flags_created": total_flags,
        }

    def get_summary(
        self,
        db: DbSession,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> dict:
        """기간별 프롬프트 카테고리 사용 추이 요약."""
        query = db.query(PromptAuditSummary)
        if date_from:
            query = query.filter(PromptAuditSummary.audit_date >= date_from)
        if date_to:
            query = query.filter(PromptAuditSummary.audit_date <= date_to)

        rows = query.order_by(
            PromptAuditSummary.audit_date.desc(),
            PromptAuditSummary.username,
        ).all()

        # 사용자별 집계
        users: dict[str, dict] = {}
        category_totals: Counter = Counter()
        daily_trend: dict[str, dict] = {}  # date_str -> {category: count}

        for row in rows:
            # 사용자별 합산
            if row.username not in users:
                users[row.username] = {
                    "username": row.username,
                    "total_prompts": 0,
                    "total_chars": 0,
                    "flagged_count": 0,
                    "category_counts": Counter(),
                }
            u = users[row.username]
            u["total_prompts"] += row.total_prompts or 0
            u["total_chars"] += row.total_chars or 0
            u["flagged_count"] += row.flagged_count or 0
            cats = row.category_counts or {}
            for cat, cnt in cats.items():
                u["category_counts"][cat] += cnt
                category_totals[cat] += cnt

            # 일별 추이
            date_str = row.audit_date.isoformat()
            if date_str not in daily_trend:
                daily_trend[date_str] = Counter()
            for cat, cnt in cats.items():
                daily_trend[date_str][cat] += cnt

        # Counter → dict 변환
        user_list = []
        for u in users.values():
            u["category_counts"] = dict(u["category_counts"])
            user_list.append(u)

        return {
            "date_range": {
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None,
            },
            "users": user_list,
            "category_totals": dict(category_totals),
            "daily_trend": [
                {"date": d, "categories": dict(cats)}
                for d, cats in sorted(daily_trend.items())
            ],
        }

    def get_flags(
        self,
        db: DbSession,
        severity: Optional[str] = None,
        reviewed: Optional[bool] = None,
        limit: int = 50,
    ) -> list[dict]:
        """보안 위반 플래그 목록 조회."""
        query = db.query(PromptAuditFlag)
        if severity:
            query = query.filter(PromptAuditFlag.severity == severity)
        if reviewed is not None:
            query = query.filter(PromptAuditFlag.reviewed == reviewed)

        flags = query.order_by(PromptAuditFlag.flagged_at.desc()).limit(limit).all()

        return [
            {
                "id": f.id,
                "username": f.username,
                "flagged_at": f.flagged_at.isoformat() if f.flagged_at else None,
                "category": f.category,
                "severity": f.severity,
                "prompt_excerpt": f.prompt_excerpt,
                "reason": f.reason,
                "reviewed": f.reviewed,
                "reviewed_by": f.reviewed_by,
                "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
            }
            for f in flags
        ]

    @staticmethod
    def review_flag(db: DbSession, flag_id: int, reviewer: str) -> dict:
        """보안 플래그 검토 완료 처리."""
        flag = db.query(PromptAuditFlag).filter(PromptAuditFlag.id == flag_id).first()
        if not flag:
            return {"error": "flag not found"}

        flag.reviewed = True
        flag.reviewed_by = reviewer
        flag.reviewed_at = datetime.now(timezone.utc)
        db.commit()

        return {
            "id": flag.id,
            "reviewed": True,
            "reviewed_by": reviewer,
            "reviewed_at": flag.reviewed_at.isoformat(),
        }

    # ── 내부 메서드 ──

    def _extract_prompts_from_pod(self, pod_name: str, namespace: str) -> list[dict]:
        """Pod 내부에서 JSONL 프롬프트를 추출."""
        try:
            resp = stream(
                self.v1.connect_get_namespaced_pod_exec,
                pod_name, namespace,
                command=["python3", "-c", EXTRACT_SCRIPT],
                container="terminal",
                stderr=False, stdin=False, stdout=True, tty=False,
            )
            raw = resp.strip()
            if not raw:
                return []
            # JSON 파싱 시도, 실패 시 single-quote → double-quote 변환
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Python repr 형식(single quotes) 대응: 안전한 문자열 치환
                fixed = raw.replace("'", '"')
                data = json.loads(fixed)
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"프롬프트 추출 실패 ({pod_name}): {e}")
            return []

    @staticmethod
    def _classify_prompt(text: str) -> list[str]:
        """프롬프트를 카테고리 키워드로 분류 (복수 카테고리 가능)."""
        matched: list[str] = []
        text_lower = text.lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    matched.append(category)
                    break  # 카테고리당 1회만 매칭
        return matched

    @staticmethod
    def _check_security(text: str) -> list[dict]:
        """보안 위반 패턴 검사 — re.IGNORECASE로 매칭."""
        violations: list[dict] = []
        for category, info in _COMPILED_SECURITY.items():
            for pattern in info["compiled"]:
                if pattern.search(text):
                    violations.append({
                        "category": category,
                        "severity": info["severity"],
                        "reason": info["reason"],
                    })
                    break  # 카테고리당 1회만 기록
        return violations
