#!/usr/bin/env python3
"""Generate a decision-ready LinkedIn ads daily check from spreadsheet data."""

from __future__ import annotations

import argparse
import calendar
import csv
import html
import io
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

DEFAULT_SAMPLE_CSV_PATH = "fixtures/sample_linkedin_ads.csv"
DEFAULT_TARGETS = {
    "cpc_max": 5.0,
    "ctr_min": 0.65,
    "cpl_max": 120.0,
    "conversion_rate_min": 1.0,
}
DEFAULT_AI_CONFIG = {
    "enabled": False,
    "provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4.1-mini",
    "api_key_env": "OPENAI_API_KEY",
}
CURRENT_MONTH_ALIASES = {"current", "latest"}
TREND_METRICS = ("spend", "clicks", "leads", "cpc", "ctr", "cpl", "conversion_rate")
LOWER_IS_BETTER_METRICS = {"cpc", "cpl"}
METRIC_LABELS = {
    "spend": "Spend",
    "clicks": "Clicks",
    "leads": "Leads",
    "cpc": "CPC",
    "ctr": "CTR",
    "cpl": "CPL",
    "conversion_rate": "Conversion rate",
}


@dataclass(frozen=True)
class AIProviderConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key_env: str


@dataclass(frozen=True)
class SummaryResult:
    lines: list[str]
    source_label: str
    detail: str


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch LinkedIn campaign data, score KPI health, and write shareable report artifacts."
        )
    )
    parser.add_argument("--config", help="Optional JSON config file.")
    parser.add_argument("--csv-url", help="CSV export URL for the spreadsheet.")
    parser.add_argument("--csv-path", help="Optional local CSV path instead of URL.")
    parser.add_argument(
        "--monthly-budget",
        type=float,
        help="Monthly budget used for pacing checks. Can also be set in the config file.",
    )
    parser.add_argument(
        "--month",
        default="latest",
        help="Month to report in YYYY-MM format or 'latest'.",
    )
    parser.add_argument(
        "--top-campaigns",
        type=int,
        default=12,
        help="Maximum number of campaigns to show in each table.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory where report artifacts should be written.",
    )
    parser.add_argument(
        "--enable-ai-summary",
        action="store_true",
        help="Generate the optional analyst note through an OpenAI-compatible API.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_argument_parser().parse_args(argv)


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_rows(csv_url: str | None, csv_path: str | None) -> list[dict[str, str]]:
    if csv_path:
        raw_text = Path(csv_path).read_text(encoding="utf-8-sig")
    elif csv_url:
        with urllib.request.urlopen(csv_url, timeout=30) as response:
            raw_text = response.read().decode("utf-8-sig")
    else:
        raise ValueError("A CSV path or CSV URL is required.")
    reader = csv.DictReader(io.StringIO(raw_text))
    required_headers = {
        "Date",
        "Campaign name",
        "Impressions",
        "Clicks",
        "Total spent",
        "Conversions",
        "Leads",
    }
    actual_headers = set(reader.fieldnames or [])
    missing_headers = sorted(required_headers - actual_headers)
    if missing_headers:
        raise ValueError(f"Missing required CSV headers: {', '.join(missing_headers)}")
    rows = [row for row in reader if row.get("Date") and row.get("Campaign name")]
    if not rows:
        raise ValueError("No campaign rows were found in the CSV input.")
    return rows


def parse_number(raw_value: str) -> float:
    cleaned = (raw_value or "").strip().replace(",", "")
    return float(cleaned) if cleaned else 0.0


def split_campaign_name(campaign_name: str) -> dict[str, str]:
    parts = [part.strip() for part in campaign_name.split("|")]
    if len(parts) == 8:
        return {
            "quarter": parts[0],
            "product": parts[1],
            "region": parts[2],
            "consent_status": parts[3],
            "audience": parts[4],
            "funnel_stage": parts[5],
            "objective": parts[6],
            "creative": parts[7],
        }
    if len(parts) == 7:
        return {
            "quarter": parts[0],
            "product": parts[1],
            "region": parts[2],
            "consent_status": "",
            "audience": parts[3],
            "funnel_stage": parts[4],
            "objective": parts[5],
            "creative": parts[6],
        }
    raise ValueError(
        f"Unsupported campaign name shape with {len(parts)} parts: {campaign_name}"
    )


def normalize_rows(raw_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        campaign = row["Campaign name"].strip()
        try:
            normalized.append(
                {
                    "date": date.fromisoformat(row["Date"].strip()),
                    "campaign_name": campaign,
                    "impressions": int(parse_number(row.get("Impressions", ""))),
                    "clicks": int(parse_number(row.get("Clicks", ""))),
                    "spend": parse_number(row.get("Total spent", "")),
                    "conversions": int(parse_number(row.get("Conversions", ""))),
                    "leads": int(parse_number(row.get("Leads", ""))),
                    **split_campaign_name(campaign),
                }
            )
        except ValueError as error:
            raise ValueError(f"Invalid row for campaign '{campaign}': {error}") from error
    return normalized


def format_currency(value: float | None) -> str:
    return "N/A" if value is None else f"${value:,.2f}"


def format_number(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def format_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def metric_value(numerator: float, denominator: float, multiplier: float = 1.0) -> float | None:
    if denominator <= 0:
        return None
    return (numerator / denominator) * multiplier


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    impressions = sum(row["impressions"] for row in rows)
    clicks = sum(row["clicks"] for row in rows)
    spend = sum(row["spend"] for row in rows)
    conversions = sum(row["conversions"] for row in rows)
    leads = sum(row["leads"] for row in rows)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "conversions": conversions,
        "leads": leads,
        "cpc": metric_value(spend, clicks),
        "ctr": metric_value(clicks, impressions, 100.0),
        "cpl": metric_value(spend, leads),
        "conversion_rate": metric_value(conversions, clicks, 100.0),
    }


def evaluate_metric(metric_name: str, value: float | None, targets: dict[str, float]) -> dict[str, Any]:
    metric_rules = {
        "cpc": ("max", targets["cpc_max"]),
        "ctr": ("min", targets["ctr_min"]),
        "cpl": ("max", targets["cpl_max"]),
        "conversion_rate": ("min", targets["conversion_rate_min"]),
    }
    direction, threshold = metric_rules[metric_name]
    if value is None:
        return {"label": "N/A", "tone": "muted", "threshold": threshold, "variance_pct": None}

    if direction == "max":
        variance_pct = ((value - threshold) / threshold) * 100
        if value <= threshold:
            tone, label = "good", "On target"
        elif value <= threshold * 1.1:
            tone, label = "warn", "Near target"
        else:
            tone, label = "bad", "Off target"
    else:
        variance_pct = ((threshold - value) / threshold) * 100
        if value >= threshold:
            tone, label = "good", "On target"
        elif value >= threshold * 0.9:
            tone, label = "warn", "Near target"
        else:
            tone, label = "bad", "Off target"

    return {"label": label, "tone": tone, "threshold": threshold, "variance_pct": variance_pct}


def pick_report_month(rows: list[dict[str, Any]], month_arg: str) -> str:
    months = sorted({row["date"].strftime("%Y-%m") for row in rows})
    if not months:
        raise ValueError("No dated campaign rows were found in the CSV input.")
    if month_arg.lower() in CURRENT_MONTH_ALIASES:
        return months[-1]
    if month_arg not in months:
        available = ", ".join(months)
        raise ValueError(f"Month '{month_arg}' not found in data. Available months: {available}")
    return month_arg


def build_pacing(monthly_budget: float, spend_mtd: float, latest_date: date) -> dict[str, Any]:
    total_days = calendar.monthrange(latest_date.year, latest_date.month)[1]
    elapsed_days = latest_date.day
    planned_spend_to_date = monthly_budget * (elapsed_days / total_days)
    projected_month_end_spend = (spend_mtd / elapsed_days) * total_days if elapsed_days else 0.0
    projected_gap = projected_month_end_spend - monthly_budget

    lower_bound = monthly_budget * 0.95
    upper_bound = monthly_budget * 1.05
    if projected_month_end_spend < lower_bound:
        label, tone = "Under pace", "warn"
    elif projected_month_end_spend > upper_bound:
        label, tone = "Over pace", "bad"
    else:
        label, tone = "On pace", "good"

    return {
        "monthly_budget": monthly_budget,
        "elapsed_days": elapsed_days,
        "total_days": total_days,
        "planned_spend_to_date": planned_spend_to_date,
        "spend_mtd": spend_mtd,
        "spend_gap_today": spend_mtd - planned_spend_to_date,
        "projected_month_end_spend": projected_month_end_spend,
        "projected_gap": projected_gap,
        "label": label,
        "tone": tone,
    }


def campaign_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    return {
        "cpc": metric_value(row["spend"], row["clicks"]),
        "ctr": metric_value(row["clicks"], row["impressions"], 100.0),
        "cpl": metric_value(row["spend"], row["leads"]),
        "conversion_rate": metric_value(row["conversions"], row["clicks"], 100.0),
    }


def build_campaign_alerts(
    latest_rows: list[dict[str, Any]],
    targets: dict[str, float],
    limit: int,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    for row in latest_rows:
        metrics = campaign_metrics(row)
        reasons: list[str] = []
        severity = 0.0

        if row["spend"] >= 50 and row["clicks"] == 0:
            reasons.append("Spend with no clicks")
            severity += 3.0

        if metrics["cpc"] is not None and row["clicks"] >= 5 and metrics["cpc"] > targets["cpc_max"]:
            reasons.append(f"CPC {format_currency(metrics['cpc'])} vs target {format_currency(targets['cpc_max'])}")
            severity += max(metrics["cpc"] / targets["cpc_max"] - 1, 0)

        if metrics["ctr"] is not None and row["impressions"] >= 1500 and metrics["ctr"] < targets["ctr_min"]:
            reasons.append(f"CTR {format_pct(metrics['ctr'])} vs target {format_pct(targets['ctr_min'])}")
            severity += max(targets["ctr_min"] / max(metrics["ctr"], 0.01) - 1, 0)

        if row["leads"] > 0 and metrics["cpl"] is not None and metrics["cpl"] > targets["cpl_max"]:
            reasons.append(f"CPL {format_currency(metrics['cpl'])} vs target {format_currency(targets['cpl_max'])}")
            severity += max(metrics["cpl"] / targets["cpl_max"] - 1, 0)
        elif row["objective"].lower() == "lead gen" and row["spend"] >= 120 and row["leads"] == 0:
            reasons.append("Lead gen spend without leads")
            severity += 2.0

        if row["objective"].lower() != "lead gen" and row["clicks"] >= 25 and row["conversions"] == 0:
            reasons.append("Traffic campaign without conversions")
            severity += 2.0
        elif (
            row["conversions"] > 0
            and metrics["conversion_rate"] is not None
            and metrics["conversion_rate"] < targets["conversion_rate_min"]
        ):
            reasons.append(
                f"Conversion rate {format_pct(metrics['conversion_rate'])} vs target {format_pct(targets['conversion_rate_min'])}"
            )
            severity += max(targets["conversion_rate_min"] / max(metrics["conversion_rate"], 0.01) - 1, 0)

        if not reasons:
            continue

        alerts.append(
            {
                **row,
                **metrics,
                "reasons": reasons,
                "severity": round(severity + (row["spend"] / 500.0), 3),
            }
        )

    alerts.sort(key=lambda item: (item["severity"], item["spend"]), reverse=True)
    return alerts[:limit]


def build_campaign_wins(
    latest_rows: list[dict[str, Any]],
    targets: dict[str, float],
    limit: int,
    excluded_campaigns: set[str] | None = None,
) -> list[dict[str, Any]]:
    wins: list[dict[str, Any]] = []
    excluded_campaigns = excluded_campaigns or set()

    for row in latest_rows:
        if row["campaign_name"] in excluded_campaigns:
            continue
        metrics = campaign_metrics(row)
        if row["clicks"] < 10 or row["spend"] < 50:
            continue

        passes = 0
        applicable = 0
        if metrics["cpc"] is not None:
            applicable += 1
            passes += int(metrics["cpc"] <= targets["cpc_max"])
        if metrics["ctr"] is not None:
            applicable += 1
            passes += int(metrics["ctr"] >= targets["ctr_min"])
        if row["leads"] > 0 and metrics["cpl"] is not None:
            applicable += 1
            passes += int(metrics["cpl"] <= targets["cpl_max"])
        if row["conversions"] > 0 and metrics["conversion_rate"] is not None:
            applicable += 1
            passes += int(metrics["conversion_rate"] >= targets["conversion_rate_min"])

        efficiency_score = (
            (row["leads"] * 4)
            + (row["conversions"] * 2)
            + (passes * 5)
            + (metrics["ctr"] or 0)
            - ((metrics["cpc"] or 0) / 10)
        )
        wins.append(
            {
                **row,
                **metrics,
                "passes": passes,
                "applicable": applicable,
                "efficiency_score": round(efficiency_score, 3),
            }
        )

    wins.sort(
        key=lambda item: (
            item["passes"],
            item["leads"],
            item["conversions"],
            item["efficiency_score"],
            item["spend"],
        ),
        reverse=True,
    )
    return wins[:limit]


def campaign_control_key(row: dict[str, Any]) -> str:
    return " | ".join([row["product"], row["region"], row["objective"]])


def pct_change(current: float | int | None, previous: float | int | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((float(current) - float(previous)) / float(previous)) * 100


def trend_tone(metric_name: str, delta_pct: float | None) -> str:
    if delta_pct is None:
        return "muted"
    if abs(delta_pct) < 5:
        return "muted"
    if metric_name in LOWER_IS_BETTER_METRICS:
        return "good" if delta_pct < 0 else "bad"
    if metric_name in {"ctr", "conversion_rate", "leads", "clicks"}:
        return "good" if delta_pct > 0 else "bad"
    return "warn" if abs(delta_pct) >= 25 else "muted"


def format_metric_value(metric_name: str, value: float | int | None) -> str:
    if metric_name in {"spend", "cpc", "cpl"}:
        return format_currency(value if value is None else float(value))
    if metric_name in {"ctr", "conversion_rate"}:
        return format_pct(value if value is None else float(value))
    return format_number(value)


def format_delta_pct(delta_pct: float | None) -> str:
    if delta_pct is None:
        return "n/a"
    sign = "+" if delta_pct > 0 else ""
    return f"{sign}{delta_pct:.1f}%"


def build_daily_trends(month_rows: list[dict[str, Any]], latest_date: date) -> list[dict[str, Any]]:
    previous_dates = sorted({row["date"] for row in month_rows if row["date"] < latest_date})
    if not previous_dates:
        return []
    previous_date = previous_dates[-1]
    latest_summary = aggregate([row for row in month_rows if row["date"] == latest_date])
    previous_summary = aggregate([row for row in month_rows if row["date"] == previous_date])
    trends: list[dict[str, Any]] = []
    for metric_name in TREND_METRICS:
        current = latest_summary.get(metric_name)
        previous = previous_summary.get(metric_name)
        delta = pct_change(current, previous)
        trends.append(
            {
                "metric": metric_name,
                "label": METRIC_LABELS[metric_name],
                "latest_date": latest_date.isoformat(),
                "previous_date": previous_date.isoformat(),
                "current": current,
                "previous": previous,
                "current_display": format_metric_value(metric_name, current),
                "previous_display": format_metric_value(metric_name, previous),
                "delta_pct": delta,
                "delta_display": format_delta_pct(delta),
                "tone": trend_tone(metric_name, delta),
            }
        )
    return trends


def aggregate_campaign_group(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    return aggregate(rows)


def build_campaign_movements(
    month_rows: list[dict[str, Any]],
    latest_date: date,
    limit: int,
) -> list[dict[str, Any]]:
    previous_dates = sorted({row["date"] for row in month_rows if row["date"] < latest_date})
    if not previous_dates:
        return []
    previous_date = previous_dates[-1]
    latest_groups: dict[str, list[dict[str, Any]]] = {}
    previous_groups: dict[str, list[dict[str, Any]]] = {}
    for row in month_rows:
        if row["date"] == latest_date:
            latest_groups.setdefault(campaign_control_key(row), []).append(row)
        elif row["date"] == previous_date:
            previous_groups.setdefault(campaign_control_key(row), []).append(row)

    movements: list[dict[str, Any]] = []
    for key, latest_items in latest_groups.items():
        previous_items = previous_groups.get(key)
        if not previous_items:
            continue
        latest_metrics = aggregate_campaign_group(latest_items)
        previous_metrics = aggregate_campaign_group(previous_items)
        spend_delta = (latest_metrics["spend"] or 0) - (previous_metrics["spend"] or 0)
        click_delta = (latest_metrics["clicks"] or 0) - (previous_metrics["clicks"] or 0)
        lead_delta = (latest_metrics["leads"] or 0) - (previous_metrics["leads"] or 0)
        ctr_delta = pct_change(latest_metrics["ctr"], previous_metrics["ctr"])
        movement_score = abs(spend_delta) + (abs(click_delta) * 2) + (abs(lead_delta) * 12)
        movements.append(
            {
                "campaign_key": key,
                "latest_date": latest_date.isoformat(),
                "previous_date": previous_date.isoformat(),
                "spend_delta": spend_delta,
                "click_delta": click_delta,
                "lead_delta": lead_delta,
                "ctr_delta_pct": ctr_delta,
                "current_spend": latest_metrics["spend"],
                "previous_spend": previous_metrics["spend"],
                "current_ctr": latest_metrics["ctr"],
                "previous_ctr": previous_metrics["ctr"],
                "movement_score": round(movement_score, 3),
            }
        )
    movements.sort(key=lambda item: item["movement_score"], reverse=True)
    return movements[:limit]


def build_risk_register(
    latest_statuses: dict[str, dict[str, Any]],
    pacing: dict[str, Any],
    alerts: list[dict[str, Any]],
    daily_trends: list[dict[str, Any]],
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if pacing["tone"] != "good":
        risks.append(
            {
                "area": "Budget pacing",
                "severity": "high" if pacing["tone"] == "bad" else "medium",
                "evidence": (
                    f"{pacing['label']} with projected month-end spend "
                    f"{format_currency(pacing['projected_month_end_spend'])}."
                ),
                "next_step": "Recheck monthly budget, pacing target, and campaigns driving spend.",
            }
        )
    for metric, status in latest_statuses.items():
        if status["tone"] == "bad":
            risks.append(
                {
                    "area": f"Account KPI: {METRIC_LABELS[metric]}",
                    "severity": "high",
                    "evidence": f"{status['label']} against target {format_metric_value(metric, status['threshold'])}.",
                    "next_step": "Inspect the campaign review table and pause or revise the first flagged campaigns.",
                }
            )
    for alert in alerts[:3]:
        risks.append(
            {
                "area": "Campaign review",
                "severity": "high" if alert["severity"] >= 3 else "medium",
                "evidence": f"{alert['campaign_name']}: {alert['reasons'][0]}.",
                "next_step": "Open this campaign first and decide whether to pause, edit targeting, or adjust budget.",
            }
        )
    for trend in daily_trends:
        if trend["tone"] == "bad":
            risks.append(
                {
                    "area": f"Daily movement: {trend['label']}",
                    "severity": "medium",
                    "evidence": f"{trend['label']} moved {trend['delta_display']} since the previous reporting day.",
                    "next_step": "Compare the campaign movement table before changing budget allocation.",
                }
            )
    return risks[:8]


def ai_config_from_dict(config: dict[str, Any]) -> AIProviderConfig:
    return AIProviderConfig(
        enabled=bool(config.get("enabled", False)),
        provider=str(config.get("provider", "openai")),
        base_url=str(config.get("base_url", "https://api.openai.com/v1")),
        model=str(config.get("model", "gpt-4.1-mini")),
        api_key_env=str(config.get("api_key_env", "OPENAI_API_KEY")),
    )


def extract_llm_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    if payload.get("choices"):
        message = payload["choices"][0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(item.get("text", "") for item in content if isinstance(item, dict))
    output = payload.get("output")
    if isinstance(output, list):
        fragments: list[str] = []
        for item in output:
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                    fragments.append(content.get("text", ""))
        return "".join(fragments)
    return ""


class OpenAICompatibleSummaryProvider:
    """Provider boundary for optional live analyst-note generation."""

    def __init__(self, config: AIProviderConfig):
        self.config = config

    def generate(self, prompt: str) -> str | None:
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            return None

        base_url = self.config.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        candidate_requests = [
            (
                f"{base_url}/responses",
                {"model": self.config.model, "input": prompt, "max_output_tokens": 220},
            ),
            (
                f"{base_url}/chat/completions",
                {
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 220,
                },
            ),
        ]

        for endpoint, payload in candidate_requests:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                summary = extract_llm_text(parsed)
                if summary:
                    return summary.strip()
            except urllib.error.HTTPError as error:
                if error.code in {400, 404, 405}:
                    continue
                raise
            except Exception:
                return None
        return None


def json_dumps_compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), indent=2)


def build_analyst_prompt(report_payload: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""
        You are a paid media analyst. Write exactly 4 concise bullet points for the LinkedIn ads specialist.
        Requirements:
        - Be practical, specific, and brief.
        - Mention account-level KPI health, pacing, and the first campaigns to inspect.
        - Do not mention that you are an AI model.
        - Do not invent data.

        Report data:
        {json_dumps_compact(report_payload)}
        """
    ).strip()


def split_summary_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = [line.lstrip("-*• ").strip() for line in lines]
    return cleaned[:4]


def build_rule_based_analyst_lines(
    latest_summary: dict[str, Any],
    month_summary: dict[str, Any],
    pacing: dict[str, Any],
    latest_statuses: dict[str, dict[str, Any]],
    month_statuses: dict[str, dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    bad_latest = [metric.upper() for metric, status in latest_statuses.items() if status["tone"] == "bad"]
    if bad_latest:
        lines.append(
            f"Latest-day KPI misses: {', '.join(bad_latest)}. Prioritize budget away from campaigns that missed both CPC and CTR."
        )
    else:
        lines.append("Latest-day KPI health is stable with no hard account-level misses.")
    lines.append(
        f"Pacing is {pacing['label'].lower()}: projected month-end spend is {format_currency(pacing['projected_month_end_spend'])} against a {format_currency(pacing['monthly_budget'])} budget."
    )
    if latest_summary["leads"]:
        lines.append(
            f"Latest day delivered {format_number(latest_summary['leads'])} leads from {format_number(latest_summary['clicks'])} clicks at {format_currency(latest_summary['cpl'])} CPL."
        )
    else:
        lines.append("Latest day delivered no leads, so lead-gen spend concentration should be reviewed first.")
    if month_statuses["conversion_rate"]["tone"] == "bad":
        lines.append(
            f"Month-to-date conversion rate is {format_pct(month_summary['conversion_rate'])}, below the {format_pct(month_statuses['conversion_rate']['threshold'])} target."
        )
    elif alerts:
        lines.append(f"Top campaign to inspect first: {alerts[0]['campaign_name']} because {alerts[0]['reasons'][0].lower()}.")
    else:
        lines.append("No campaign-level breaches were detected on the latest date.")
    return lines[:4]


def generate_analyst_note(
    report_payload: dict[str, Any],
    ai_config: AIProviderConfig,
    fallback_lines: list[str],
) -> SummaryResult:
    if not ai_config.enabled:
        return SummaryResult(fallback_lines, "Rule-based fallback", "Optional live AI narrative is disabled.")
    if not os.getenv(ai_config.api_key_env):
        return SummaryResult(
            fallback_lines,
            "Rule-based fallback",
            f"Optional live AI narrative was requested but {ai_config.api_key_env} is not set.",
        )

    try:
        summary = OpenAICompatibleSummaryProvider(ai_config).generate(build_analyst_prompt(report_payload))
    except Exception as error:
        return SummaryResult(fallback_lines, "Rule-based fallback", f"Optional live AI narrative failed: {error}")
    if not summary:
        return SummaryResult(fallback_lines, "Rule-based fallback", "Optional live AI narrative returned no text.")

    lines = split_summary_lines(summary)
    if not lines:
        return SummaryResult(fallback_lines, "Rule-based fallback", "Optional live AI narrative returned empty bullet content.")
    return SummaryResult(lines, "Live AI note", f"Generated through {ai_config.provider} using model {ai_config.model}.")


def build_account_health_line(latest_summary: dict[str, Any], latest_statuses: dict[str, dict[str, Any]]) -> str:
    parts = [
        f"CPC {format_currency(latest_summary['cpc'])} ({latest_statuses['cpc']['label'].lower()} vs {format_currency(latest_statuses['cpc']['threshold'])})",
        f"CTR {format_pct(latest_summary['ctr'])} ({latest_statuses['ctr']['label'].lower()} vs {format_pct(latest_statuses['ctr']['threshold'])})",
        f"CPL {format_currency(latest_summary['cpl'])} ({latest_statuses['cpl']['label'].lower()} vs {format_currency(latest_statuses['cpl']['threshold'])})",
        f"Conversion rate {format_pct(latest_summary['conversion_rate'])} ({latest_statuses['conversion_rate']['label'].lower()} vs {format_pct(latest_statuses['conversion_rate']['threshold'])})",
    ]
    return "Account health: " + "; ".join(parts) + "."


def build_action_list(
    latest_summary: dict[str, Any],
    latest_statuses: dict[str, dict[str, Any]],
    pacing: dict[str, Any],
    alerts: list[dict[str, Any]],
    wins: list[dict[str, Any]],
    risk_register: list[dict[str, str]] | None = None,
) -> list[str]:
    actions = [build_account_health_line(latest_summary, latest_statuses)]
    actions.append(
        f"Pacing verdict: {pacing['label']} with projected month-end spend {format_currency(pacing['projected_month_end_spend'])} against a {format_currency(pacing['monthly_budget'])} budget."
    )
    if risk_register:
        actions.append(
            "Resolve these control risks first: "
            + "; ".join(f"{risk['area']} ({risk['severity']})" for risk in risk_register[:3])
            + "."
        )
    if alerts:
        actions.append("Review these campaigns first: " + "; ".join(f"{alert['campaign_name']} ({alert['reasons'][0]})" for alert in alerts[:3]) + ".")
    else:
        actions.append("No campaigns breached the rule set on the latest reporting date.")
    if wins:
        actions.append(f"Protect or scale the strongest latest-day campaigns: {', '.join(win['campaign_name'] for win in wins[:2])}.")
    return actions


def build_metric_cards(summary: dict[str, Any], targets: dict[str, float]) -> dict[str, dict[str, Any]]:
    return {
        "cpc": {"title": "CPC", "value": format_currency(summary["cpc"]), "status": evaluate_metric("cpc", summary["cpc"], targets)},
        "ctr": {"title": "CTR", "value": format_pct(summary["ctr"]), "status": evaluate_metric("ctr", summary["ctr"], targets)},
        "cpl": {"title": "CPL", "value": format_currency(summary["cpl"]), "status": evaluate_metric("cpl", summary["cpl"], targets)},
        "conversion_rate": {
            "title": "Conversion rate",
            "value": format_pct(summary["conversion_rate"]),
            "status": evaluate_metric("conversion_rate", summary["conversion_rate"], targets),
        },
    }


def tone_chip(label: str, tone: str) -> str:
    return f'<span class="chip {tone}">{html.escape(label)}</span>'


def render_metric_tile(card: dict[str, Any]) -> str:
    status = card["status"]
    return (
        '<article class="metric-tile">'
        f"<p class=\"eyebrow\">{html.escape(card['title'])}</p>"
        "<div class=\"metric-row\">"
        f"<strong>{html.escape(card['value'])}</strong>{tone_chip(status['label'], status['tone'])}"
        "</div></article>"
    )


def render_campaign_table(rows: list[dict[str, Any]], empty_label: str, is_alert_table: bool) -> str:
    if not rows:
        return f'<tr><td colspan="9" class="empty-state">{html.escape(empty_label)}</td></tr>'
    row_html: list[str] = []
    for row in rows:
        reason_cell = "<br>".join(html.escape(reason) for reason in row.get("reasons", []))
        status_cell = html.escape(f"{row.get('passes', 0)}/{row.get('applicable', 0)} KPI checks")
        final_cell = reason_cell if is_alert_table else status_cell
        row_html.append(
            "<tr>"
            f"<td>{html.escape(row['campaign_name'])}</td>"
            f"<td>{html.escape(row['region'])}</td>"
            f"<td>{html.escape(row['objective'])}</td>"
            f"<td>{format_currency(row['spend'])}</td>"
            f"<td>{format_pct(row['ctr'])}</td>"
            f"<td>{format_currency(row['cpc'])}</td>"
            f"<td>{format_currency(row['cpl'])}</td>"
            f"<td>{format_pct(row['conversion_rate'])}</td>"
            f"<td>{final_cell}</td>"
            "</tr>"
        )
    return "\n".join(row_html)


def render_trend_cards(trends: list[dict[str, Any]]) -> str:
    if not trends:
        return '<div class="empty-state">No previous reporting day exists for trend comparison.</div>'
    cards = []
    for trend in trends:
        cards.append(
            '<article class="trend-card">'
            f"<p class=\"eyebrow\">{html.escape(trend['label'])}</p>"
            f"<strong>{html.escape(trend['current_display'])}</strong>"
            f"{tone_chip(trend['delta_display'], trend['tone'])}"
            f"<span class=\"trend-note\">Previous: {html.escape(trend['previous_display'])}</span>"
            "</article>"
        )
    return "".join(cards)


def render_risk_table(risks: list[dict[str, str]]) -> str:
    if not risks:
        return '<tr><td colspan="4" class="empty-state">No control risks were generated for this report.</td></tr>'
    rows = []
    for risk in risks:
        rows.append(
            "<tr>"
            f"<td>{html.escape(risk['area'])}</td>"
            f"<td>{html.escape(risk['severity'].title())}</td>"
            f"<td>{html.escape(risk['evidence'])}</td>"
            f"<td>{html.escape(risk['next_step'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_movement_table(movements: list[dict[str, Any]]) -> str:
    if not movements:
        return '<tr><td colspan="6" class="empty-state">No comparable campaign movement exists for the latest reporting day.</td></tr>'
    rows = []
    for movement in movements:
        rows.append(
            "<tr>"
            f"<td>{html.escape(movement['campaign_key'])}</td>"
            f"<td>{format_currency(movement['previous_spend'])}</td>"
            f"<td>{format_currency(movement['current_spend'])}</td>"
            f"<td>{format_currency(movement['spend_delta'])}</td>"
            f"<td>{format_delta_pct(movement['ctr_delta_pct'])}</td>"
            f"<td>{format_number(movement['lead_delta'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_html(report: dict[str, Any]) -> str:
    latest_cards = build_metric_cards(report["latest_summary"], report["targets"])
    month_cards = build_metric_cards(report["month_summary"], report["targets"])
    pacing = report["pacing"]
    actions_markup = "".join(f"<li>{html.escape(item)}</li>" for item in report["action_list"])
    analyst_markup = "".join(f"<li>{html.escape(item)}</li>" for item in report["analyst_note_lines"])
    assumptions_markup = "".join(f"<li>{html.escape(item)}</li>" for item in report["assumptions"])
    trend_markup = render_trend_cards(report["daily_trends"])
    risk_markup = render_risk_table(report["risk_register"])
    movement_markup = render_movement_table(report["campaign_movements"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LinkedIn Ads Control Report</title>
  <style>
    :root {{ --ink:#17212b; --muted:#586570; --paper:#ffffff; --surface:#f5f3ee; --line:#d9d6cd; --good:#0c7b5d; --warn:#a96400; --bad:#b43f2f; --shadow:0 1px 2px rgba(23,33,43,.08); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"IBM Plex Sans","Segoe UI",sans-serif; color:var(--ink); background:var(--surface); line-height:1.5; }}
    .shell {{ width:min(1240px,calc(100vw - 48px)); margin:28px auto 48px; }}
    .hero {{ background:#17212b; color:#f8fbfd; border-radius:12px; padding:28px 30px 24px; box-shadow:var(--shadow); }}
    .hero h1 {{ margin:0 0 12px; font-size:clamp(2rem,5vw,3rem); line-height:1.05; letter-spacing:0; }}
    .hero p {{ margin:0; max-width:840px; color:#dce3e8; }}
    .hero-meta {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-top:26px; }}
    .review-flow {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }}
    .flow-card {{ border:1px solid rgba(255,255,255,.18); border-radius:10px; padding:14px; background:rgba(255,255,255,.06); }}
    .flow-card span {{ display:block; color:#aebbc6; text-transform:uppercase; letter-spacing:.08em; font-size:.72rem; margin-bottom:6px; }}
    .flow-card strong {{ display:block; color:#ffffff; font-size:1rem; line-height:1.3; }}
    .meta-card,.panel {{ background:var(--paper); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }}
    .meta-card {{ padding:18px 20px; }}
    .meta-card p {{ margin:0 0 6px; color:var(--muted); font-size:.88rem; text-transform:uppercase; letter-spacing:.06em; }}
    .meta-card strong {{ display:block; font-size:1.5rem; color:var(--ink); }}
    nav {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 0; }}
    nav a {{ color:#f8fbfd; text-decoration:none; border:1px solid rgba(255,255,255,.24); padding:8px 12px; border-radius:8px; font-size:.92rem; }}
    .layout {{ display:grid; grid-template-columns:2.2fr 1fr; gap:20px; margin-top:22px; }}
    .stack {{ display:grid; gap:20px; }}
    .panel {{ padding:22px; }}
    .section-title {{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:18px; }}
    .section-title h2,.section-title h3 {{ margin:0; font-size:1.18rem; letter-spacing:0; }}
    .muted {{ color:var(--muted); }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
    .metric-tile,.strip-card {{ background:#fbfaf7; border:1px solid var(--line); border-radius:10px; padding:16px; }}
    .eyebrow {{ margin:0 0 10px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; font-size:.78rem; }}
    .metric-row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
    .metric-row strong,.strip-card strong {{ font-size:1.35rem; letter-spacing:0; display:block; margin-top:6px; }}
    .chip {{ display:inline-flex; align-items:center; border-radius:7px; padding:7px 10px; font-size:.8rem; font-weight:600; white-space:nowrap; }}
    .chip.good {{ color:var(--good); background:rgba(12,123,93,.12); }} .chip.warn {{ color:var(--warn); background:rgba(184,110,6,.12); }} .chip.bad {{ color:var(--bad); background:rgba(180,63,47,.13); }} .chip.muted {{ color:var(--muted); background:rgba(88,112,129,.12); }}
    .summary-list,.assumption-list {{ margin:0; padding-left:20px; }} .summary-list li,.assumption-list li {{ margin-bottom:10px; }}
    .pacing-strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:16px; }}
    .trend-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
    .trend-card {{ background:#fbfaf7; border:1px solid var(--line); border-radius:10px; padding:15px; }}
    .trend-card strong {{ display:block; margin:0 0 10px; font-size:1.25rem; }}
    .trend-note {{ display:block; margin-top:10px; color:var(--muted); font-size:.86rem; }}
    .data-table {{ width:100%; border-collapse:collapse; font-size:.92rem; }}
    .data-table th,.data-table td {{ text-align:left; padding:12px 10px; border-bottom:1px solid rgba(23,50,68,.08); vertical-align:top; }}
    .data-table th {{ color:var(--muted); font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; }}
    .two-up {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:20px; }}
    .detail,.footer-note,.empty-state {{ color:var(--muted); }} .detail,.footer-note {{ margin-top:12px; font-size:.92rem; }}
    @media (max-width:980px) {{ .layout,.two-up,.review-flow {{ grid-template-columns:1fr; }} .shell {{ width:min(100vw - 24px,1240px); margin-top:12px; }} .hero {{ padding:24px 20px 22px; }} .panel {{ padding:18px; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero" id="overview">
      <h1>LinkedIn Ads Control Report</h1>
      <p>A deterministic marketing-ops report that turns the raw LinkedIn Ads export into KPI health, pacing, movement, risks, and campaign actions.</p>
      <div class="hero-meta">
        <div class="meta-card"><p>Latest data date</p><strong>{html.escape(report['latest_date'])}</strong></div>
        <div class="meta-card"><p>Reporting month</p><strong>{html.escape(report['report_month'])}</strong></div>
        <div class="meta-card"><p>Monthly budget</p><strong>{format_currency(pacing['monthly_budget'])}</strong></div>
        <div class="meta-card"><p>Pacing status</p><strong>{html.escape(pacing['label'])}</strong></div>
        <div class="meta-card"><p>Scoring mode</p><strong>Deterministic rules</strong></div>
      </div>
      <div class="review-flow" aria-label="Daily review workflow">
        <div class="flow-card"><span>Input</span><strong>Campaign export or fixture CSV</strong></div>
        <div class="flow-card"><span>Review artifact</span><strong>KPI cards, pacing, and ranked actions</strong></div>
        <div class="flow-card"><span>Decision handoff</span><strong>Open campaigns to inspect first</strong></div>
      </div>
      <nav><a href="#kpis">KPI snapshot</a><a href="#actions">Action list</a><a href="#pacing">Budget pacing</a><a href="#campaigns">Campaign review</a><a href="#assumptions">Assumptions</a></nav>
    </section>
    <section class="layout" id="kpis">
      <div class="panel">
        <div class="section-title"><h2>Latest-day KPI snapshot</h2><span class="muted">{html.escape(report['latest_date'])}</span></div>
        <div class="metric-grid">{render_metric_tile(latest_cards['cpc'])}{render_metric_tile(latest_cards['ctr'])}{render_metric_tile(latest_cards['cpl'])}{render_metric_tile(latest_cards['conversion_rate'])}</div>
        <div class="section-title" style="margin-top:24px;"><h3>Month-to-date KPI snapshot</h3><span class="muted">{html.escape(report['report_month'])}</span></div>
        <div class="metric-grid">{render_metric_tile(month_cards['cpc'])}{render_metric_tile(month_cards['ctr'])}{render_metric_tile(month_cards['cpl'])}{render_metric_tile(month_cards['conversion_rate'])}</div>
      </div>
      <aside class="stack">
        <section class="panel" id="actions"><div class="section-title"><h2>Today's action list</h2>{tone_chip("Deterministic", "muted")}</div><ul class="summary-list">{actions_markup}</ul></section>
        <section class="panel"><div class="section-title"><h2>Analyst note</h2>{tone_chip(report['analyst_note_source'], 'muted')}</div><ul class="summary-list">{analyst_markup}</ul><p class="detail">{html.escape(report['analyst_note_detail'])}</p></section>
      </aside>
    </section>
    <section class="panel" id="pacing">
      <div class="section-title"><h2>Budget pacing</h2>{tone_chip(pacing['label'], pacing['tone'])}</div>
      <p class="muted">Checks whether spend is below monthly budget and still trending toward full budget delivery by month end.</p>
      <div class="pacing-strip">
        <div class="strip-card"><p class="eyebrow">Spend MTD</p><strong>{format_currency(pacing['spend_mtd'])}</strong></div>
        <div class="strip-card"><p class="eyebrow">Planned spend to date</p><strong>{format_currency(pacing['planned_spend_to_date'])}</strong></div>
        <div class="strip-card"><p class="eyebrow">Projected month-end spend</p><strong>{format_currency(pacing['projected_month_end_spend'])}</strong></div>
        <div class="strip-card"><p class="eyebrow">Projected gap to budget</p><strong>{format_currency(pacing['projected_gap'])}</strong></div>
      </div>
    </section>
    <section class="panel" id="control-risks" style="margin-top:20px;">
      <div class="section-title"><h2>Control risks</h2><span class="muted">Prioritized review register</span></div>
      <table class="data-table"><thead><tr><th>Area</th><th>Severity</th><th>Evidence</th><th>Next step</th></tr></thead><tbody>{risk_markup}</tbody></table>
    </section>
    <section class="panel" id="trends" style="margin-top:20px;">
      <div class="section-title"><h2>Daily movement</h2><span class="muted">Previous reporting day comparison</span></div>
      <div class="trend-grid">{trend_markup}</div>
    </section>
    <section class="two-up" id="campaigns">
      <div class="panel">
        <div class="section-title"><h2>Campaigns to review first</h2><span class="muted">Top {len(report['alerts'])}</span></div>
        <table class="data-table"><thead><tr><th>Campaign</th><th>Region</th><th>Objective</th><th>Spend</th><th>CTR</th><th>CPC</th><th>CPL</th><th>Conv. rate</th><th>Issue</th></tr></thead><tbody>{render_campaign_table(report['alerts'], 'No campaigns breached the rules for this date.', True)}</tbody></table>
      </div>
      <div class="panel">
        <div class="section-title"><h2>Best campaigns on latest day</h2><span class="muted">Top {len(report['wins'])}</span></div>
        <table class="data-table"><thead><tr><th>Campaign</th><th>Region</th><th>Objective</th><th>Spend</th><th>CTR</th><th>CPC</th><th>CPL</th><th>Conv. rate</th><th>Status</th></tr></thead><tbody>{render_campaign_table(report['wins'], 'No clear winners met the minimum volume filters.', False)}</tbody></table>
      </div>
    </section>
    <section class="panel" id="movement" style="margin-top:20px;">
      <div class="section-title"><h2>Campaign movement</h2><span class="muted">Latest vs previous reporting day</span></div>
      <table class="data-table"><thead><tr><th>Campaign group</th><th>Previous spend</th><th>Current spend</th><th>Spend delta</th><th>CTR delta</th><th>Lead delta</th></tr></thead><tbody>{movement_markup}</tbody></table>
    </section>
    <section class="panel" id="assumptions" style="margin-top:20px;">
      <div class="section-title"><h2>Assumptions</h2></div>
      <ul class="assumption-list">{assumptions_markup}</ul>
      <p class="footer-note">Effective source: {html.escape(report['effective_source'])}. The deterministic path is the default behavior. Optional live AI only affects the analyst note, never the KPI calculations.</p>
    </section>
  </main>
</body>
</html>
"""


def render_markdown_summary(report: dict[str, Any]) -> str:
    action_lines = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(report["action_list"]))
    risk_lines = "\n".join(
        f"- {risk['severity'].title()} - {risk['area']}: {risk['evidence']} Next: {risk['next_step']}"
        for risk in report["risk_register"]
    ) or "- No control risks were generated for this report."
    trend_lines = "\n".join(
        f"- {trend['label']}: {trend['current_display']} vs {trend['previous_display']} ({trend['delta_display']})"
        for trend in report["daily_trends"]
    ) or "- No previous reporting day exists for comparison."
    alert_lines = "\n".join(
        f"- {alert['campaign_name']}: {'; '.join(alert['reasons'])}"
        for alert in report["alerts"][:5]
    ) or "- No campaign breaches were detected on the latest date."
    analyst_lines = "\n".join(f"- {line}" for line in report["analyst_note_lines"])
    return (
        "# LinkedIn Ads Daily Check\n\n"
        f"Generated at: {report['generated_at']}\n"
        f"Latest data date: {report['latest_date']}\n"
        f"Reporting month: {report['report_month']}\n"
        f"Effective source: {report['effective_source']}\n"
        f"Narrative mode: {report['analyst_note_source']}\n\n"
        "## Account KPI Snapshot\n\n"
        f"- CPC: {format_currency(report['latest_summary']['cpc'])}\n"
        f"- CTR: {format_pct(report['latest_summary']['ctr'])}\n"
        f"- CPL: {format_currency(report['latest_summary']['cpl'])}\n"
        f"- Conversion rate: {format_pct(report['latest_summary']['conversion_rate'])}\n"
        f"- Pacing verdict: {report['pacing']['label']} with projected month-end spend {format_currency(report['pacing']['projected_month_end_spend'])}\n\n"
        "## Today's Action List\n\n"
        f"{action_lines}\n\n"
        "## Control Risks\n\n"
        f"{risk_lines}\n\n"
        "## Daily Movement\n\n"
        f"{trend_lines}\n\n"
        "## Campaigns To Review First\n\n"
        f"{alert_lines}\n\n"
        "## Analyst Note\n\n"
        f"{analyst_lines}\n\n"
        "## Optional Analyst Note Mode\n\n"
        "- A live analyst note can be enabled at runtime.\n"
        "- When disabled, the report falls back to deterministic rule-based notes.\n"
        "- KPI scoring, pacing logic, and campaign ranking remain deterministic.\n"
    )


def ensure_budget(monthly_budget: float | None) -> float:
    if monthly_budget is None or monthly_budget <= 0:
        raise ValueError("A positive monthly budget is required. Pass --monthly-budget or set it in the config file.")
    return monthly_budget


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    config_from_file = load_config(args.config)
    config = deep_merge(
        {"csv_url": None, "csv_path": DEFAULT_SAMPLE_CSV_PATH, "monthly_budget": None, "targets": DEFAULT_TARGETS, "ai": DEFAULT_AI_CONFIG},
        config_from_file,
    )
    if args.csv_url:
        config["csv_url"] = args.csv_url
    if args.csv_path:
        config["csv_path"] = args.csv_path
    if args.monthly_budget is not None:
        config["monthly_budget"] = args.monthly_budget
    if args.enable_ai_summary:
        config["ai"] = deep_merge(config["ai"], {"enabled": True})

    configured_csv_path = config.get("csv_path")
    effective_source = Path(configured_csv_path).as_posix() if configured_csv_path else config["csv_url"]
    rows = normalize_rows(load_rows(config["csv_url"], configured_csv_path))
    report_month = pick_report_month(rows, args.month)
    month_rows = [row for row in rows if row["date"].strftime("%Y-%m") == report_month]
    if not month_rows:
        raise ValueError(f"No data found for month {report_month}.")
    latest_date = max(row["date"] for row in month_rows)
    latest_rows = [row for row in month_rows if row["date"] == latest_date]

    latest_summary = aggregate(latest_rows)
    month_summary = aggregate(month_rows)
    targets = config["targets"]
    latest_statuses = {metric: evaluate_metric(metric, latest_summary[metric], targets) for metric in ("cpc", "ctr", "cpl", "conversion_rate")}
    month_statuses = {metric: evaluate_metric(metric, month_summary[metric], targets) for metric in ("cpc", "ctr", "cpl", "conversion_rate")}
    pacing = build_pacing(ensure_budget(config["monthly_budget"]), month_summary["spend"] or 0.0, latest_date)
    alerts = build_campaign_alerts(latest_rows, targets, args.top_campaigns)
    wins = build_campaign_wins(latest_rows, targets, args.top_campaigns, {alert["campaign_name"] for alert in alerts})
    daily_trends = build_daily_trends(month_rows, latest_date)
    campaign_movements = build_campaign_movements(month_rows, latest_date, args.top_campaigns)
    risk_register = build_risk_register(latest_statuses, pacing, alerts, daily_trends)

    assumptions = [
        "The Google Sheet or CSV input is treated as the source of truth and contains one row per campaign per day.",
        "Campaign names are expected in the 7-part or 8-part format used by the sample export.",
        "Conversion rate is calculated as Conversions / Clicks to match the campaign export KPI definition.",
        "CPL is calculated as Total spent / Leads and shown as N/A when a campaign generated no leads.",
        "Budget pacing needs a monthly budget input because the source data does not include budget values.",
        "A live analyst note can be enabled at runtime, but KPI scoring, pacing logic, and campaign ranking remain deterministic.",
    ]

    report_payload = {
        "latest_date": latest_date.isoformat(),
        "report_month": report_month,
        "latest_summary": latest_summary,
        "month_summary": month_summary,
        "latest_statuses": latest_statuses,
        "month_statuses": month_statuses,
        "pacing": pacing,
        "targets": targets,
        "daily_trends": daily_trends,
        "risk_register": risk_register,
        "campaign_movements": campaign_movements,
        "top_alerts": [{"campaign_name": alert["campaign_name"], "region": alert["region"], "objective": alert["objective"], "spend": alert["spend"], "reasons": alert["reasons"]} for alert in alerts[:5]],
    }
    fallback_lines = build_rule_based_analyst_lines(latest_summary, month_summary, pacing, latest_statuses, month_statuses, alerts)
    summary_result = generate_analyst_note(report_payload, ai_config_from_dict(config["ai"]), fallback_lines)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_date": latest_date.isoformat(),
        "report_month": report_month,
        "latest_summary": latest_summary,
        "month_summary": month_summary,
        "latest_statuses": latest_statuses,
        "month_statuses": month_statuses,
        "pacing": pacing,
        "alerts": alerts,
        "wins": wins,
        "daily_trends": daily_trends,
        "campaign_movements": campaign_movements,
        "risk_register": risk_register,
        "targets": targets,
        "assumptions": assumptions,
        "action_list": build_action_list(latest_summary, latest_statuses, pacing, alerts, wins, risk_register),
        "analyst_note_lines": summary_result.lines,
        "analyst_note_source": summary_result.source_label,
        "analyst_note_detail": summary_result.detail,
        "source_url": config["csv_url"],
        "effective_source": effective_source,
    }


def write_artifacts(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = report["latest_date"]
    paths = {
        "html_timestamped": output_dir / f"linkedin_daily_check_{suffix}.html",
        "html_latest": output_dir / "latest_report.html",
        "json_timestamped": output_dir / f"linkedin_daily_check_{suffix}.json",
        "json_latest": output_dir / "latest_report.json",
        "summary_timestamped": output_dir / f"linkedin_daily_check_{suffix}_summary.md",
        "summary_latest": output_dir / "latest_summary.md",
    }
    report_html = render_html(report)
    report_json = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    report_summary = render_markdown_summary(report)
    for key, path in paths.items():
        if key.startswith("html"):
            path.write_text(report_html, encoding="utf-8")
        elif key.startswith("json"):
            path.write_text(report_json, encoding="utf-8")
        else:
            path.write_text(report_summary, encoding="utf-8")
    return paths


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = write_artifacts(build_report(args), Path(args.output_dir))
    print(f"HTML report written to {paths['html_timestamped']}")
    print(f"Latest HTML report written to {paths['html_latest']}")
    print(f"JSON snapshot written to {paths['json_latest']}")
    print(f"Markdown summary written to {paths['summary_latest']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
