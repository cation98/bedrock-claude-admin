"""모든 Bedrock 모델 가격표 + KRW 환율 단일 출처.

기존 산재된 가격 상수 (admin.py:58-60, bedrock_proxy.py:288-303 등)를
이 모듈로 통합. pricing 변경 시 1곳만 수정.

가격 출처: AWS Bedrock 공식 가격 페이지
  https://docs.aws.amazon.com/bedrock/latest/userguide/model-pricing.html
가격 검토 주기: 분기별 (Bedrock 가격 분기별 변동 가능)
Anthropic prompt caching 가격 정책:
  cache_creation = 1.25× input
  cache_read     = 0.10× input
"""

# 4분기 가격 (USD per 1M tokens, 2026-04-25 기준)
PRICE_TABLE: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input":          3.00,
        "output":         15.00,
        "cache_creation": 3.75,   # 1.25× input
        "cache_read":     0.30,   # 0.10× input
    },
    "claude-haiku-4-5": {
        "input":          0.80,
        "output":         4.00,
        "cache_creation": 1.00,   # 1.25× input
        "cache_read":     0.08,   # 0.10× input
    },
    "claude-opus-4-6": {
        "input":          15.00,
        "output":         75.00,
        "cache_creation": 18.75,  # 1.25× input
        "cache_read":     1.50,   # 0.10× input
    },
}

KRW_RATE: int = 1400  # 1 USD = 1400 KRW (2026-04 기준 평균, 분기별 재검토)


def get_price_table(model_id: str) -> dict[str, float]:
    """모델 ID에서 4분기 가격표 조회.

    Bedrock cross-region inference profile prefix(global./us./eu./ap.) 및
    -YYYYMMDD-vN 버전 suffix를 제거한 뒤 substring 매칭한다.

    매칭 우선순위:
      1. 'haiku' substring → claude-haiku-4-5
      2. 'opus' substring  → claude-opus-4-6
      3. default          → claude-sonnet-4-6 (가장 흔한 모델)

    Args:
        model_id: Bedrock model ID (e.g. 'global.anthropic.claude-sonnet-4-6')

    Returns:
        가격 dict {'input': float, 'output': float,
                   'cache_creation': float, 'cache_read': float}
    """
    lid = model_id.lower()
    if "haiku" in lid:
        return PRICE_TABLE["claude-haiku-4-5"]
    if "opus" in lid:
        return PRICE_TABLE["claude-opus-4-6"]
    return PRICE_TABLE["claude-sonnet-4-6"]
