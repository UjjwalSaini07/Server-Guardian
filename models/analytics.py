"""
models/analytics.py

Pydantic response models for the ServerGuardian Analytics Engine.
All models are forward-compatible: optional fields ensure existing records
are never broken and future fields can be added without a migration.
"""

from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Primitive / Shared
# ---------------------------------------------------------------------------

class TrendPoint(BaseModel):
    """Single data-point used in any time-series chart (one entry per day)."""
    date: str = Field(..., description="ISO date string YYYY-MM-DD")
    uptime_pct: float = Field(..., description="Uptime percentage for this day")
    total_checks: int = Field(0, description="Total checks executed on this day")
    success_checks: int = Field(0, description="Successful checks on this day")
    failure_checks: int = Field(0, description="Failed checks on this day")
    avg_latency_ms: Optional[float] = Field(None, description="Average latency on this day in ms")


class LatencyPercentiles(BaseModel):
    """Full latency distribution for a service over a time window."""
    avg_ms: Optional[float] = Field(None, description="Average latency in ms")
    min_ms: Optional[float] = Field(None, description="Minimum observed latency in ms")
    max_ms: Optional[float] = Field(None, description="Maximum observed latency in ms")
    median_ms: Optional[float] = Field(None, description="Median (P50) latency in ms")
    p95_ms: Optional[float] = Field(None, description="95th-percentile latency in ms")
    p99_ms: Optional[float] = Field(None, description="99th-percentile latency in ms")
    sample_count: int = Field(0, description="Number of latency samples used")


# ---------------------------------------------------------------------------
# Phase 1 – Core Response Models
# ---------------------------------------------------------------------------

class UptimeStats(BaseModel):
    """Uptime statistics for a single service over a configurable time window."""
    service_id: str
    service_name: str
    days: Optional[int] = Field(None, description="Window size in days; None means all-time")

    uptime_pct: float = Field(..., description="Uptime percentage (0-100)")
    total_checks: int = Field(0)
    success_checks: int = Field(0)
    failure_checks: int = Field(0)

    # Future-proof fields for comparison and reporting
    sla_target_pct: float = Field(99.9, description="SLA target percentage")
    sla_met: bool = Field(True, description="Whether the SLA target was met")


class LatencyStats(BaseModel):
    """Latency analytics for a single service over a configurable time window."""
    service_id: str
    service_name: str
    days: Optional[int] = Field(None, description="Window size in days; None means all-time")
    percentiles: LatencyPercentiles


class ReliabilityReport(BaseModel):
    """Comprehensive reliability summary for a single service."""
    service_id: str
    service_name: str

    # Multi-window uptime
    uptime_24h: float = Field(100.0)
    uptime_7d: float = Field(100.0)
    uptime_30d: float = Field(100.0)
    uptime_all_time: float = Field(100.0)

    # Check counts (all-time)
    total_checks: int = Field(0)
    success_checks: int = Field(0)
    failure_checks: int = Field(0)

    # Qualitative rating + trend
    reliability_rating: str = Field("Excellent", description="Excellent | Good | Warning | Critical")
    trend_indicator: str = Field("→", description="↑ Improving | → Stable | ↓ Degrading")

    # Incident context
    consecutive_outages: bool = Field(False, description="True if last N checks all failed")

    # Latency summary (optional; populated when history is available)
    latency_30d: Optional[LatencyPercentiles] = None

    # SLA
    sla_target_pct: float = Field(99.9)
    sla_met_30d: bool = Field(True)


class ServiceRankEntry(BaseModel):
    """Single entry in the cross-service uptime ranking list."""
    rank: int
    service_id: str
    service_name: str
    uptime_pct: float
    total_checks: int
    reliability_rating: str
    trend_indicator: str


class ServiceRanking(BaseModel):
    """Full service ranking table for a time window."""
    days: Optional[int] = None
    ranked_services: List[ServiceRankEntry] = Field(default_factory=list)
    best_service_id: Optional[str] = None
    worst_service_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Executive / Platform Summary (ready for Phase 3 / future Pro features)
# ---------------------------------------------------------------------------

class PlatformSummary(BaseModel):
    """Platform-wide health snapshot – structured for executive reporting."""
    generated_at: str = Field(..., description="ISO timestamp (UTC) when report was generated")
    window_days: int = Field(30)

    overall_uptime_pct: float = Field(100.0)
    total_checks: int = Field(0)
    total_failures: int = Field(0)

    best_service: Optional[str] = None
    worst_service: Optional[str] = None

    services_above_sla: int = Field(0)
    services_below_sla: int = Field(0)

    avg_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None

    # Per-service summary list for embedding in reports
    service_summaries: List[ReliabilityReport] = Field(default_factory=list)
