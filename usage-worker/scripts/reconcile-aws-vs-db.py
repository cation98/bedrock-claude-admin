#!/usr/bin/env python3
"""reconcile-aws-vs-db.py — CloudWatch Bedrock 사용량 vs DB token_usage_daily 비교.

사용법:
  python scripts/reconcile-aws-vs-db.py              # 어제 데이터 비교
  python scripts/reconcile-aws-vs-db.py --date 2026-04-24  # 특정 날짜
  python scripts/reconcile-aws-vs-db.py --alert      # Telegram 알림 활성화
  python scripts/reconcile-aws-vs-db.py --threshold 0.05  # 5% 불일치 임계값 (기본)

환경변수:
  DATABASE_URL           — PostgreSQL DSN
  TELEGRAM_BOT_TOKEN     — 알림 발송용 봇 토큰 (optional)
  TELEGRAM_ALERT_CHAT_ID — 알림 수신 채팅 ID (optional)

CloudWatch 메트릭:
  Namespace: AWS/Bedrock
  MetricName: InputTokenCount / OutputTokenCount (per ModelId)
  Statistics: Sum
  Period: 86400s (1일)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta, datetime, timezone

try:
    import boto3
except ImportError:
    boto3 = None

try:
    import httpx
except ImportError:
    httpx = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

THRESHOLD = float(os.environ.get("RECONCILE_THRESHOLD", "0.05"))  # 5%
CW_NAMESPACE = "AWS/Bedrock"


def fetch_cloudwatch_tokens(target_date: date, region: str) -> dict[str, int]:
    """CloudWatch에서 target_date 기준 모델별 input/output 토큰 합계 조회."""
    if not boto3:
        print("boto3 미설치 — CloudWatch 조회 건너뜀", file=sys.stderr)
        return {}

    cw = boto3.client("cloudwatch", region_name=region)
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    result: dict[str, int] = {}
    for metric_name in ("InputTokenCount", "OutputTokenCount"):
        paginator = cw.get_paginator("list_metrics")
        for page in paginator.paginate(Namespace=CW_NAMESPACE, MetricName=metric_name):
            for m in page["Metrics"]:
                model_id = next(
                    (d["Value"] for d in m.get("Dimensions", []) if d["Name"] == "ModelId"),
                    "unknown",
                )
                resp = cw.get_metric_statistics(
                    Namespace=CW_NAMESPACE,
                    MetricName=metric_name,
                    Dimensions=[{"Name": "ModelId", "Value": model_id}],
                    StartTime=start,
                    EndTime=end,
                    Period=86400,
                    Statistics=["Sum"],
                )
                tokens = int(sum(p["Sum"] for p in resp.get("Datapoints", [])))
                key = f"{model_id}:{metric_name}"
                result[key] = result.get(key, 0) + tokens
    return result


def fetch_db_totals(target_date: date, database_url: str) -> dict[str, dict]:
    """DB token_usage_daily에서 target_date의 모델별 집계."""
    if not psycopg2:
        print("psycopg2 미설치 — DB 조회 건너뜀", file=sys.stderr)
        return {}

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_id,
                       SUM(input_tokens)  AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_usd)      AS cost_usd
                FROM   token_usage_daily
                WHERE  usage_date = %s
                GROUP  BY model_id
                """,
                (target_date,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {
        row[0]: {
            "input_tokens": row[1] or 0,
            "output_tokens": row[2] or 0,
            "cost_usd": float(row[3] or 0),
        }
        for row in rows
    }


def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> None:
    if not httpx:
        print("httpx 미설치 — Telegram 알림 건너뜀", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    httpx.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)


def compare(cw_data: dict, db_data: dict, threshold: float) -> list[dict]:
    """비교 결과 반환. threshold 초과 불일치만 포함."""
    discrepancies = []
    all_models: set[str] = set()
    for key in cw_data:
        model = key.split(":")[0]
        all_models.add(model)
    all_models.update(db_data.keys())

    for model in sorted(all_models):
        cw_input = cw_data.get(f"{model}:InputTokenCount", 0)
        cw_output = cw_data.get(f"{model}:OutputTokenCount", 0)
        db = db_data.get(model, {"input_tokens": 0, "output_tokens": 0})

        for token_type, cw_val, db_val in [
            ("input", cw_input, db["input_tokens"]),
            ("output", cw_output, db["output_tokens"]),
        ]:
            if cw_val == 0 and db_val == 0:
                continue
            denom = max(cw_val, db_val, 1)
            pct = abs(cw_val - db_val) / denom
            if pct > threshold:
                discrepancies.append({
                    "model": model,
                    "type": token_type,
                    "cloudwatch": cw_val,
                    "db": db_val,
                    "diff_pct": round(pct * 100, 2),
                })
    return discrepancies


def main() -> int:
    parser = argparse.ArgumentParser(description="CloudWatch vs DB token reconciliation")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--alert", action="store_true", help="Send Telegram alert on discrepancy")
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-2"))
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    database_url = os.environ.get("DATABASE_URL", "")

    print(f"=== Reconcile {target} ===")
    print(f"  Region:    {args.region}")
    print(f"  Threshold: {args.threshold * 100:.1f}%")
    print()

    cw_data = fetch_cloudwatch_tokens(target, args.region)
    db_data = fetch_db_totals(target, database_url) if database_url else {}

    if not cw_data:
        print("CloudWatch 데이터 없음 (자격증명/권한 확인 필요)")
    if not db_data:
        print("DB 데이터 없음")

    discrepancies = compare(cw_data, db_data, args.threshold)

    if not discrepancies:
        print("✅ 불일치 없음 (모든 모델 오차 ≤ {:.1f}%)".format(args.threshold * 100))
        return 0

    print(f"⚠️  불일치 {len(discrepancies)}건:")
    for d in discrepancies:
        print(f"  [{d['model']}] {d['type']}: CW={d['cloudwatch']:,} DB={d['db']:,} ({d['diff_pct']}% 차이)")

    if args.alert:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
        if bot_token and chat_id:
            lines = [f"⚠️ *Bedrock 사용량 불일치 ({target})*\n"]
            for d in discrepancies:
                lines.append(
                    f"• `{d['model']}` {d['type']}: CW={d['cloudwatch']:,} DB={d['db']:,} ({d['diff_pct']}%)"
                )
            send_telegram_alert(bot_token, chat_id, "\n".join(lines))
            print("📱 Telegram 알림 발송 완료")
        else:
            print("Telegram 자격증명 없음 — 알림 건너뜀")

    return 1


if __name__ == "__main__":
    sys.exit(main())
