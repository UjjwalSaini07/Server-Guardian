"""
services/analytics_service.py  (v2 – Enterprise Analytics Engine)

Full replacement of the thin v1 stub.

Design principles
-----------------
* monitoring_history is the **single source of truth** – no extra collections.
* All functions use MongoDB aggregation pipelines to avoid N+1 queries.
* Every function accepts a plain PyMongo MongoClient so it works in both the
  synchronous monitor_runner.py context and the FastAPI dashboard.
* Functions are thin and composable so executive summaries / reports can batch
  results without re-running pipelines.
* Reliability thresholds are read from environment variables with sensible
  defaults (RELIABILITY_EXCELLENT, RELIABILITY_GOOD, RELIABILITY_WARNING).
"""

from __future__ import annotations

import logging
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from config import SERVICES_CONFIG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable Thresholds (env-driven, default to 99 / 95 / 90)
# ---------------------------------------------------------------------------

_THRESHOLD_EXCELLENT: float = float(os.getenv("RELIABILITY_EXCELLENT", "99"))
_THRESHOLD_GOOD: float      = float(os.getenv("RELIABILITY_GOOD",      "95"))
_THRESHOLD_WARNING: float   = float(os.getenv("RELIABILITY_WARNING",   "90"))

MONITOR_VERSION: str = "1.0"


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _get_col(mongo_client, db_name: str = "ServerAutomation", col_name: str = "monitoring_history"):
    return mongo_client[db_name][col_name]


def _cutoff(days: Optional[int]) -> Optional[datetime]:
    """Return a UTC datetime N days ago, or None for all-time."""
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _build_match(
    service_id: Optional[str],
    days: Optional[int],
    extra: Optional[dict] = None,
) -> dict:
    """Build a $match stage dict for a monitoring_history query."""
    match: dict = {}
    if service_id:
        match["service_id"] = service_id
    cut = _cutoff(days)
    if cut:
        match["timestamp"] = {"$gte": cut}
    if extra:
        match.update(extra)
    return match


def _percentile(sorted_values: List[float], pct: float) -> Optional[float]:
    """Compute an approximate percentile (nearest-rank method) from a sorted list."""
    if not sorted_values:
        return None
    n = len(sorted_values)
    idx = max(0, int(round(pct / 100.0 * n)) - 1)
    return round(sorted_values[min(idx, n - 1)], 2)


def _latency_percentiles_from_list(values: List[float]) -> dict:
    """
    Given a list of latency floats, compute all required percentile stats.
    Returns a dict matching LatencyPercentiles fields.
    """
    if not values:
        return {
            "avg_ms": None, "min_ms": None, "max_ms": None,
            "median_ms": None, "p95_ms": None, "p99_ms": None,
            "sample_count": 0
        }
    sv = sorted(values)
    return {
        "avg_ms":    round(sum(sv) / len(sv), 2),
        "min_ms":    round(sv[0], 2),
        "max_ms":    round(sv[-1], 2),
        "median_ms": round(statistics.median(sv), 2),
        "p95_ms":    _percentile(sv, 95),
        "p99_ms":    _percentile(sv, 99),
        "sample_count": len(sv),
    }


# ---------------------------------------------------------------------------
# Reliability Rating
# ---------------------------------------------------------------------------

def get_reliability_rating(uptime_pct: float) -> str:
    """
    Map an uptime percentage to a human-readable reliability tier.
    Thresholds are driven by RELIABILITY_EXCELLENT / RELIABILITY_GOOD /
    RELIABILITY_WARNING environment variables.
    """
    if uptime_pct >= _THRESHOLD_EXCELLENT:
        return "Excellent"
    elif uptime_pct >= _THRESHOLD_GOOD:
        return "Good"
    elif uptime_pct >= _THRESHOLD_WARNING:
        return "Warning"
    else:
        return "Critical"


# ---------------------------------------------------------------------------
# Trend Indicator
# ---------------------------------------------------------------------------

def get_trend_indicator(service_id: str, mongo_client) -> str:
    """
    Compare 24-hour uptime against 7-day uptime to determine direction.
    Returns ↑ (Improving), → (Stable), or ↓ (Degrading).
    """
    up_24h, _, _ = get_uptime_counts(service_id, mongo_client, days=1)
    up_7d,  _, _ = get_uptime_counts(service_id, mongo_client, days=7)
    if up_24h > up_7d + 0.5:
        return "↑"
    elif up_24h < up_7d - 0.5:
        return "↓"
    return "→"


# ---------------------------------------------------------------------------
# Consecutive Outage Detection
# ---------------------------------------------------------------------------

def detect_consecutive_outages(
    service_id: str,
    mongo_client,
    threshold: int = 3,
) -> bool:
    """
    Return True if the last `threshold` checks for service_id all failed.
    Uses a lean sort+limit query – no aggregation needed.
    """
    try:
        col = _get_col(mongo_client)
        records = list(
            col.find({"service_id": service_id})
               .sort("timestamp", -1)
               .limit(threshold)
        )
        if len(records) < threshold:
            return False
        return all(r.get("status") == "failure" for r in records)
    except Exception as exc:
        logger.error("[AnalyticsService] detect_consecutive_outages error for %s: %s", service_id, exc)
        return False


# ---------------------------------------------------------------------------
# Core: Uptime Counts (single aggregation pipeline)
# ---------------------------------------------------------------------------

def get_uptime_counts(
    service_id: str,
    mongo_client,
    days: Optional[int] = 30,
) -> Tuple[float, int, int]:
    """
    Return (uptime_pct, success_count, failure_count) for a service.
    Uses a single $group aggregation – never fetches raw documents.
    """
    try:
        col = _get_col(mongo_client)
        match = _build_match(service_id, days)

        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": None,
                "total":   {"$sum": 1},
                "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            }},
        ]
        result = list(col.aggregate(pipeline))
        if not result:
            return 100.0, 0, 0

        total   = result[0].get("total", 0)
        success = result[0].get("success", 0)
        failure = total - success
        pct     = round((success / total) * 100.0, 2) if total > 0 else 100.0
        return pct, success, failure

    except Exception as exc:
        logger.error("[AnalyticsService] get_uptime_counts error for %s: %s", service_id, exc)
        return 100.0, 0, 0


# ---------------------------------------------------------------------------
# Core: Full UptimeStats dict
# ---------------------------------------------------------------------------

def get_uptime(
    service_id: str,
    service_name: str,
    mongo_client,
    days: Optional[int] = 30,
    sla_target: float = 99.9,
) -> dict:
    """
    Return a dict matching the UptimeStats model for a single service.
    """
    uptime_pct, success, failure = get_uptime_counts(service_id, mongo_client, days)
    return {
        "service_id":     service_id,
        "service_name":   service_name,
        "days":           days,
        "uptime_pct":     uptime_pct,
        "total_checks":   success + failure,
        "success_checks": success,
        "failure_checks": failure,
        "sla_target_pct": sla_target,
        "sla_met":        uptime_pct >= sla_target,
    }


# ---------------------------------------------------------------------------
# Core: Latency Stats (aggregation pipeline – no raw doc fetch)
# ---------------------------------------------------------------------------

def get_latency_stats(
    service_id: str,
    service_name: str,
    mongo_client,
    days: Optional[int] = 30,
) -> dict:
    """
    Return a dict matching the LatencyStats model.

    Strategy:
    - Use MongoDB $group to get avg/min/max cheaply.
    - Fetch latency values for the full P50/P95/P99 calculation.
      This is acceptable at current scale; a $percentile stage (MongoDB 7+) can
      replace this later without changing the function signature.
    """
    try:
        col   = _get_col(mongo_client)
        match = _build_match(service_id, days, extra={"latency_ms": {"$exists": True, "$ne": None}})

        # Fetch all latency values (lean projection)
        raw = list(col.find(match, {"latency_ms": 1, "_id": 0}))
        latency_values = [r["latency_ms"] for r in raw if isinstance(r.get("latency_ms"), (int, float))]

        percentiles = _latency_percentiles_from_list(latency_values)

        return {
            "service_id":   service_id,
            "service_name": service_name,
            "days":         days,
            "percentiles":  percentiles,
        }

    except Exception as exc:
        logger.error("[AnalyticsService] get_latency_stats error for %s: %s", service_id, exc)
        empty = {
            "avg_ms": None, "min_ms": None, "max_ms": None,
            "median_ms": None, "p95_ms": None, "p99_ms": None, "sample_count": 0,
        }
        return {"service_id": service_id, "service_name": service_name, "days": days, "percentiles": empty}


# ---------------------------------------------------------------------------
# Core: Daily Trend (one aggregation pipeline, grouped by day)
# ---------------------------------------------------------------------------

def get_trend(
    service_id: str,
    mongo_client,
    days: int = 30,
) -> List[dict]:
    """
    Return a list of TrendPoint dicts (one per calendar day, UTC) sorted ascending.
    Uses a single $group pipeline – no Python-side loops over raw docs.
    """
    try:
        col   = _get_col(mongo_client)
        match = _build_match(service_id, days)

        pipeline = [
            {"$match": match},
            {"$group": {
                "_id":          {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "total":        {"$sum": 1},
                "success":      {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "avg_latency":  {"$avg": "$latency_ms"},
            }},
            {"$sort": {"_id": 1}},
        ]
        results = list(col.aggregate(pipeline))

        trend = []
        for r in results:
            total   = r.get("total", 0)
            success = r.get("success", 0)
            failure = total - success
            pct     = round((success / total) * 100.0, 2) if total > 0 else 100.0
            avg_lat = round(r["avg_latency"], 2) if r.get("avg_latency") is not None else None
            trend.append({
                "date":           r["_id"],
                "uptime_pct":     pct,
                "total_checks":   total,
                "success_checks": success,
                "failure_checks": failure,
                "avg_latency_ms": avg_lat,
            })
        return trend

    except Exception as exc:
        logger.error("[AnalyticsService] get_trend error for %s: %s", service_id, exc)
        return []


# ---------------------------------------------------------------------------
# Core: Service Ranking (batched single-pass aggregation)
# ---------------------------------------------------------------------------

def get_service_ranking(mongo_client, days: Optional[int] = 30) -> dict:
    """
    Return a dict matching the ServiceRanking model.
    Uses a single aggregation pipeline over all services – avoids N+1 queries.
    """
    try:
        col   = _get_col(mongo_client)
        match = _build_match(service_id=None, days=days)

        pipeline = [
            {"$match": match},
            {"$group": {
                "_id":          "$service_id",
                "service_name": {"$first": "$service_name"},
                "total":        {"$sum": 1},
                "success":      {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            }},
            {"$sort": {"success": -1}},
        ]
        results = list(col.aggregate(pipeline))

        # Build ranked list
        # Map service_id → name using SERVICES_CONFIG as fallback
        sid_to_name: Dict[str, str] = {
            s["service_id"]: s["name"]
            for s in SERVICES_CONFIG if s.get("service_id")
        }

        ranked = []
        for i, r in enumerate(results, start=1):
            sid   = r["_id"]
            total = r.get("total", 0)
            succ  = r.get("success", 0)
            fail  = total - succ
            pct   = round((succ / total) * 100.0, 2) if total > 0 else 100.0
            name  = r.get("service_name") or sid_to_name.get(sid, sid)

            # Re-compute trend for each service
            trend = get_trend_indicator(sid, mongo_client)

            ranked.append({
                "rank":              i,
                "service_id":        sid,
                "service_name":      name,
                "uptime_pct":        pct,
                "total_checks":      total,
                "reliability_rating": get_reliability_rating(pct),
                "trend_indicator":   trend,
            })

        best  = ranked[0]["service_id"]  if ranked else None
        worst = ranked[-1]["service_id"] if ranked else None

        return {
            "days":            days,
            "ranked_services": ranked,
            "best_service_id": best,
            "worst_service_id": worst,
        }

    except Exception as exc:
        logger.error("[AnalyticsService] get_service_ranking error: %s", exc)
        return {"days": days, "ranked_services": [], "best_service_id": None, "worst_service_id": None}


# ---------------------------------------------------------------------------
# Core: Full Reliability Report for a Single Service
# ---------------------------------------------------------------------------

def get_reliability_report(
    service_id: str,
    service_name: str,
    mongo_client,
    sla_target: float = 99.9,
) -> dict:
    """
    Return a dict matching the ReliabilityReport model.
    Batches all DB calls efficiently; total = 5 queries (4 uptime + 1 latency).
    """
    up_24h,  s24,  f24  = get_uptime_counts(service_id, mongo_client, days=1)
    up_7d,   s7,   f7   = get_uptime_counts(service_id, mongo_client, days=7)
    up_30d,  s30,  f30  = get_uptime_counts(service_id, mongo_client, days=30)
    up_all,  s_all, f_all = get_uptime_counts(service_id, mongo_client, days=None)

    latency_data = get_latency_stats(service_id, service_name, mongo_client, days=30)

    rating  = get_reliability_rating(up_30d)
    trend   = get_trend_indicator(service_id, mongo_client)
    outages = detect_consecutive_outages(service_id, mongo_client)

    return {
        "service_id":        service_id,
        "service_name":      service_name,
        "uptime_24h":        up_24h,
        "uptime_7d":         up_7d,
        "uptime_30d":        up_30d,
        "uptime_all_time":   up_all,
        "total_checks":      s_all + f_all,
        "success_checks":    s_all,
        "failure_checks":    f_all,
        "reliability_rating": rating,
        "trend_indicator":   trend,
        "consecutive_outages": outages,
        "latency_30d":       latency_data["percentiles"],
        "sla_target_pct":    sla_target,
        "sla_met_30d":       up_30d >= sla_target,
    }


# ---------------------------------------------------------------------------
# Platform Summary (for executive reporting / future Pro tier)
# ---------------------------------------------------------------------------

def get_platform_summary(mongo_client, days: int = 30) -> dict:
    """
    Return a dict matching the PlatformSummary model.

    Optimised implementation – total DB queries = 4 (constant, regardless of
    number of services):

    Pipeline 1 – per-service uptime across four time windows (24h/7d/30d/all-time)
                 via a single $facet aggregation.
    Pipeline 2 – per-service latency values for percentile calculation.
    Pipeline 3 – per-service last-N-records for consecutive-outage detection.
    Pipeline 4 – platform-wide P95 latency (single $group).

    Old implementation: ~33 queries for 4 services, ~230 for 50 services.
    New implementation: 4 queries for any number of services.
    """
    try:
        col = _get_col(mongo_client)
        now = datetime.now(timezone.utc)

        # Pre-compute cutoffs
        cut_24h  = now - timedelta(days=1)
        cut_7d   = now - timedelta(days=7)
        cut_30d  = now - timedelta(days=days)   # honours 'days' parameter

        # Build name lookup from config
        sid_to_name: Dict[str, str] = {
            s["service_id"]: s["name"]
            for s in SERVICES_CONFIG if s.get("service_id")
        }

        # ==================================================================
        # Pipeline 1 – Per-service uptime across all four time windows.
        # Uses $facet to run four sub-pipelines in a single round-trip.
        # ==================================================================
        uptime_pipeline = [
            {"$match": {"service_id": {"$exists": True, "$ne": None}}},
            {"$facet": {
                "all_time": [
                    {"$group": {
                        "_id":     "$service_id",
                        "name":    {"$first": "$service_name"},
                        "total":   {"$sum": 1},
                        "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                    }},
                ],
                "last_30d": [
                    {"$match": {"timestamp": {"$gte": cut_30d}}},
                    {"$group": {
                        "_id":     "$service_id",
                        "total":   {"$sum": 1},
                        "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                        "avg_lat": {"$avg": "$latency_ms"},
                    }},
                ],
                "last_7d": [
                    {"$match": {"timestamp": {"$gte": cut_7d}}},
                    {"$group": {
                        "_id":     "$service_id",
                        "total":   {"$sum": 1},
                        "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                    }},
                ],
                "last_24h": [
                    {"$match": {"timestamp": {"$gte": cut_24h}}},
                    {"$group": {
                        "_id":     "$service_id",
                        "total":   {"$sum": 1},
                        "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                    }},
                ],
            }},
        ]
        facet_result = list(col.aggregate(uptime_pipeline))
        facet = facet_result[0] if facet_result else {
            "all_time": [], "last_30d": [], "last_7d": [], "last_24h": []
        }

        def _to_map(rows):
            return {r["_id"]: r for r in rows if r.get("_id")}

        all_time_map = _to_map(facet["all_time"])
        d30_map      = _to_map(facet["last_30d"])
        d7_map       = _to_map(facet["last_7d"])
        d24_map      = _to_map(facet["last_24h"])

        # ==================================================================
        # Pipeline 2 – All latency values grouped by service_id (for P50/P95/P99)
        # One pipeline, returns one doc per service with a sorted latency array.
        # ==================================================================
        latency_pipeline = [
            {"$match": {
                "timestamp":  {"$gte": cut_30d},
                "service_id": {"$exists": True, "$ne": None},
                "latency_ms": {"$exists": True, "$ne": None},
            }},
            {"$group": {
                "_id":      "$service_id",
                "latencies": {"$push": "$latency_ms"},
            }},
        ]
        latency_by_sid = {
            r["_id"]: sorted(r["latencies"])
            for r in col.aggregate(latency_pipeline)
            if r.get("_id")
        }

        # ==================================================================
        # Pipeline 3 – Last 3 records per service (consecutive-outage check)
        # Uses $sort + $group with $push then $slice.
        # ==================================================================
        outage_pipeline = [
            {"$match": {"service_id": {"$exists": True, "$ne": None}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id":     "$service_id",
                "last3":   {"$push": "$status"},
            }},
            {"$project": {
                "_id":   1,
                "last3": {"$slice": ["$last3", 3]},
            }},
        ]
        outage_by_sid = {
            r["_id"]: all(s == "failure" for s in r["last3"]) and len(r["last3"]) >= 3
            for r in col.aggregate(outage_pipeline)
            if r.get("_id")
        }

        # ==================================================================
        # Pipeline 4 – Platform-wide P95 latency (single group over 30d window)
        # Also provides platform-level avg/total for the summary header.
        # ==================================================================
        platform_pipeline = [
            {"$match": {"timestamp": {"$gte": cut_30d}, "service_id": {"$exists": True}}},
            {"$group": {
                "_id":     None,
                "total":   {"$sum": 1},
                "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                "avg_lat": {"$avg": "$latency_ms"},
            }},
        ]
        plat = list(col.aggregate(platform_pipeline))
        plat_total   = plat[0]["total"]   if plat else 0
        plat_success = plat[0]["success"] if plat else 0
        plat_failure = plat_total - plat_success
        plat_overall = round(plat_success / plat_total * 100.0, 2) if plat_total > 0 else 100.0
        plat_avg_lat = round(plat[0]["avg_lat"], 2) if plat and plat[0].get("avg_lat") else None

        # Platform P95: merge all latency lists across services
        all_lat = sorted(v for lats in latency_by_sid.values() for v in lats)
        p95_lat = _percentile(all_lat, 95) if all_lat else None

        # ==================================================================
        # Build per-service summaries (pure Python, zero extra DB queries)
        # ==================================================================
        def _pct(row) -> float:
            t = row.get("total", 0)
            s = row.get("success", 0)
            return round(s / t * 100.0, 2) if t > 0 else 100.0

        summaries     = []
        above_sla     = 0
        below_sla     = 0
        best_sid      = None
        worst_sid     = None
        best_uptime   = -1.0
        worst_uptime  = 101.0
        SLA_TARGET    = 99.9

        for sid, name in sid_to_name.items():
            row_all  = all_time_map.get(sid, {})
            row_30d  = d30_map.get(sid, {})
            row_7d   = d7_map.get(sid, {})
            row_24h  = d24_map.get(sid, {})

            up_all  = _pct(row_all)
            up_30d  = _pct(row_30d)
            up_7d   = _pct(row_7d)
            up_24h  = _pct(row_24h)

            t_all   = row_all.get("total",   0)
            s_all   = row_all.get("success", 0)
            f_all   = t_all - s_all

            # Trend
            if up_24h > up_7d + 0.5:
                trend = "↑"
            elif up_24h < up_7d - 0.5:
                trend = "↓"
            else:
                trend = "→"

            # Latency percentiles for this service
            lats = latency_by_sid.get(sid, [])
            perc = _latency_percentiles_from_list(lats)

            # Consecutive outages
            outage = outage_by_sid.get(sid, False)

            rating   = get_reliability_rating(up_30d)
            sla_met  = up_30d >= SLA_TARGET

            if sla_met:
                above_sla += 1
            else:
                below_sla += 1

            if up_30d > best_uptime:
                best_uptime = up_30d
                best_sid    = sid
            if up_30d < worst_uptime:
                worst_uptime = up_30d
                worst_sid    = sid

            summaries.append({
                "service_id":          sid,
                "service_name":        name,
                "uptime_24h":          up_24h,
                "uptime_7d":           up_7d,
                "uptime_30d":          up_30d,
                "uptime_all_time":     up_all,
                "total_checks":        t_all,
                "success_checks":      s_all,
                "failure_checks":      f_all,
                "reliability_rating":  rating,
                "trend_indicator":     trend,
                "consecutive_outages": outage,
                "latency_30d":         perc,
                "sla_target_pct":      SLA_TARGET,
                "sla_met_30d":         sla_met,
            })

        return {
            "generated_at":       now.isoformat(),
            "window_days":        days,
            "overall_uptime_pct": plat_overall,
            "total_checks":       plat_total,
            "total_failures":     plat_failure,
            "best_service":       best_sid,
            "worst_service":      worst_sid,
            "services_above_sla": above_sla,
            "services_below_sla": below_sla,
            "avg_latency_ms":     plat_avg_lat,
            "p95_latency_ms":     p95_lat,
            "service_summaries":  summaries,
        }

    except Exception as exc:
        logger.error("[AnalyticsService] get_platform_summary error: %s", exc)
        return {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "window_days":        days,
            "overall_uptime_pct": 100.0,
            "total_checks":       0,
            "total_failures":     0,
            "best_service":       None,
            "worst_service":      None,
            "services_above_sla": 0,
            "services_below_sla": 0,
            "avg_latency_ms":     None,
            "p95_latency_ms":     None,
            "service_summaries":  [],
        }


# ---------------------------------------------------------------------------
# v1 Compat: parse_health_json (preserved from original stub)
# ---------------------------------------------------------------------------

def parse_health_json(service_name: str, data: dict) -> dict:
    """
    Parse nested health metrics from known service health endpoints.
    Called by monitoring_service.execute_ping when parse_analytics=True.
    This function is unchanged from v1 for full backward compatibility.
    """
    parsed: dict = {
        "db_ok":          True,
        "redis_ok":       True,
        "uptime_seconds": None,
        "details":        {}
    }

    if not isinstance(data, dict):
        return parsed

    try:
        if service_name == "Stock Sentinel Server":
            parsed["uptime_seconds"] = data.get("uptime_seconds")
            services = data.get("services", {})

            mongo_status = services.get("mongodb", {})
            parsed["db_ok"] = mongo_status.get("status") == "healthy"
            parsed["details"]["mongodb_latency_ms"] = mongo_status.get("latency_ms")

            redis_status = services.get("redis", {})
            parsed["redis_ok"] = redis_status.get("status") == "healthy"
            parsed["details"]["redis_latency_ms"] = redis_status.get("latency_ms")

            parsed["details"]["system"]      = data.get("system", {})
            parsed["details"]["environment"] = data.get("environment", {})

        elif service_name == "Vision Retail IQ Server":
            parsed["uptime_seconds"] = data.get("uptime_seconds")
            parsed["db_ok"]          = data.get("db_ok", True)
            parsed["redis_ok"]       = data.get("redis_ok", True)
            parsed["details"]["redis_latency_ms"]          = data.get("redis_latency_ms", 0.0)
            parsed["details"]["processing_latency_ms"]     = data.get("processing_latency_ms")
            parsed["details"]["event_ingestion_rate_epm"]  = data.get("event_ingestion_rate_epm")
            parsed["details"]["cameras"]                   = data.get("cameras", [])
            parsed["details"]["stores"]                    = data.get("stores", [])
            parsed["details"]["status_overall"]            = data.get("status", "unknown")
        elif service_name == "Nexora AI Server":
            parsed["uptime_seconds"] = data.get("uptime_seconds")
            parsed["db_ok"]          = data.get("mongo_connected", True)
            parsed["redis_ok"]       = data.get("redis_connected", True)
            parsed["details"]["contexts_loaded"]           = data.get("contexts_loaded", {})
            parsed["details"]["total_actions_logged"]      = data.get("total_actions_logged")
            parsed["details"]["total_replies_logged"]      = data.get("total_replies_logged")
            parsed["details"]["active_suppression_keys"]  = data.get("active_suppression_keys")
            parsed["details"]["environment"]               = data.get("environment")
            parsed["details"]["memory_usage_mb"]           = data.get("memory_usage_mb")
            parsed["details"]["error"]                     = data.get("error") or data.get("message")
            parsed["details"]["errors"]                    = data.get("errors")
            parsed["details"]["status"]                    = data.get("status")

    except Exception as exc:
        logger.error("[AnalyticsService] parse_health_json error for %s: %s", service_name, exc)

    return parsed


# ---------------------------------------------------------------------------
# Additive: get_performance_analytics & Anomaly Detection (Observability Upgrade)
# ---------------------------------------------------------------------------

def get_performance_analytics(service_id: str, mongo_client, days: int = 30) -> dict:
    """
    Compute enterprise-grade Performance Analytics for a service.
    Includes rolling averages, latency percentiles, trend indicators, anomalies, and summary suggestions.
    """
    try:
        col = _get_col(mongo_client)
        now = datetime.now(timezone.utc)
        
        # 1. Fetch rolling data windows
        cut_1h = now - timedelta(hours=1)
        cut_24h = now - timedelta(days=1)
        
        # Fetch records
        records_30d = list(col.find({"service_id": service_id, "timestamp": {"$gte": now - timedelta(days=days)}}).sort("timestamp", -1))
        
        if not records_30d:
            return {
                "service_id": service_id,
                "performance_score": 100.0,
                "performance_trend": "Stable",
                "rolling_averages": {"1h_ms": 0.0, "24h_ms": 0.0, "7d_ms": 0.0, "30d_ms": 0.0},
                "latency_metrics": {"avg_ms": 0.0, "median_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0},
                "trends": {"latency_trend": "Stable", "availability_trend": "Stable", "health_trend": "Stable"},
                "anomalies": [],
                "summary": "No validation data recorded to compute performance aggregates."
            }
            
        def _avg_lat(recs) -> float:
            lats = [r["latency_ms"] for r in recs if r.get("latency_ms") is not None]
            return round(sum(lats) / len(lats), 2) if lats else 0.0

        # Calculations
        avg_1h = _avg_lat([r for r in records_30d if r["timestamp"] >= cut_1h])
        avg_24h = _avg_lat([r for r in records_30d if r["timestamp"] >= cut_24h])
        avg_7d = _avg_lat([r for r in records_30d if r["timestamp"] >= (now - timedelta(days=7))])
        avg_30d = _avg_lat(records_30d)
        
        # Latency lists
        all_lats = sorted([r["latency_ms"] for r in records_30d if r.get("latency_ms") is not None])
        metrics = _latency_percentiles_from_list(all_lats)
        
        # Latency Trend
        # Compare last 7 days vs previous 7 days (7d to 14d)
        recs_last_7d = [r for r in records_30d if r["timestamp"] >= (now - timedelta(days=7))]
        recs_prev_7d = [r for r in records_30d if (now - timedelta(days=14)) <= r["timestamp"] < (now - timedelta(days=7))]
        avg_last_7 = _avg_lat(recs_last_7d)
        avg_prev_7 = _avg_lat(recs_prev_7d)
        
        latency_trend = "Stable"
        pct_change = 0.0
        if avg_prev_7 > 0:
            pct_change = ((avg_last_7 - avg_prev_7) / avg_prev_7) * 100.0
            if pct_change > 10.0:
                latency_trend = "Degrading"
            elif pct_change < -10.0:
                latency_trend = "Improving"
                
        # Availability Trend
        # Uptime comparison
        def _uptime_pct(recs) -> float:
            total = len(recs)
            if total == 0:
                return 100.0
            success = sum(1 for r in recs if r.get("status") == "success")
            return (success / total) * 100.0
            
        up_last_7 = _uptime_pct(recs_last_7d)
        up_prev_7 = _uptime_pct(recs_prev_7d)
        availability_trend = "Stable"
        if up_last_7 < up_prev_7 - 0.5:
            availability_trend = "Degrading"
        elif up_last_7 > up_prev_7 + 0.5:
            availability_trend = "Improving"
            
        health_trend = "Stable"
        if latency_trend == "Degrading" or availability_trend == "Degrading":
            health_trend = "Degrading"
        elif latency_trend == "Improving" and availability_trend != "Degrading":
            health_trend = "Improving"
            
        # Anomaly detection (sudden spikes > 3x average)
        anomalies = []
        if len(all_lats) > 5:
            threshold = max(300.0, avg_30d * 3.0)
            for r in records_30d[:50]: # Look at last 50 runs
                lat = r.get("latency_ms")
                if lat and lat > threshold:
                    anomalies.append({
                        "timestamp": r["timestamp"].isoformat(),
                        "latency_ms": lat,
                        "threshold": threshold,
                        "type": "Latency Spike"
                    })
                    
        # Performance Score (0 - 100)
        perf_score = 100.0
        # Deduct for degradation, anomalies, and latency
        if latency_trend == "Degrading":
            perf_score -= 10
        if availability_trend == "Degrading":
            perf_score -= 20
        perf_score -= min(30, len(anomalies) * 5)
        # Latency penalty
        if avg_30d > 1000:
            perf_score -= min(20, (avg_30d - 1000) / 100)
        perf_score = max(0.0, min(100.0, perf_score))

        # Summary Generation
        direction = "increased" if pct_change > 0 else "decreased"
        abs_change = abs(pct_change)
        summary_text = f"Average latency {direction} by {abs_change:.1f}% during the last seven days."
        
        if health_trend == "Degrading":
            summary_text += " Performance Trend: Degrading. Recommendation: Investigate backend database performance."
        else:
            summary_text += f" Performance Trend: {health_trend}."

        return {
            "service_id": service_id,
            "performance_score": round(perf_score, 1),
            "performance_trend": health_trend,
            "rolling_averages": {
                "1h_ms": avg_1h,
                "24h_ms": avg_24h,
                "7d_ms": avg_7d,
                "30d_ms": avg_30d
            },
            "latency_metrics": metrics,
            "trends": {
                "latency_trend": latency_trend,
                "availability_trend": availability_trend,
                "health_trend": health_trend
            },
            "anomalies": anomalies,
            "summary": summary_text
        }
    except Exception as exc:
        logger.error("[AnalyticsService] get_performance_analytics error for %s: %s", service_id, exc)
        return {}

