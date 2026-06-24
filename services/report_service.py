"""
services/report_service.py

ServerGuardian Executive Reporting Engine.

Generates structured weekly and monthly platform health reports from
monitoring_history data (no pre-aggregation required). Supports:

  - Weekly report  (last 7 days)
  - Monthly report (last 30 days)
  - Service benchmarks (uptime rank, latency rank, incident rank)
  - SLA compliance summary

Reports are cached in the 'reports_log' collection to support idempotent
email dispatch from monitor_runner.py.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

SLA_TARGET_PCT = float(os.getenv("SLA_TARGET_PCT", "99.0"))


# ── Main Report Generators ────────────────────────────────────────────────────

def generate_report(mongo_client, days: int) -> dict:
    """
    Generate a platform report covering the last `days` days.

    Returns a dict with:
      period, overall_uptime_pct, total_checks, total_failures,
      incidents_this_period, avg_mttr_seconds, sla_target_pct, services[]
    """
    try:
        from services.analytics_service import get_platform_summary, get_latency_stats
        from config import SERVICES_CONFIG

        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        period_start = cutoff.strftime("%Y-%m-%d")
        period_end = now.strftime("%Y-%m-%d")

        # Platform-level aggregate (1 pipeline)
        platform_pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "successes": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "failures": {"$sum": {"$cond": [{"$ne": ["$status", "success"]}, 1, 0]}},
                "avg_latency": {"$avg": "$latency_ms"},
            }}
        ]
        platform_agg = list(col.aggregate(platform_pipeline))
        if platform_agg:
            p = platform_agg[0]
            total_checks = p.get("total", 0)
            total_successes = p.get("successes", 0)
            total_failures = p.get("failures", 0)
            platform_uptime = round(total_successes / total_checks * 100, 2) if total_checks else 100.0
            avg_latency = round(p.get("avg_latency") or 0.0, 1)
        else:
            total_checks = total_successes = total_failures = 0
            platform_uptime = 100.0
            avg_latency = None

        # Incident metrics
        incident_metrics = _get_incident_metrics(days, mongo_client)

        # Per-service summaries (1 pipeline for all services)
        svc_pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}, "service_id": {"$exists": True}}},
            {"$group": {
                "_id": "$service_id",
                "service_name": {"$last": "$service_name"},
                "total": {"$sum": 1},
                "successes": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "failures": {"$sum": {"$cond": [{"$ne": ["$status", "success"]}, 1, 0]}},
                "avg_latency": {"$avg": "$latency_ms"},
                "max_latency": {"$max": "$latency_ms"},
                "min_latency": {"$min": "$latency_ms"},
            }}
        ]
        svc_agg = list(col.aggregate(svc_pipeline))

        services = []
        for s in svc_agg:
            svc_id = s["_id"]
            svc_total = s.get("total", 0)
            svc_ok = s.get("successes", 0)
            svc_fail = s.get("failures", 0)
            uptime_pct = round(svc_ok / svc_total * 100, 2) if svc_total > 0 else 100.0
            sla_met = uptime_pct >= SLA_TARGET_PCT

            services.append({
                "service_id": svc_id,
                "service_name": s.get("service_name") or svc_id,
                f"uptime_{days}d": uptime_pct,
                "reliability_rating": _get_reliability_rating(uptime_pct),
                "total_checks": svc_total,
                "failure_count": svc_fail,
                "avg_latency_ms": round(s.get("avg_latency") or 0.0, 1),
                "max_latency_ms": round(s.get("max_latency") or 0.0, 1),
                "min_latency_ms": round(s.get("min_latency") or 0.0, 1),
                "sla_target_pct": SLA_TARGET_PCT,
                "sla_met": sla_met,
            })

        # Sort by uptime descending
        services.sort(key=lambda x: x.get(f"uptime_{days}d", 0), reverse=True)

        return {
            "period": f"{period_start} to {period_end}",
            "period_days": days,
            "generated_at": now.isoformat(),
            "overall_uptime_pct": platform_uptime,
            "total_checks": total_checks,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "avg_latency_ms": avg_latency,
            "sla_target_pct": SLA_TARGET_PCT,
            "services_above_sla": sum(1 for s in services if s["sla_met"]),
            "services_below_sla": sum(1 for s in services if not s["sla_met"]),
            "incidents_this_period": incident_metrics.get("total_incidents", 0),
            "avg_mttr_seconds": incident_metrics.get("avg_mttr_seconds"),
            "avg_mttd_seconds": incident_metrics.get("avg_mttd_seconds"),
            "services": services,
        }

    except Exception as e:
        logging.error(f"[ReportService] Failed to generate {days}d report: {e}")
        return {
            "period": "N/A",
            "period_days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_uptime_pct": 100.0,
            "total_checks": 0,
            "total_successes": 0,
            "total_failures": 0,
            "avg_latency_ms": None,
            "sla_target_pct": SLA_TARGET_PCT,
            "services_above_sla": 0,
            "services_below_sla": 0,
            "incidents_this_period": 0,
            "avg_mttr_seconds": None,
            "avg_mttd_seconds": None,
            "services": [],
        }


def generate_weekly_report(mongo_client) -> dict:
    """Generate a 7-day platform health report."""
    return generate_report(mongo_client, days=7)


def generate_monthly_report(mongo_client) -> dict:
    """Generate a 30-day platform health report."""
    return generate_report(mongo_client, days=30)


def generate_benchmarks(mongo_client) -> dict:
    """
    Generate service benchmarking table.
    Ranks services by uptime, latency, and incident frequency.
    """
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        svc_pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}, "service_id": {"$exists": True}}},
            {"$group": {
                "_id": "$service_id",
                "service_name": {"$last": "$service_name"},
                "total": {"$sum": 1},
                "successes": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "avg_latency": {"$avg": "$latency_ms"},
                "p95_latency": {"$percentile": {"input": "$latency_ms", "p": [0.95], "method": "approximate"}}
                if False else None,  # MongoDB 7+ only, skip
            }}
        ]

        # Simpler pipeline without $percentile for compatibility
        svc_pipeline_compat = [
            {"$match": {"timestamp": {"$gte": cutoff}, "service_id": {"$exists": True}}},
            {"$group": {
                "_id": "$service_id",
                "service_name": {"$last": "$service_name"},
                "total": {"$sum": 1},
                "successes": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "avg_latency": {"$avg": "$latency_ms"},
            }}
        ]
        svc_agg = list(col.aggregate(svc_pipeline_compat))

        # Get incident counts per service for last 30d
        incident_counts = {}
        try:
            inc_agg = list(db["incidents"].aggregate([
                {"$match": {"started_at": {"$gte": cutoff}}},
                {"$group": {"_id": "$service_id", "count": {"$sum": 1}}}
            ]))
            incident_counts = {r["_id"]: r["count"] for r in inc_agg}
        except Exception:
            pass

        services = []
        for s in svc_agg:
            svc_id = s["_id"]
            total = s.get("total", 0)
            ok = s.get("successes", 0)
            uptime_pct = round(ok / total * 100, 2) if total > 0 else 100.0
            avg_lat = round(s.get("avg_latency") or 0.0, 1)
            incident_count = incident_counts.get(svc_id, 0)

            services.append({
                "service_id": svc_id,
                "service_name": s.get("service_name") or svc_id,
                "uptime_30d": uptime_pct,
                "avg_latency_ms": avg_lat,
                "incident_count_30d": incident_count,
                "total_checks": total,
                "reliability_rating": _get_reliability_rating(uptime_pct),
                "sla_met": uptime_pct >= SLA_TARGET_PCT,
            })

        # Assign ranks
        sorted_by_uptime = sorted(services, key=lambda x: x["uptime_30d"], reverse=True)
        sorted_by_latency = sorted(services, key=lambda x: x["avg_latency_ms"])
        sorted_by_incidents = sorted(services, key=lambda x: x["incident_count_30d"])

        uptime_rank = {s["service_id"]: i + 1 for i, s in enumerate(sorted_by_uptime)}
        latency_rank = {s["service_id"]: i + 1 for i, s in enumerate(sorted_by_latency)}
        incident_rank = {s["service_id"]: i + 1 for i, s in enumerate(sorted_by_incidents)}

        for s in services:
            sid = s["service_id"]
            s["uptime_rank"] = uptime_rank.get(sid, 0)
            s["latency_rank"] = latency_rank.get(sid, 0)
            s["incident_rank"] = incident_rank.get(sid, 0)

        # Sort final output by uptime rank
        services.sort(key=lambda x: x["uptime_rank"])

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": 30,
            "sla_target_pct": SLA_TARGET_PCT,
            "services": services,
        }

    except Exception as e:
        logging.error(f"[ReportService] generate_benchmarks error: {e}")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": 30,
            "sla_target_pct": SLA_TARGET_PCT,
            "services": [],
        }


# ── Report Dispatch Log (Idempotency) ─────────────────────────────────────────

def should_send_report(report_type: str, period_key: str, mongo_client) -> bool:
    """
    Check if a report has already been sent for this period.
    report_type: 'weekly' or 'monthly'
    period_key: ISO week/month string e.g. '2026-W25' or '2026-06'
    """
    try:
        db = mongo_client["ServerAutomation"]
        existing = db["reports_log"].find_one({
            "report_type": report_type,
            "period_key": period_key
        })
        return existing is None
    except Exception:
        return True


def log_report_sent(report_type: str, period_key: str, sent: bool, mongo_client):
    """Record a report dispatch in the idempotency log."""
    try:
        db = mongo_client["ServerAutomation"]
        db["reports_log"].update_one(
            {"report_type": report_type, "period_key": period_key},
            {"$set": {
                "sent": sent,
                "sent_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )
    except Exception as e:
        logging.warning(f"[ReportService] Failed to log report dispatch: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_incident_metrics(days: int, mongo_client) -> dict:
    try:
        from services.incident_service import get_incident_metrics
        return get_incident_metrics(days, mongo_client)
    except Exception:
        return {"total_incidents": 0, "avg_mttr_seconds": None, "avg_mttd_seconds": None}


def _get_reliability_rating(uptime_pct: float) -> str:
    excellent = float(os.getenv("RELIABILITY_EXCELLENT", "99"))
    good = float(os.getenv("RELIABILITY_GOOD", "95"))
    warning = float(os.getenv("RELIABILITY_WARNING", "90"))
    if uptime_pct >= excellent:
        return "Excellent"
    elif uptime_pct >= good:
        return "Good"
    elif uptime_pct >= warning:
        return "Warning"
    else:
        return "Critical"


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m {seconds % 60:.0f}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:.0f}h {m:.0f}m"
