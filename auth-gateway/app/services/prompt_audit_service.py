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

from app.models.prompt_audit import PromptAuditSummary, PromptAuditFlag, PromptAuditConversation

logger = logging.getLogger(__name__)

# ── Pod 내부에서 실행할 프롬프트 추출 스크립트 ──
EXTRACT_SCRIPT = '''
import json, glob, os
prompts = []
for f in sorted(glob.glob("/home/node/.claude/projects/-home-node/*.jsonl"), key=os.path.getmtime):
    try:
        for line in open(f, encoding="utf-8"):
            try:
                obj = json.loads(line.strip())
                t = obj.get("type", "")
                if t not in ("user", "assistant"):
                    continue
                msg = obj.get("message", {})
                content = ""
                if isinstance(msg, dict):
                    c = msg.get("content", "")
                    if isinstance(c, list):
                        parts = []
                        for item in c:
                            if isinstance(item, dict):
                                parts.append(item.get("text", ""))
                        content = " ".join(parts)
                    elif isinstance(c, str):
                        content = c
                elif isinstance(msg, str):
                    content = msg
                if not content or len(content) < 3:
                    continue
                ts = obj.get("timestamp", "")
                session_id = obj.get("sessionId", os.path.basename(f).replace(".jsonl",""))
                prompts.append({"type": t, "text": content[:2000], "ts": ts, "sid": session_id})
            except:
                pass
    except:
        pass
print(json.dumps(prompts[-500:], ensure_ascii=False))
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
    "safety_mgmt": [
        "안전", "safety", "위험성", "사고", "재해", "점검", "안전관리",
        "보호구", "작업허가", "TBM", "MSDS", "위험물", "소방", "화재",
        "안전교육", "산업안전", "중대재해", "안전보건",
    ],
    "facility": [
        "시설", "설비", "facility", "장비", "기계", "유지보수", "정비",
        "플랜트", "배관", "전기", "계장", "공조", "HVAC", "건물",
        "토목", "건축", "자산", "도면",
    ],
    "quality": [
        "품질", "quality", "QC", "QA", "검사", "불량", "합격",
        "시험", "규격", "표준", "ISO", "인증", "측정", "공정",
        "스펙", "spec", "성적서",
    ],
    "business_analysis": [
        "업무", "프로세스", "효율", "개선", "KPI", "성과", "목표",
        "전략", "기획", "계획", "예산", "비용", "매출", "실적",
        "조직", "인사", "평가", "지표", "워크플로우",
    ],
    "scm": [
        "SCM", "구매", "조달", "발주", "납품", "재고", "물류",
        "공급망", "입고", "출고", "자재", "원자재", "협력업체",
        "vendor", "공급", "수요",
    ],
    "communication": [
        "이메일", "메일", "보고", "공문", "회의", "발표자료",
        "프레젠테이션", "PPT", "번역", "영작", "작성해", "초안",
        "문장", "편지", "안내문", "공지",
    ],
    "session_mgmt": [
        "최근작업", "최근 작업", "계속해", "이어서", "불러", "continue",
        "resume", "접속 url", "접속url", "실행 url", "실행url", "주소",
        "리셋", "다시 시작", "방금전", "하던거", "마저", "이전 작업",
        "기존 작업", "작업 보여", "찾아줘",
    ],
    "ui_ux": [
        "버튼", "레이아웃", "정렬", "디자인", "색상", "스타일",
        "팝업", "드롭다운", "필터", "탭", "아래에", "옆으로",
        "이동해", "만들어줘", "추가해줘", "위치를", "크기",
        "간격", "마진", "패딩", "폰트", "아이콘", "사이버틱",
        "컬럼명", "그리드", "화면", "보여줘", "안보여",
    ],
    "gis_mapping": [
        "지도", "맵핑", "위치", "좌표", "지역", "맵", "map",
        "거리별", "포인트", "GPS", "경도", "위도", "마커",
        "무선국", "국소",
    ],
    "fault_analysis": [
        "고장", "장애", "알람", "OOS", "발생건", "고장현황",
        "고장리스트", "fault", "alarm", "장애현황", "복구",
        "이벤트", "트러블",
    ],
    "file_ops": [
        "파일", "업로드", "다운로드", "첨부", "PDF", "pdf",
        "변환", "다운받", "올려", "내려", "xlsx", "hwp",
        "문서", "서식", "양식",
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
        today = datetime.now(timezone.utc).date()

        for pod in pods.items:
            pod_name = pod.metadata.name
            username = pod_name.replace("claude-terminal-", "").upper()

            # Pod에서 대화 추출 (user + assistant)
            prompts = self._extract_prompts_from_pod(pod_name, namespace)
            if not prompts:
                continue

            pods_scanned += 1

            # 대화 이력 저장 (upsert — 중복 무시)
            for entry in prompts:
                ts_str = entry.get("ts", "")
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
                except (ValueError, AttributeError):
                    ts_dt = None
                session_id = entry.get("sid", "")
                msg_type = entry.get("type", "user")

                existing_conv = db.query(PromptAuditConversation).filter(
                    PromptAuditConversation.username == username,
                    PromptAuditConversation.session_id == session_id,
                    PromptAuditConversation.message_type == msg_type,
                    PromptAuditConversation.timestamp == ts_dt,
                ).first()
                if not existing_conv:
                    db.add(PromptAuditConversation(
                        username=username,
                        session_id=session_id,
                        message_type=msg_type,
                        content=entry.get("text", ""),
                        timestamp=ts_dt,
                    ))

            # 분류 + 보안 검사 (user 메시지만)
            user_prompts = [p for p in prompts if p.get("type") == "user"]
            category_counter: Counter = Counter()
            char_total = 0
            flag_count = 0

            for prompt_data in user_prompts:
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
                    excerpt = text[:200] if len(text) > 200 else text
                    flag = PromptAuditFlag(
                        username=username,
                        category=violation["category"],
                        severity=violation["severity"],
                        prompt_excerpt=excerpt,
                        reason=violation["reason"],
                    )
                    db.add(flag)

            total_prompts += len(user_prompts)

            # 일별 요약 upsert
            existing = db.query(PromptAuditSummary).filter(
                PromptAuditSummary.username == username,
                PromptAuditSummary.audit_date == today,
            ).first()

            if existing:
                existing.total_prompts = len(user_prompts)
                existing.total_chars = char_total
                existing.category_counts = dict(category_counter)
                existing.flagged_count = flag_count
                existing.updated_at = datetime.now(timezone.utc)
            else:
                summary = PromptAuditSummary(
                    username=username,
                    audit_date=today,
                    total_prompts=len(user_prompts),
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
        # 시스템 메시지 제외
        if text.startswith("<") and ("command-name" in text or "local-command" in text or "task-notification" in text):
            return ["system_message"]

        matched: list[str] = []
        text_lower = text.lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    matched.append(category)
                    break  # 카테고리당 1회만 매칭

        # 단순 응답: 10자 이하 + 키워드 미매칭 → confirmation
        if not matched and len(text.strip()) <= 10:
            matched.append("confirmation")

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
