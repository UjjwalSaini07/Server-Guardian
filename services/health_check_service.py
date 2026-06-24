"""
services/health_check_service.py

ServerGuardian Deep Health Check System.

Evaluates a ping result along multiple dimensions and produces a composite
health_score (0-100) with a full deduction breakdown.

Scoring dimensions:
  - HTTP status (required — score drops to 0 on failure)
  - JSON parseability (-10 pts if not valid JSON)
  - Schema validation (-10 pts if required fields missing, -5 per wrong value)
  - Database sub-component (-15 pts if db_ok == False)
  - Cache sub-component (-10 pts if redis_ok/cache_ok == False)
  - Latency warning  (-5 pts if >= HEALTH_LATENCY_WARNING_MS)
  - Latency critical (-10 pts total if >= HEALTH_LATENCY_CRITICAL_MS)

All thresholds are configurable via environment variables.
"""

import os
import logging
from typing import Optional

# Environment-configurable thresholds
LATENCY_WARNING_MS = float(os.getenv("HEALTH_LATENCY_WARNING_MS", "1000"))
LATENCY_CRITICAL_MS = float(os.getenv("HEALTH_LATENCY_CRITICAL_MS", "3000"))


def evaluate_health_score(
    response_json: Optional[dict],
    status_code: int,
    latency_ms: float,
    service_config: dict,
) -> tuple:
    """
    Evaluate a ping result and return (health_score: int, health_detail: dict).

    Args:
        response_json:   Parsed JSON body from HTTP response, or None.
        status_code:     HTTP status code received.
        latency_ms:      Round-trip latency in milliseconds.
        service_config:  Service entry from SERVICES_CONFIG (may contain 'health_schema').

    Returns:
        (health_score, health_detail) where health_detail is a dict suitable
        for storing in monitoring_history.health_detail.
    """
    score = 100
    deductions = []

    http_ok = (status_code == 200)
    json_valid = response_json is not None
    schema_valid = True
    latency_ok = True
    db_ok = None
    cache_ok = None

    # ── 1. HTTP gate ──────────────────────────────────────────────────────────
    if not http_ok:
        score = 0
        deductions.append({"reason": "HTTP_FAILURE", "points": -100})
        return score, {
            "http_ok": False,
            "json_valid": json_valid,
            "schema_valid": False,
            "latency_ok": latency_ms < LATENCY_CRITICAL_MS,
            "db_ok": None,
            "cache_ok": None,
            "deductions": deductions,
        }

    # ── 2. JSON parseability ──────────────────────────────────────────────────
    if not json_valid:
        score -= 10
        deductions.append({"reason": "INVALID_JSON", "points": -10})

    # ── 3. Schema validation ─────────────────────────────────────────────────
    health_schema = service_config.get("health_schema")
    if json_valid and health_schema and response_json:
        required_fields = health_schema.get("required_fields", [])
        expected_values = health_schema.get("expected_values", {})

        missing = [f for f in required_fields if f not in response_json]
        if missing:
            schema_valid = False
            score -= 10
            deductions.append({
                "reason": f"MISSING_FIELDS:{','.join(missing)}",
                "points": -10
            })

        for field, expected in expected_values.items():
            actual = response_json.get(field)
            if actual != expected:
                schema_valid = False
                score -= 5
                deductions.append({
                    "reason": f"FIELD_VALUE_MISMATCH:{field}",
                    "points": -5
                })

    # ── 4. Sub-component probing ─────────────────────────────────────────────
    if json_valid and response_json:
        # Database
        if "db_ok" in response_json:
            db_ok = bool(response_json["db_ok"])
        elif "database_ok" in response_json:
            db_ok = bool(response_json["database_ok"])
        elif "database" in response_json:
            db_ok = bool(response_json["database"])

        # Cache
        if "redis_ok" in response_json:
            cache_ok = bool(response_json["redis_ok"])
        elif "cache_ok" in response_json:
            cache_ok = bool(response_json["cache_ok"])
        elif "redis" in response_json:
            cache_ok = bool(response_json["redis"])

    if db_ok is False:
        score -= 15
        deductions.append({"reason": "DB_FAILURE", "points": -15})

    if cache_ok is False:
        score -= 10
        deductions.append({"reason": "CACHE_FAILURE", "points": -10})

    # ── 5. Latency scoring ───────────────────────────────────────────────────
    if latency_ms >= LATENCY_CRITICAL_MS:
        latency_ok = False
        score -= 10
        deductions.append({"reason": "CRITICAL_LATENCY", "points": -10})
    elif latency_ms >= LATENCY_WARNING_MS:
        latency_ok = False
        score -= 5
        deductions.append({"reason": "HIGH_LATENCY", "points": -5})

    score = max(0, score)

    health_detail = {
        "http_ok": http_ok,
        "json_valid": json_valid,
        "schema_valid": schema_valid,
        "latency_ok": latency_ok,
        "db_ok": db_ok,
        "cache_ok": cache_ok,
        "deductions": deductions,
    }

    logging.debug(
        f"[HealthCheckService] Score={score} | latency={latency_ms:.0f}ms | "
        f"db_ok={db_ok} | cache_ok={cache_ok} | deductions={len(deductions)}"
    )

    return score, health_detail
