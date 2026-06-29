import math
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from config import SERVICES_CONFIG

logger = logging.getLogger(__name__)

def calculate_reliability(service_id: str, service_name: str, mongo_client) -> Dict[str, Any]:
    """
    Calculate composite reliability metrics, grade, and predict failure window.
    """
    try:
        db = mongo_client["ServerAutomation"]
        history_col = db["monitoring_history"]
        incidents_col = db["incidents"]
        retry_col = db["retry_history"]
        
        now = datetime.utcnow()
        cutoff_30d = now - timedelta(days=30)
        cutoff_7d = now - timedelta(days=7)
        
        # 1. Fetch 30-day checks
        query_30d = {"service_id": service_id, "timestamp": {"$gte": cutoff_30d}}
        records = list(history_col.find(query_30d))
        total_checks = len(records)
        
        if total_checks == 0:
            return _default_reliability(service_id, service_name)
            
        success_checks = sum(1 for r in records if r.get("status") == "success")
        uptime_pct = (success_checks / total_checks) * 100.0
        
        # 2. Latency metrics
        latencies = [r["latency_ms"] for r in records if r.get("latency_ms") is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        latency_stddev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        
        # 3. Retries count
        retry_count = retry_col.count_documents({"service_id": service_id, "timestamp": {"$gte": cutoff_30d}})
        retry_freq = retry_count / total_checks if total_checks > 0 else 0.0
        
        # 4. Incident count
        incident_count = incidents_col.count_documents({
            "service_id": service_id, 
            "started_at": {"$gte": cutoff_30d}
        })
        
        # 5. Anomaly detection (sudden spikes > 2.5x standard deviation or 3x average)
        anomaly_count = 0
        if len(latencies) > 5:
            threshold = max(300.0, avg_latency * 3.0)
            anomaly_count = sum(1 for l in latencies if l > threshold)
            
        # ── Compute Score (0 - 100) ───────────────────────────────────────────
        # Factors:
        # - Uptime: 40%
        # - Latency performance (vs 1000ms warning): 15%
        # - Latency stability (stddev relative to average): 15%
        # - Retry frequency (lower is better): 10%
        # - Incident count (lower is better): 10%
        # - Anomaly count (lower is better): 10%
        
        score_uptime = uptime_pct
        
        score_latency = max(0.0, 100.0 - (avg_latency / 10.0)) # 1000ms avg latency maps to 0
        score_latency = min(100.0, score_latency)
        
        coef_var = (latency_stddev / avg_latency) if avg_latency > 0 else 0.0
        score_stability = max(0.0, 100.0 - (coef_var * 50.0)) # high variance penalizes stability
        score_stability = min(100.0, score_stability)
        
        score_retries = max(0.0, 100.0 - (retry_freq * 500.0)) # 20% checks retried maps to 0
        score_retries = min(100.0, score_retries)
        
        score_incidents = max(0.0, 100.0 - (incident_count * 20.0)) # 5 incidents maps to 0
        score_incidents = min(100.0, score_incidents)
        
        score_anomalies = max(0.0, 100.0 - (anomaly_count * 10.0)) # 10 anomalies maps to 0
        score_anomalies = min(100.0, score_anomalies)
        
        score = (
            (score_uptime * 0.40) +
            (score_latency * 0.15) +
            (score_stability * 0.15) +
            (score_retries * 0.10) +
            (score_incidents * 0.10) +
            (score_anomalies * 0.10)
        )
        score = round(max(0.0, min(100.0, score)), 1)
        
        # ── Grade mapping ─────────────────────────────────────────────────────
        if score >= 97.0:
            grade = "A+"
        elif score >= 93.0:
            grade = "A"
        elif score >= 88.0:
            grade = "B+"
        elif score >= 80.0:
            grade = "B"
        elif score >= 70.0:
            grade = "C"
        else:
            grade = "D"
            
        # ── Failure Risk mapping ──────────────────────────────────────────────
        if score >= 95.0:
            risk = "Very Low"
        elif score >= 85.0:
            risk = "Low"
        elif score >= 70.0:
            risk = "Medium"
        elif score >= 50.0:
            risk = "High"
        else:
            risk = "Critical"
            
        # ── Predictive diagnostics ────────────────────────────────────────────
        # Predict the next failure window
        recent_7d_incidents = incidents_col.count_documents({
            "service_id": service_id, 
            "started_at": {"$gte": cutoff_7d}
        })
        recent_24h_anomalies = 0
        cutoff_24h = now - timedelta(days=1)
        recent_24h_records = [r for r in records if r.get("timestamp") and r["timestamp"] >= cutoff_24h]
        if recent_24h_records:
            r_latencies = [r["latency_ms"] for r in recent_24h_records if r.get("latency_ms") is not None]
            r_avg = sum(r_latencies) / len(r_latencies) if r_latencies else 0.0
            r_threshold = max(300.0, r_avg * 2.5)
            recent_24h_anomalies = sum(1 for l in r_latencies if l > r_threshold)

        # Build prediction logic
        prediction_window = "Stable (No failure predicted)"
        prediction_confidence = 98.0
        
        if risk in ("Critical", "High") or recent_7d_incidents > 2:
            prediction_window = "Next 12 Hours"
            prediction_confidence = 85.0
        elif recent_24h_anomalies > 3 or incident_count > 1:
            prediction_window = "Next 24 Hours"
            prediction_confidence = 75.0
        elif score < 90.0:
            prediction_window = "Next 7 Days"
            prediction_confidence = 80.0
            
        # ── Trends calculations ───────────────────────────────────────────────
        # Compare 30d vs last 7d
        records_7d = [r for r in records if r.get("timestamp") and r["timestamp"] >= cutoff_7d]
        score_7d = score
        if records_7d:
            uptime_7d = (sum(1 for r in records_7d if r.get("status") == "success") / len(records_7d)) * 100.0
            l_7d = [r["latency_ms"] for r in records_7d if r.get("latency_ms") is not None]
            avg_7d = sum(l_7d) / len(l_7d) if l_7d else 0.0
            score_7d_calculated = (uptime_7d * 0.7) + (max(0.0, 100.0 - (avg_7d / 10.0)) * 0.3)
            score_7d = round(max(0.0, min(100.0, score_7d_calculated)), 1)
            
        reliability_trend = "Stable"
        if score_7d > score + 1.0:
            reliability_trend = "Improving"
        elif score_7d < score - 1.0:
            reliability_trend = "Degrading"
            
        risk_trend = "Stable"
        if score_7d < score - 1.0:
            risk_trend = "Increasing"
        elif score_7d > score + 1.0:
            risk_trend = "Decreasing"

        result = {
            "service_id": service_id,
            "service_name": service_name,
            "reliability_score": score,
            "reliability_grade": grade,
            "failure_risk": risk,
            "predicted_failure_window": prediction_window,
            "prediction_confidence": prediction_confidence,
            "reliability_trend": reliability_trend,
            "risk_trend": risk_trend,
            "metrics": {
                "uptime_pct": round(uptime_pct, 2),
                "avg_latency_ms": round(avg_latency, 1),
                "latency_stddev": round(latency_stddev, 1),
                "retry_count": retry_count,
                "incident_count": incident_count,
                "anomaly_count": anomaly_count
            },
            "last_calculated": now
        }
        
        # Persist to database
        db["reliability_history"].update_one(
            {"service_id": service_id},
            {"$set": result},
            upsert=True
        )
        db["reliability_history"].create_index([("service_id", 1)])
        
        return result
    except Exception as e:
        logger.error(f"[ReliabilityEngine] Error computing score for {service_id}: {e}")
        return _default_reliability(service_id, service_name)

def get_platform_reliability(mongo_client) -> Dict[str, Any]:
    """
    Get aggregated reliability metrics across all monitored services.
    """
    try:
        db = mongo_client["ServerAutomation"]
        records = list(db["reliability_history"].find())
        
        if not records:
            # Generate on demand
            records = []
            for s in SERVICES_CONFIG:
                if s.get("service_id"):
                    records.append(calculate_reliability(s["service_id"], s["name"], mongo_client))
                    
        # Find best/worst/unstable services
        best_svc = None
        best_score = -1.0
        worst_svc = None
        worst_score = 101.0
        most_unstable_svc = None
        max_anomalies = -1
        
        scores_sum = 0.0
        for r in records:
            score = r.get("reliability_score", 100.0)
            name = r.get("service_name", r["service_id"])
            scores_sum += score
            
            if score > best_score:
                best_score = score
                best_svc = name
                
            if score < worst_score:
                worst_score = score
                worst_svc = name
                
            anomalies = r.get("metrics", {}).get("anomaly_count", 0)
            if anomalies > max_anomalies:
                max_anomalies = anomalies
                most_unstable_svc = name
                
        avg_score = scores_sum / len(records) if records else 100.0
        
        # Stringify object IDs and datetimes to allow JSON serialization
        for r in records:
            if "_id" in r:
                r["_id"] = str(r["_id"])
            if "last_calculated" in r and isinstance(r["last_calculated"], datetime):
                r["last_calculated"] = r["last_calculated"].isoformat()
                
        return {
            "average_reliability_score": round(avg_score, 1),
            "most_reliable_service": best_svc or "N/A",
            "least_reliable_service": worst_svc or "N/A",
            "most_unstable_service": most_unstable_svc or "N/A",
            "services_count": len(records),
            "details": records
        }
    except Exception as e:
        logger.error(f"[ReliabilityEngine] Failed to compile platform reliability: {e}")
        return {
            "average_reliability_score": 100.0,
            "most_reliable_service": "N/A",
            "least_reliable_service": "N/A",
            "most_unstable_service": "N/A",
            "services_count": 0,
            "details": []
        }

def _default_reliability(service_id: str, service_name: str) -> Dict[str, Any]:
    return {
        "service_id": service_id,
        "service_name": service_name,
        "reliability_score": 100.0,
        "reliability_grade": "A+",
        "failure_risk": "Very Low",
        "predicted_failure_window": "Stable (No failure predicted)",
        "prediction_confidence": 99.0,
        "reliability_trend": "Stable",
        "risk_trend": "Stable",
        "metrics": {
            "uptime_pct": 100.0,
            "avg_latency_ms": 0.0,
            "latency_stddev": 0.0,
            "retry_count": 0,
            "incident_count": 0,
            "anomaly_count": 0
        },
        "last_calculated": datetime.utcnow()
    }
