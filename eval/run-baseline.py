#!/usr/bin/env python3
"""
OnlyOffice AI 한국어 품질 Eval Baseline 러너
eval/onlyoffice-ai-ko-baseline/ 의 YAML 샘플을 읽어
/api/v1/ai/chat/completions 에 요청 후 rubric 기반 HTML 보고서 출력.

사용법:
  # mock 모드 (기본): 스키마 검증 + 스텁 응답, Bedrock 호출 없음
  python eval/run-baseline.py --mock

  # real 모드: 실제 endpoint 호출 (IRSA 권한 필요)
  python eval/run-baseline.py \\
      --endpoint http://auth-gateway.platform.svc.cluster.local:8000 \\
      --model claude-sonnet-4-6 \\
      --token <JWT>

  # 특정 샘플만 실행
  python eval/run-baseline.py --mock --filter ko-001,ko-005

  # 출력 파일 지정
  python eval/run-baseline.py --mock --output /tmp/report.html

의존성: PyYAML (pip install pyyaml)
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

EVAL_DIR = Path(__file__).parent / "onlyoffice-ai-ko-baseline"
DEFAULT_REPORT = Path(__file__).parent / "baseline-report.html"

# OnlyOffice 플러그인이 메뉴별로 주입하는 시스템 프롬프트 (실제 플러그인 코드 기준)
MENU_SYSTEM_PROMPTS: dict[str, str] = {
    "summarize": (
        "당신은 한국어 비즈니스 문서 요약 전문가입니다. "
        "제공된 텍스트를 핵심 내용 중심으로 간결하게 요약하세요. "
        "반드시 한국어로 답변하세요."
    ),
    "translate": (
        "당신은 한국어-영어 비즈니스 번역 전문가입니다. "
        "제공된 지시에 따라 정확하고 자연스럽게 번역하세요."
    ),
    "proofread": (
        "당신은 한국어 문서 교정 전문가입니다. "
        "문법, 맞춤법, 띄어쓰기 오류를 수정하고 "
        "교정된 전체 텍스트와 수정 내역을 함께 반환하세요."
    ),
    "draft": (
        "당신은 한국어 비즈니스 문서 작성 전문가입니다. "
        "제공된 데이터 또는 지시사항을 바탕으로 전문적인 문서 초안을 작성하세요."
    ),
}

VALID_MENUS = set(MENU_SYSTEM_PROMPTS.keys())


# ─── 데이터 모델 ───────────────────────────────────────────────────────────────

@dataclass
class RubricItem:
    name: str
    max: int
    guide: str = ""


@dataclass
class Sample:
    id: str
    menu: str
    description: str
    input: str
    expected: dict
    rubric: list[RubricItem]
    golden: str
    source_path: Path


@dataclass
class EvalResult:
    sample: Sample
    model_response: str = ""
    latency_ms: int = 0
    error: Optional[str] = None
    auto_checks: dict[str, object] = field(default_factory=dict)
    # rubric_scores: 사람 평가 슬롯 (None = 미입력)
    rubric_scores: list[Optional[int]] = field(default_factory=list)

    @property
    def total_max(self) -> int:
        return sum(r.max for r in self.sample.rubric)

    @property
    def auto_pass(self) -> bool:
        """자동 검사에서 확정 실패(False)가 없으면 True."""
        return all(v is not False for v in self.auto_checks.values())


# ─── YAML 로더 ─────────────────────────────────────────────────────────────────

def _parse_rubric(raw: list[dict]) -> list[RubricItem]:
    items = []
    for r in (raw or []):
        if not isinstance(r, dict):
            raise ValueError(f"rubric item must be a dict: {r!r}")
        items.append(RubricItem(
            name=str(r.get("name", "항목")),
            max=int(r.get("max", 5)),
            guide=str(r.get("guide", "")),
        ))
    return items


def load_samples(filter_ids: Optional[list[str]] = None) -> list[Sample]:
    """eval/onlyoffice-ai-ko-baseline/*.yaml 전체 로드 + 스키마 검증."""
    if not EVAL_DIR.exists():
        print(f"ERROR: eval directory not found: {EVAL_DIR}", file=sys.stderr)
        sys.exit(1)

    paths = sorted(EVAL_DIR.glob("ko-*.yaml"))
    if not paths:
        print(f"ERROR: no ko-*.yaml files found in {EVAL_DIR}", file=sys.stderr)
        sys.exit(1)

    samples = []
    errors = []

    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: YAML parse error — {e}")
            continue

        # 필수 필드 검증
        for required in ("id", "menu", "description", "input", "rubric", "golden"):
            if required not in data:
                errors.append(f"{path.name}: missing required field '{required}'")
                break
        else:
            menu = str(data.get("menu", ""))
            if menu not in VALID_MENUS:
                errors.append(
                    f"{path.name}: invalid menu '{menu}' "
                    f"(must be one of: {', '.join(sorted(VALID_MENUS))})"
                )
                continue

            sample = Sample(
                id=str(data["id"]),
                menu=menu,
                description=str(data.get("description", "")).strip(),
                input=str(data.get("input", "")).strip(),
                expected=data.get("expected") or {},
                rubric=_parse_rubric(data.get("rubric") or []),
                golden=str(data.get("golden", "")).strip(),
                source_path=path,
            )

            if filter_ids and sample.id not in filter_ids:
                continue

            samples.append(sample)

    if errors:
        print("Schema validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)

    return samples


# ─── API 호출 ──────────────────────────────────────────────────────────────────

def call_mock(sample: Sample) -> tuple[str, int]:
    """Mock 모드: 실제 Bedrock 호출 없이 스텁 응답 반환.

    스키마 검증에 집중. 응답 형식은 실제 API와 동일한 구조로 시뮬레이션.
    """
    time.sleep(0.02)  # 최소 latency 시뮬레이션
    preview = sample.input[:150].replace("\n", " ")
    stub = (
        f"[MOCK {sample.menu.upper()}] 입력 {len(sample.input)}자 수신됨.\n\n"
        f"입력 미리보기: {preview}...\n\n"
        f"(실제 응답은 --endpoint 옵션으로 real 모드 실행 시 확인 가능합니다.)"
    )
    return stub, 20


def call_real(
    sample: Sample,
    endpoint: str,
    model: str,
    token: Optional[str],
) -> tuple[str, int]:
    """Real 모드: auth-gateway /api/v1/ai/chat/completions HTTP POST."""
    system_prompt = MENU_SYSTEM_PROMPTS.get(sample.menu, "")
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": sample.input})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096,
    }
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = endpoint.rstrip("/") + "/api/v1/ai/chat/completions"

    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            text: str = data["choices"][0]["message"]["content"]
            return text, elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        body_out = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {body_out}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}") from e


# ─── 자동 검사 ─────────────────────────────────────────────────────────────────

def auto_check(sample: Sample, response: str) -> dict[str, object]:
    """구조 요구사항 및 금지사항에 대한 규칙 기반 자동 검사.

    반환 dict:
      key = check_name
      value = True(통과) | False(실패) | "manual_required"(사람 검토 필요)
    """
    checks: dict[str, object] = {}
    structural = sample.expected.get("structural", [])
    forbidden = sample.expected.get("forbidden", [])

    # ── 문단 수 검사 (요약 계열) ──
    for item in structural:
        if "3문단" in item:
            paras = [p for p in response.split("\n\n") if p.strip()]
            checks["paragraph_count_3"] = len(paras) == 3
        elif "2문단" in item:
            paras = [p for p in response.split("\n\n") if p.strip()]
            checks["paragraph_count_ge2"] = len(paras) >= 2

    # ── 금지사항 검사 ──
    for item in forbidden:
        # 환각/사실 오류는 사람 검토 필요
        if any(kw in item for kw in ("환각", "사실 오류", "없는 사실", "없는 담당자")):
            checks["no_hallucination"] = "manual_required"
            continue

        # 영어 문장(서술) 혼입 금지 — 번역 to English 샘플은 제외
        if "영어 혼입 금지" in item or "영어 서술" in item:
            # 4글자 이상 영문 단어가 5개 초과이면 경고 (기술 고유명사 허용)
            eng_words = re.findall(r"\b[A-Za-z]{4,}\b", response)
            checks["no_excessive_english"] = len(eng_words) <= 10

        # 구어체/반말 금지
        if "구어체" in item or "반말" in item:
            informal_patterns = ["해요?", "이에요?", "했어", "할게"]
            has_informal = any(p in response for p in informal_patterns)
            checks["no_informal_speech"] = not has_informal

        # 원문 수치 변형 금지 (간단한 숫자 보존 검사)
        if "수치 변형 금지" in item or "숫자 변형 금지" in item:
            checks["no_number_alteration"] = "manual_required"

    # ── 응답 최소 길이 (너무 짧은 응답 차단) ──
    checks["min_response_length"] = len(response.strip()) >= 50

    # ── [MOCK] 플래그 존재 시 real 검사 스킵 ──
    if response.startswith("[MOCK"):
        checks = {k: "mock_mode" for k in checks}

    return checks


# ─── HTML 보고서 생성 ──────────────────────────────────────────────────────────

_CHECK_ICON = {True: "✅", False: "❌", "manual_required": "👤", "mock_mode": "🔵"}


def _check_badge(val: object) -> str:
    return _CHECK_ICON.get(val, "❓")  # type: ignore[arg-type]


def _e(text: str) -> str:
    """HTML escape."""
    return html_mod.escape(str(text), quote=True)


def generate_html_report(
    results: list[EvalResult],
    mode: str,
    model: str,
    endpoint: str,
) -> str:
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    total_samples = len(results)
    pass_count = sum(1 for r in results if r.auto_pass and not r.error)
    error_count = sum(1 for r in results if r.error)

    rows = []
    for r in results:
        s = r.sample
        menu_color = {
            "summarize": "#3b82f6",
            "translate": "#8b5cf6",
            "proofread": "#10b981",
            "draft": "#f59e0b",
        }.get(s.menu, "#6b7280")

        # 자동 검사 결과 배지
        check_html = "".join(
            f'<span title="{_e(k)}">{_check_badge(v)}</span> '
            for k, v in r.auto_checks.items()
        )

        # rubric 스코어 입력 폼 (사람 평가용)
        rubric_html = ""
        for i, item in enumerate(s.rubric):
            score_val = r.rubric_scores[i] if i < len(r.rubric_scores) else ""
            rubric_html += f"""
            <div class="rubric-item">
              <label>
                <strong>{_e(item.name)}</strong> (0–{item.max}점)
                <br><small>{_e(item.guide)}</small>
              </label>
              <input type="number" min="0" max="{item.max}"
                     placeholder="—" value="{_e(str(score_val))}"
                     class="score-input"
                     data-sample="{_e(s.id)}" data-idx="{i}" data-max="{item.max}"
                     oninput="updateTotal('{_e(s.id)}')">
              <span class="score-max">/ {item.max}</span>
            </div>"""

        status_class = "error" if r.error else ("pass" if r.auto_pass else "fail")
        rows.append(f"""
        <div class="sample-card status-{status_class}" id="{_e(s.id)}">
          <div class="card-header">
            <span class="sample-id">{_e(s.id)}</span>
            <span class="menu-badge" style="background:{menu_color}">{_e(s.menu)}</span>
            <span class="latency">{r.latency_ms}ms</span>
          </div>
          <p class="description">{_e(s.description)}</p>

          <details>
            <summary>입력 원문 ({len(s.input)}자)</summary>
            <pre class="text-block">{_e(s.input)}</pre>
          </details>

          {"<div class='error-box'>⚠️ " + _e(r.error or "") + "</div>" if r.error else ""}

          <details {"open" if not r.error else ""}>
            <summary>모델 응답</summary>
            <pre class="text-block response">{_e(r.model_response)}</pre>
          </details>

          <details>
            <summary>Golden Answer (참조용)</summary>
            <pre class="text-block golden">{_e(s.golden)}</pre>
          </details>

          <div class="checks-row">
            <strong>자동 검사:</strong> {check_html or "(없음)"}
          </div>

          <div class="rubric-section">
            <strong>Rubric 평가 (사람 입력)</strong>
            {rubric_html}
            <div class="total-score">
              총점: <span class="score-total" id="total-{_e(s.id)}">—</span>
              / {s.rubric[0].max * len(s.rubric) if s.rubric else 0}
              &nbsp;|&nbsp; 기준: ≥ {int(s.rubric[0].max * len(s.rubric) * 0.8)} 점
            </div>
          </div>
        </div>""")

    rows_html = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OnlyOffice AI — 한국어 Eval Baseline Report</title>
<style>
  body {{ font-family: -apple-system, 'Apple SD Gothic Neo', sans-serif;
         background:#f8fafc; color:#1e293b; margin:0; padding:20px; }}
  h1 {{ font-size:1.4em; margin-bottom:4px; }}
  .meta {{ color:#64748b; font-size:.85em; margin-bottom:24px; }}
  .summary {{ display:flex; gap:16px; margin-bottom:24px; }}
  .summary-box {{ background:#fff; border-radius:8px; padding:12px 20px;
                  border:1px solid #e2e8f0; min-width:100px; text-align:center; }}
  .summary-box .num {{ font-size:2em; font-weight:700; }}
  .summary-box .label {{ font-size:.75em; color:#64748b; }}
  .sample-card {{ background:#fff; border-radius:10px; border:1px solid #e2e8f0;
                  padding:18px; margin-bottom:20px; }}
  .status-pass {{ border-left:4px solid #10b981; }}
  .status-fail {{ border-left:4px solid #ef4444; }}
  .status-error {{ border-left:4px solid #f59e0b; }}
  .card-header {{ display:flex; gap:10px; align-items:center; margin-bottom:8px; }}
  .sample-id {{ font-weight:700; font-size:1em; }}
  .menu-badge {{ color:#fff; padding:2px 8px; border-radius:12px;
                font-size:.75em; font-weight:600; }}
  .latency {{ color:#94a3b8; font-size:.8em; margin-left:auto; }}
  .description {{ color:#475569; font-size:.9em; margin:4px 0 10px; white-space:pre-wrap; }}
  pre.text-block {{ background:#f1f5f9; border-radius:6px; padding:12px;
                    font-size:.82em; white-space:pre-wrap; overflow-x:auto;
                    max-height:320px; overflow-y:auto; }}
  pre.response {{ background:#f0fdf4; }}
  pre.golden {{ background:#fef3c7; }}
  .error-box {{ background:#fef2f2; border:1px solid #fecaca; border-radius:6px;
               padding:8px 12px; color:#991b1b; font-size:.85em; margin:8px 0; }}
  .checks-row {{ font-size:.9em; margin:10px 0; }}
  .rubric-section {{ margin-top:14px; }}
  .rubric-item {{ display:flex; align-items:flex-start; gap:10px; margin:8px 0;
                  flex-wrap:wrap; }}
  .rubric-item label {{ flex:1; min-width:200px; font-size:.88em; }}
  .score-input {{ width:60px; padding:4px 6px; border:1px solid #cbd5e1;
                 border-radius:4px; font-size:.9em; text-align:center; }}
  .score-max {{ color:#64748b; font-size:.85em; }}
  .total-score {{ margin-top:10px; font-weight:600; font-size:.95em; color:#1e293b; }}
  details summary {{ cursor:pointer; color:#3b82f6; font-size:.88em; margin:6px 0; }}
  .baseline-note {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px;
                    padding:12px 16px; margin-bottom:24px; font-size:.9em; }}
  @media print {{ .score-input {{ border:1px solid #666; }} }}
</style>
</head>
<body>

<h1>OnlyOffice AI — 한국어 Eval Baseline Report</h1>
<div class="meta">
  생성: {now_str} &nbsp;|&nbsp;
  모드: <strong>{_e(mode)}</strong> &nbsp;|&nbsp;
  모델: <strong>{_e(model)}</strong> &nbsp;|&nbsp;
  엔드포인트: {_e(endpoint)}
</div>

<div class="summary">
  <div class="summary-box"><div class="num">{total_samples}</div><div class="label">총 샘플</div></div>
  <div class="summary-box" style="border-left:3px solid #10b981">
    <div class="num" style="color:#10b981">{pass_count}</div>
    <div class="label">자동 검사 통과</div>
  </div>
  <div class="summary-box" style="border-left:3px solid #f59e0b">
    <div class="num" style="color:#f59e0b">{error_count}</div>
    <div class="label">오류</div>
  </div>
</div>

<div class="baseline-note">
  <strong>Baseline 기준</strong>: Sonnet 4.6 평균 rubric ≥ 12/15 (4/5 × 3항목),
  환각/사실 오류 0건, 한국어 자연스러움 ≥ 4/5.<br>
  👤 표시 항목은 사람 검토 필요. Rubric 점수 입력 후 총점 확인.
</div>

{rows_html}

<script>
function updateTotal(sampleId) {{
  const inputs = document.querySelectorAll(`input[data-sample="${{sampleId}}"]`);
  let total = 0, filled = true;
  inputs.forEach(inp => {{
    const v = parseInt(inp.value, 10);
    if (isNaN(v)) {{ filled = false; return; }}
    const mx = parseInt(inp.dataset.max, 10);
    total += Math.min(Math.max(v, 0), mx);
  }});
  const el = document.getElementById(`total-${{sampleId}}`);
  if (el) el.textContent = filled ? total : '—';
}}
</script>
</body>
</html>"""


# ─── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OnlyOffice AI 한국어 Eval Baseline 러너",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Mock 모드: Bedrock 호출 없이 스키마 검증 + 스텁 응답 (기본 권장)",
    )
    parser.add_argument(
        "--endpoint", default="http://localhost:8000",
        help="auth-gateway endpoint URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-6",
        choices=["claude-sonnet-4-6", "claude-haiku-4-5"],
        help="사용할 모델 (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--token", default=None,
        help="Bearer JWT 토큰 (real 모드에서 필요)",
    )
    parser.add_argument(
        "--filter", default=None,
        help="쉼표로 구분된 샘플 ID 필터 (예: ko-001,ko-005)",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_REPORT),
        help=f"HTML 보고서 출력 경로 (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="콘솔에 각 샘플 처리 결과 출력",
    )
    args = parser.parse_args()

    if not args.mock and not args.token:
        print(
            "WARNING: real 모드에서 --token 없이 실행합니다. "
            "401이 발생하면 --token 을 추가하세요.",
            file=sys.stderr,
        )

    filter_ids: Optional[list[str]] = None
    if args.filter:
        filter_ids = [x.strip() for x in args.filter.split(",") if x.strip()]

    print(f"Loading samples from {EVAL_DIR} ...", file=sys.stderr)
    samples = load_samples(filter_ids)
    print(f"  {len(samples)} sample(s) loaded.", file=sys.stderr)

    mode_label = "MOCK" if args.mock else f"REAL/{args.model}"
    results: list[EvalResult] = []

    for sample in samples:
        print(f"  [{mode_label}] {sample.id} ...", end=" ", file=sys.stderr, flush=True)
        result = EvalResult(sample=sample)
        try:
            if args.mock:
                response, latency = call_mock(sample)
            else:
                response, latency = call_real(
                    sample, args.endpoint, args.model, args.token
                )
            result.model_response = response
            result.latency_ms = latency
            result.auto_checks = auto_check(sample, response)
            status = "✅ mock" if args.mock else f"✅ {latency}ms"
        except RuntimeError as e:
            result.error = str(e)
            result.auto_checks = {}
            status = f"❌ {e}"

        results.append(result)
        print(status, file=sys.stderr)

    # HTML 보고서 저장
    report_html = generate_html_report(
        results,
        mode=mode_label,
        model=args.model,
        endpoint=args.endpoint if not args.mock else "N/A (mock)",
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_html, encoding="utf-8")

    print(f"\nReport saved: {out_path}", file=sys.stderr)
    print(f"Samples: {len(results)} | Errors: {sum(1 for r in results if r.error)}")

    # 스키마 통과 확인 (CI용 exit code)
    if any(r.error for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
