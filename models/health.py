"""
models/health.py

Pydantic response models for the ServerGuardian Deep Health Check System.
All models are forward-compatible: optional fields ensure no breaking changes.
"""

from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class HealthDeduction(BaseModel):
    """A single scoring deduction applied to a health check result."""
    reason: str = Field(..., description="Deduction reason code (e.g. HIGH_LATENCY, DB_FAILURE)")
    points: int = Field(..., description="Points deducted (negative integer)")


class HealthDetail(BaseModel):
    """Full dimensional breakdown of a single health check evaluation."""
    http_ok: bool = Field(True, description="HTTP response code was 200")
    json_valid: bool = Field(True, description="Response body was parseable JSON")
    schema_valid: bool = Field(True, description="Response JSON matched expected schema")
    latency_ok: bool = Field(True, description="Latency was within acceptable threshold")
    db_ok: Optional[bool] = Field(None, description="Database sub-component healthy (None = not checked)")
    cache_ok: Optional[bool] = Field(None, description="Cache sub-component healthy (None = not checked)")
    deductions: List[HealthDeduction] = Field(default_factory=list, description="Applied deductions")


class HealthCheckResult(BaseModel):
    """Complete result of a deep health check evaluation for a single ping."""
    health_score: int = Field(100, ge=0, le=100, description="Composite health score 0-100")
    detail: HealthDetail = Field(default_factory=HealthDetail)
