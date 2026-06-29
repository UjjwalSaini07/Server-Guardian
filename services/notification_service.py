import logging
from datetime import datetime, timezone, timedelta
from config import LATENCY_PASS
from services.email_provider import (
    send_alert_email,
    format_service_down_template,
    format_service_recovered_template,
    format_high_latency_template
)
from services import incident_service

IST_OFFSET = timedelta(hours=5, minutes=30)

def _fmt_ist(dt: datetime) -> str:
    """Convert a UTC datetime to IST and format as 12-hour AM/PM string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist = dt + IST_OFFSET
    return ist.strftime("%d %b %Y, %I:%M:%S %p IST")

def format_duration(seconds):
    """Format duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    elif seconds < 3600:
        return f"{seconds // 60:.0f} minutes and {seconds % 60:.0f} seconds"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours:.0f} hours, {mins:.0f} minutes"

def process_alert(service_id, service_name, alert_type, severity, message, details, mongo_client):
    """
    Process an alert event: evaluates state, suppresses duplicates, dispatches emails, and logs to MongoDB.
    """
    try:
        db = mongo_client["ServerAutomation"]
        alerts_col = db["alerts"]
        state_col = db["alert_state"]
        
        now = datetime.now(timezone.utc)
        state_doc = state_col.find_one({"service_id": service_id}) or {}
        
        send_email = False
        subject = ""
        html_content = ""
        downtime_secs = None
        
        if alert_type == "SERVICE_DOWN":
            active_incident = state_doc.get("active_incident", False)
            if not active_incident:
                # New outage incident
                state_col.update_one(
                    {"service_id": service_id},
                    {
                        "$set": {
                            "active_incident": True,
                            "incident_started_at": now
                        }
                    },
                    upsert=True
                )
                # ── Phase 3B: Open formal incident ──────────────────────────
                incident_id = incident_service.open_incident(
                    service_id=service_id,
                    service_name=service_name,
                    trigger_alert_type=alert_type,
                    failure_reason=details.get("reason"),
                    severity="critical",
                    mongo_client=mongo_client,
                )
                
                # Retrieve AI analysis from the newly created incident
                ai_analysis = None
                if incident_id:
                    inc_doc = incident_service.get_incident_by_id(incident_id, mongo_client)
                    if inc_doc:
                        ai_analysis = inc_doc.get("ai_analysis")
                # ────────────────────────────────────────────────────────────
                send_email = True
                subject = f"🚨 Service Down - {service_name}"
                
                # Fetch last successful check if available from logs
                history_col = db["monitoring_history"]
                last_ok = history_col.find_one({
                    "service_id": service_id,
                    "status": "success"
                }, sort=[("timestamp", -1)])
                last_ok_ts = last_ok["timestamp"] if last_ok else None
                last_ok_str = _fmt_ist(last_ok_ts) if last_ok_ts else "No successful check recorded"
                
                html_content = format_service_down_template(
                    service_name=service_name,
                    url=details.get("url", "-"),
                    timestamp=_fmt_ist(now),
                    reason=details.get("reason", "Unknown error"),
                    last_success_time=last_ok_str,
                    ai_analysis=ai_analysis
                )
            else:
                # Outage is already active, suppress duplicate notification
                logging.info(f"[NotificationService] Outage alert suppressed for {service_name} (active incident already exists).")
                alerts_col.insert_one({
                    "service_id": service_id,
                    "service_name": service_name,
                    "alert_type": alert_type,
                    "severity": severity,
                    "message": message,
                    "created_at": now,
                    "sent": False
                })
                return
                
        elif alert_type == "SERVICE_RECOVERED":
            active_incident = state_doc.get("active_incident", False)
            if active_incident:
                # Incident recovery
                started_at = state_doc.get("incident_started_at", now)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                downtime_secs = (now - started_at).total_seconds()
                downtime_str = format_duration(downtime_secs)
                
                state_col.update_one(
                    {"service_id": service_id},
                    {
                        "$set": {
                            "active_incident": False
                        }
                    },
                    upsert=True
                )
                # ── Phase 3B: Resolve formal incident ───────────────────────
                incident_service.resolve_incident(
                    service_id=service_id,
                    mongo_client=mongo_client,
                )
                # ────────────────────────────────────────────────────────────
                send_email = True
                subject = f"✅ Service Recovered - {service_name}"
                html_content = format_service_recovered_template(
                    service_name=service_name,
                    downtime_duration=downtime_str,
                    recovery_time=_fmt_ist(now),
                    current_status=details.get("status", "SUCCESS")
                )
            else:
                # No active incident, suppress recovery alert
                return
                
        elif alert_type == "HIGH_LATENCY":
            if not LATENCY_PASS:
                logging.info(f"[NotificationService] Suppressing latency notification for {service_name} because LATENCY_PASS is False.")
                return
            # Prevent latency warnings spamming (rate limit to once per 30 minutes)
            last_warning = state_doc.get("last_latency_warning_at")
            should_warn = True
            if last_warning:
                if last_warning.tzinfo is None:
                    last_warning = last_warning.replace(tzinfo=timezone.utc)
                elapsed_mins = (now - last_warning).total_seconds() / 60
                if elapsed_mins < 30:
                    should_warn = False
                    
            if should_warn:
                state_col.update_one(
                    {"service_id": service_id},
                    {
                        "$set": {
                            "last_latency_warning_at": now
                        }
                    },
                    upsert=True
                )
                send_email = True
                subject = f"⚠️ High Latency Detected - {service_name}"
                html_content = format_high_latency_template(
                    service_name=service_name,
                    current_latency=details.get("latency", 0),
                    threshold=details.get("threshold", 3000),
                    timestamp=_fmt_ist(now)
                )
            else:
                logging.info(f"[NotificationService] High latency warning rate-limited for {service_name}.")
                alerts_col.insert_one({
                    "service_id": service_id,
                    "service_name": service_name,
                    "alert_type": alert_type,
                    "severity": severity,
                    "message": message,
                    "created_at": now,
                    "sent": False
                })
                return
                
        elif alert_type == "HEALTH_SCORE_DEGRADED":
            # Alert on critical health rating drops
            degraded_flag = state_doc.get("uptime_degraded", False)
            if not degraded_flag:
                state_col.update_one(
                    {"service_id": service_id},
                    {
                        "$set": {
                            "uptime_degraded": True
                        }
                    },
                    upsert=True
                )
                send_email = True
                subject = f"🚨 Uptime Degradation - {service_name}"
                html_content = f"""
                <html>
                    <body>
                        <h2>🚨 Service Reliability Degraded: {service_name}</h2>
                        <p>The 30-day health score has dropped below the threshold of {details.get('threshold', 95)}%.</p>
                        <p>Current 30-day availability: <b>{details.get('uptime', 0.0):.2f}%</b></p>
                        <p>Timestamp (IST): {_fmt_ist(now)}</p>
                    </body>
                </html>
                """
            else:
                # Already logged as degraded, suppress email
                alerts_col.insert_one({
                    "service_id": service_id,
                    "service_name": service_name,
                    "alert_type": alert_type,
                    "severity": severity,
                    "message": message,
                    "created_at": now,
                    "sent": False
                })
                return
                
        elif alert_type == "HEALTH_SCORE_RECOVERED":
            # Uptime recovered above critical threshold
            degraded_flag = state_doc.get("uptime_degraded", False)
            if degraded_flag:
                state_col.update_one(
                    {"service_id": service_id},
                    {
                        "$set": {
                            "uptime_degraded": False
                        }
                    },
                    upsert=True
                )
                send_email = True
                subject = f"✅ Uptime Restored - {service_name}"
                html_content = f"""
                <html>
                    <body>
                        <h2>✅ Service Reliability Restored: {service_name}</h2>
                        <p>The 30-day health score has recovered and is above the threshold.</p>
                        <p>Current 30-day availability: <b>{details.get('uptime', 0.0):.2f}%</b></p>
                        <p>Timestamp (IST): {_fmt_ist(now)}</p>
                    </body>
                </html>
                """
            else:
                return

        # Handle sending email and storing alerts log in MongoDB
        sent_status = False
        if send_email:
            sent_status = send_alert_email(subject, html_content)
            
        alert_doc = {
            "service_id": service_id,
            "service_name": service_name,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "created_at": now,
            "sent": sent_status
        }
        if downtime_secs is not None:
            alert_doc["downtime_seconds"] = downtime_secs
            
        alerts_col.insert_one(alert_doc)
        logging.info(f"[NotificationService] Alert logged in DB: {alert_type} for {service_name} (sent={sent_status})")
        
    except Exception as e:
        logging.error(f"[NotificationService] Error processing alert event: {e}")
