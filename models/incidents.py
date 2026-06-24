"""
models/incidents.py

Pydantic response models for the ServerGuardian Incident Timeline Engine.
Represents full incident lifecycle: open → acknowledged → resolved.
"""

from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class IncidentTimelineEvent(BaseModel):
    """A single timestamped event in an incident's history."""
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp")
    event: str = Field(..., description="INCIDENT_OPENED | ACKNOWLEDGED | NOTE_ADDED | RESOLVED")
    note: Optional[str] = Field(None, description="Human-readable context for this event")


class Incident(BaseModel):
    """Full lifecycle document for a single service incident."""
    incident_id: str = Field(..., description="Auto-generated ID e.g. INC-2026-001")
    service_id: str
    service_name: str

    # Timestamps
    started_at: str = Field(..., description="ISO timestamp when outage began (first failure)")
    detected_at: str = Field(..., description="ISO timestamp when incident was formally opened")
    resolved_at: Optional[str] = Field(None, description="ISO timestamp when incident resolved")
    acknowledged_at: Optional[str] = Field(None, description="ISO timestamp when incident acknowledged")
    acknowledged_by: Optional[str] = Field(None, description="Name or system that acknowledged")

    # State
    status: str = Field("open", description="open | acknowledged | resolved")
    severity: str = Field("critical", description="critical | warning | info")
    trigger_alert_type: str = Field("SERVICE_DOWN", description="Alert type that opened this incident")
    failure_reason: Optional[str] = Field(None, description="Human-readable failure description")

    # SRE Metrics (seconds)
    mttd_seconds: Optional[float] = Field(None, description="Mean Time To Detect (seconds)")
    mttr_seconds: Optional[float] = Field(None, description="Mean Time To Recover (seconds)")

    # Event log
    timeline: List[IncidentTimelineEvent] = Field(default_factory=list)


class IncidentMetrics(BaseModel):
    """Aggregated incident metrics for a time window."""
    days: int = Field(30)
    total_incidents: int = Field(0)
    open_incidents: int = Field(0)
    resolved_incidents: int = Field(0)
    acknowledged_incidents: int = Field(0)
    avg_mttd_seconds: Optional[float] = Field(None)
    avg_mttr_seconds: Optional[float] = Field(None)
    incidents_by_service: Dict[str, int] = Field(default_factory=dict)
    incidents_by_day: Dict[str, int] = Field(default_factory=dict)
