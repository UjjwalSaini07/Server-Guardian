import re
import socket
from typing import Optional, Tuple, Dict, Any

class RootCauseEngine:
    """
    Intelligent Root Cause Analysis Engine for ServerGuardian.
    Categorizes failures and suggests remediation steps.
    """

    CLASSIFICATIONS = {
        "DNS_RESOLUTION_FAILURE": {
            "severity": "critical",
            "recommended_action": "Verify domain registrar DNS settings and check name server resolution.",
            "summary_template": "Failed to resolve hostname '{hostname}' via DNS. Host might be unregistered or DNS servers down."
        },
        "SSL_CERTIFICATE_FAILURE": {
            "severity": "critical",
            "recommended_action": "Check SSL certificate expiration date and verify intermediate trust chain.",
            "summary_template": "SSL/TLS handshake failed for {url}. Certificate may be expired, self-signed, or untrusted."
        },
        "CONNECTION_REFUSED": {
            "severity": "critical",
            "recommended_action": "Verify server daemon is running and correct port is open in firewall.",
            "summary_template": "Connection refused by target host on port {port}. Server port is closed or firewall is dropping packets."
        },
        "TCP_TIMEOUT": {
            "severity": "critical",
            "recommended_action": "Check network routing path and host firewall configuration.",
            "summary_template": "TCP connection timed out during handshake with {url}. Host is unreachable."
        },
        "REQUEST_TIMEOUT": {
            "severity": "warning",
            "recommended_action": "Optimize backend processing time or increase pinger request timeout limit.",
            "summary_template": "Read timeout reached after waiting for response from {url}."
        },
        "HTTP_4XX": {
            "severity": "warning",
            "recommended_action": "Check request parameters, headers, or API endpoint path compatibility.",
            "summary_template": "Target returned client error HTTP {status_code}."
        },
        "HTTP_5XX": {
            "severity": "critical",
            "recommended_action": "Investigate backend server error logs and process crash logs.",
            "summary_template": "Target returned internal server error HTTP {status_code}."
        },
        "JSON_PARSING_FAILURE": {
            "severity": "warning",
            "recommended_action": "Check endpoint response format; ensure Content-Type is application/json.",
            "summary_template": "Failed to parse response body as JSON."
        },
        "INVALID_RESPONSE_SCHEMA": {
            "severity": "warning",
            "recommended_action": "Verify health endpoint output matches expected schema contract keys.",
            "summary_template": "Response JSON is missing required health schema fields: {details}."
        },
        "UNEXPECTED_RESPONSE_BODY": {
            "severity": "warning",
            "recommended_action": "Check application status or database state for unexpected field values.",
            "summary_template": "Health field mismatch: expected {expected}, got {actual}."
        },
        "AUTHENTICATION_FAILURE": {
            "severity": "critical",
            "recommended_action": "Verify API keys, headers, or authentication tokens configuration.",
            "summary_template": "Authentication failed with status HTTP {status_code}."
        },
        "DATABASE_UNAVAILABLE": {
            "severity": "critical",
            "recommended_action": "Check MongoDB/MySQL/PostgreSQL daemon connectivity and cluster logs.",
            "summary_template": "Health check reports database sub-component is offline."
        },
        "REDIS_UNAVAILABLE": {
            "severity": "critical",
            "recommended_action": "Verify Redis server connectivity and cache instance capacity.",
            "summary_template": "Health check reports Redis cache sub-component is offline."
        },
        "DEPENDENCY_FAILURE": {
            "severity": "warning",
            "recommended_action": "Investigate upstream APIs or downstream service status integration.",
            "summary_template": "Service sub-component check failed: {details}."
        },
        "HIGH_LATENCY_DEGRADATION": {
            "severity": "warning",
            "recommended_action": "Optimize application database queries or scale instances to handle load.",
            "summary_template": "Response latency ({latency:.0f}ms) is above warning thresholds."
        },
        "UNKNOWN_FAILURE": {
            "severity": "critical",
            "recommended_action": "Examine overall server logs and verify host power status.",
            "summary_template": "An unknown error occurred during monitoring: {details}."
        }
    }

    @classmethod
    def diagnose(
        cls,
        service_config: dict,
        response_json: Optional[Dict[str, Any]],
        status_code: Optional[int],
        latency_ms: float,
        error_exception: Optional[Exception] = None
    ) -> Dict[str, Any]:
        """
        Diagnose a failed health check run, determine root cause, and return diagnostic metadata.
        """
        url = service_config.get("url", "")
        hostname = ""
        port = "80/443"
        
        # Parse hostname and port if possible
        if url:
            match = re.search(r"https?://([^:/]+)(?::(\d+))?", url)
            if match:
                hostname = match.group(1)
                port = match.group(2) or ("443" if url.startswith("https") else "80")

        failure_type = "UNKNOWN_FAILURE"
        details = ""
        confidence_score = 90

        # ── Exception checks ──────────────────────────────────────────────────
        if error_exception:
            err_str = str(error_exception).lower()
            details = str(error_exception)
            
            if "name or service not known" in err_str or "gaierror" in err_str or "dns" in err_str:
                failure_type = "DNS_RESOLUTION_FAILURE"
                confidence_score = 98
            elif "ssl" in err_str or "cert" in err_str or "handshake" in err_str:
                failure_type = "SSL_CERTIFICATE_FAILURE"
                confidence_score = 95
            elif "connection refused" in err_str:
                failure_type = "CONNECTION_REFUSED"
                confidence_score = 97
            elif "connect timeout" in err_str or "connection timed out" in err_str:
                failure_type = "TCP_TIMEOUT"
                confidence_score = 92
            elif "read timeout" in err_str or "timeout" in err_str:
                failure_type = "REQUEST_TIMEOUT"
                confidence_score = 94
            else:
                failure_type = "UNKNOWN_FAILURE"
                confidence_score = 70

        # ── HTTP status codes checks ──────────────────────────────────────────
        elif status_code and status_code != 200:
            confidence_score = 95
            if status_code in (401, 403):
                failure_type = "AUTHENTICATION_FAILURE"
            elif 400 <= status_code < 500:
                failure_type = "HTTP_4XX"
            elif 500 <= status_code < 600:
                failure_type = "HTTP_5XX"

        # ── JSON and Schema checks ────────────────────────────────────────────
        elif response_json is None and service_config.get("parse_analytics", False):
            failure_type = "JSON_PARSING_FAILURE"
            confidence_score = 88
            
        elif response_json:
            # Check db connection
            db_failed = False
            for k in ["db_ok", "database_ok", "database", "mongo_connected"]:
                if k in response_json and not response_json[k]:
                    db_failed = True
                    break
            
            # Check redis/cache
            redis_failed = False
            for k in ["redis_ok", "cache_ok", "redis", "redis_connected"]:
                if k in response_json and not response_json[k]:
                    redis_failed = True
                    break
            
            if db_failed:
                failure_type = "DATABASE_UNAVAILABLE"
                confidence_score = 96
            elif redis_failed:
                failure_type = "REDIS_UNAVAILABLE"
                confidence_score = 96
            else:
                # Schema mismatch checks
                health_schema = service_config.get("health_schema")
                if health_schema:
                    required_fields = health_schema.get("required_fields", [])
                    expected_values = health_schema.get("expected_values", {})
                    
                    missing = [f for f in required_fields if f not in response_json]
                    if missing:
                        failure_type = "INVALID_RESPONSE_SCHEMA"
                        details = ", ".join(missing)
                        confidence_score = 94
                    else:
                        mismatches = []
                        for field, expected in expected_values.items():
                            actual = response_json.get(field)
                            if actual != expected:
                                mismatches.append(f"{field} (expected: {expected}, got: {actual})")
                        if mismatches:
                            failure_type = "UNEXPECTED_RESPONSE_BODY"
                            details = "; ".join(mismatches)
                            confidence_score = 94
                
                # High latency check
                if failure_type == "UNKNOWN_FAILURE" and latency_ms >= 3000:
                    failure_type = "HIGH_LATENCY_DEGRADATION"
                    confidence_score = 85

        # ── Construct details and technical summary ───────────────────────────
        config_meta = cls.CLASSIFICATIONS.get(failure_type, cls.CLASSIFICATIONS["UNKNOWN_FAILURE"])
        
        # Build technical summary
        summary = config_meta["summary_template"].format(
            hostname=hostname,
            url=url,
            port=port,
            status_code=status_code,
            details=details,
            latency=latency_ms,
            expected=service_config.get("health_schema", {}).get("expected_values", {}),
            actual=str(response_json)
        )

        return {
            "failure_type": failure_type,
            "severity": config_meta["severity"],
            "confidence_score": confidence_score,
            "recommended_action": config_meta["recommended_action"],
            "technical_summary": summary
        }
