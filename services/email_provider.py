import os
import smtplib
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

# Load configuration from environment variables
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM", EMAIL_USER)
ALERT_RECIPIENTS = os.getenv("ALERT_RECIPIENTS", "")

def send_alert_email(subject, html_content):
    """
    Send an HTML alert email with retry mechanisms.
    """
    if not EMAIL_HOST or not EMAIL_USER or not EMAIL_PASSWORD or not ALERT_RECIPIENTS:
        logging.warning("[EmailProvider] SMTP variables not fully configured. Skipping email dispatch.")
        return False
        
    recipients = [r.strip() for r in ALERT_RECIPIENTS.split(",") if r.strip()]
    if not recipients:
        logging.warning("[EmailProvider] No recipients configured in ALERT_RECIPIENTS.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html"))

    max_retries = 3
    retry_delay = 2 # seconds
    
    for attempt in range(max_retries):
        try:
            # Connect to SMTP server
            server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15)
            server.ehlo()
            if EMAIL_PORT == 587:
                server.starttls() # Enable security
                server.ehlo()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            
            # Send email
            server.sendmail(EMAIL_FROM, recipients, msg.as_string())
            server.quit()
            logging.info(f"[EmailProvider] Alert email sent successfully: '{subject}' to {recipients}")
            return True
        except Exception as e:
            logging.error(f"[EmailProvider] Attempt {attempt+1} failed to send email: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logging.error("[EmailProvider] Failed to send email after maximum retries.")
                return False

def format_service_down_template(service_name, url, timestamp, reason, last_success_time, ai_analysis=None):
    """Format HTML email template for SERVICE_DOWN alerts."""
    ai_section = ""
    if ai_analysis:
        ai_section = f"""
        <div style="margin-top: 20px; padding: 15px; border: 1px solid #c7d2fe; border-radius: 8px; background-color: #f5f7ff; color: #3730a3;">
            <h3 style="margin-top: 0; color: #4338ca; font-size: 13px; font-weight: bold; text-transform: uppercase; font-family: sans-serif; display: flex; align-items: center; gap: 6px;">
                🧠 AI Root Cause Diagnostics
            </h3>
            <p style="margin-bottom: 0; font-size: 12px; white-space: pre-wrap; font-family: monospace; line-height: 1.5; color: #1e1b4b;">{ai_analysis}</p>
        </div>
        """

    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f2cfcf; border-radius: 10px; background-color: #fff8f8;">
                <h2 style="color: #d9534f; margin-top: 0;">🚨 Outage Detected: {service_name} is DOWN</h2>
                <hr style="border: 0; border-top: 1px solid #eed3d3; margin: 15px 0;">
                <p>An outage was detected during the automated health monitoring cycle.</p>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; width: 150px;">Service Name:</td>
                        <td style="padding: 6px 0;">{service_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Connection URL:</td>
                        <td style="padding: 6px 0;"><a href="{url}" style="color: #6366f1; text-decoration: none;">{url}</a></td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Timestamp (IST):</td>
                        <td style="padding: 6px 0;">{timestamp}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; color: #d9534f;">Failure Reason:</td>
                        <td style="padding: 6px 0; color: #d9534f; font-family: monospace;">{reason}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Last Success:</td>
                        <td style="padding: 6px 0;">{last_success_time}</td>
                    </tr>
                </table>
                {ai_section}
                <p style="font-size: 11px; color: #999; border-top: 1px solid #eee; padding-top: 10px; margin-top: 20px; margin-bottom: 0;">
                    This is an automated notification from ServerGuardian. Duplicate alerts for this incident are suppressed.
                </p>
            </div>
        </body>
    </html>
    """

def format_service_recovered_template(service_name, downtime_duration, recovery_time, current_status):
    """Format HTML email template for SERVICE_RECOVERED alerts."""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #d0ebd0; border-radius: 10px; background-color: #f7fdf7;">
                <h2 style="color: #4cae4c; margin-top: 0;">✅ Service Recovered: {service_name} is ONLINE</h2>
                <hr style="border: 0; border-top: 1px solid #d4ecd4; margin: 15px 0;">
                <p>The service has returned to a healthy state and is responding normally.</p>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; width: 150px;">Service Name:</td>
                        <td style="padding: 6px 0;">{service_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; color: #4cae4c;">Current Status:</td>
                        <td style="padding: 6px 0; color: #4cae4c; font-weight: bold;">{current_status}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Recovery Time (IST):</td>
                        <td style="padding: 6px 0;">{recovery_time}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Downtime Duration:</td>
                        <td style="padding: 6px 0; font-weight: bold; color: #666;">{downtime_duration}</td>
                    </tr>
                </table>
                <p style="font-size: 11px; color: #999; border-top: 1px solid #eee; padding-top: 10px; margin-bottom: 0;">
                    This is an automated notification from ServerGuardian.
                </p>
            </div>
        </body>
    </html>
    """

def format_high_latency_template(service_name, current_latency, threshold, timestamp):
    """Format HTML email template for HIGH_LATENCY alerts."""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #fcefa1; border-radius: 10px; background-color: #fffdf0;">
                <h2 style="color: #f0ad4e; margin-top: 0;">⚠️ High Latency Warning: {service_name}</h2>
                <hr style="border: 0; border-top: 1px solid #fbf2c2; margin: 15px 0;">
                <p>The response time of the service has exceeded the configured threshold.</p>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; width: 150px;">Service Name:</td>
                        <td style="padding: 6px 0;">{service_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold; color: #f0ad4e;">Current Latency:</td>
                        <td style="padding: 6px 0; color: #f0ad4e; font-weight: bold;">{current_latency:.0f} ms</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Configured Threshold:</td>
                        <td style="padding: 6px 0;">{threshold:.0f} ms</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; font-weight: bold;">Timestamp (IST):</td>
                        <td style="padding: 6px 0;">{timestamp}</td>
                    </tr>
                </table>
                <p style="font-size: 11px; color: #999; border-top: 1px solid #eee; padding-top: 10px; margin-bottom: 0;">
                    This is an automated warning notification from ServerGuardian.
                </p>
            </div>
        </body>
    </html>
    """


def _reliability_badge(rating: str) -> str:
    colors = {
        "Excellent": "#22c55e", "Good": "#84cc16",
        "Warning": "#f59e0b", "Critical": "#ef4444"
    }
    color = colors.get(rating, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:bold;">{rating}</span>'


def format_weekly_report_template(report: dict) -> str:
    """Format a rich HTML weekly summary email from a report dict."""
    return _format_report_template(report, "Weekly")


def format_monthly_report_template(report: dict) -> str:
    """Format a rich HTML monthly summary email from a report dict."""
    return _format_report_template(report, "Monthly")


def _format_report_template(report: dict, period_label: str) -> str:
    """Shared HTML generator for weekly/monthly executive report emails."""
    period = report.get("period", "N/A")
    overall_uptime = report.get("overall_uptime_pct", 100.0)
    total_checks = report.get("total_checks", 0)
    total_failures = report.get("total_failures", 0)
    incidents = report.get("incidents_this_period", 0)
    sla_above = report.get("services_above_sla", 0)
    sla_below = report.get("services_below_sla", 0)
    sla_target = report.get("sla_target_pct", 99.0)
    avg_latency = report.get("avg_latency_ms")
    avg_mttr = report.get("avg_mttr_seconds")
    services = report.get("services", [])

    uptime_color = "#22c55e" if overall_uptime >= 99 else "#f59e0b" if overall_uptime >= 95 else "#ef4444"
    days = report.get("period_days", 7)
    uptime_key = f"uptime_{days}d"

    def _fmt_mttr(s):
        if s is None:
            return "N/A"
        if s < 60:
            return f"{s:.0f}s"
        elif s < 3600:
            return f"{s // 60:.0f}m"
        return f"{s // 3600:.0f}h {(s % 3600) // 60:.0f}m"

    svc_rows = ""
    for s in services:
        uptime = s.get(uptime_key, s.get("uptime_30d", 100.0))
        u_color = "#22c55e" if uptime >= 99 else "#f59e0b" if uptime >= 95 else "#ef4444"
        sla_icon = "✅" if s.get("sla_met") else "⚠️"
        svc_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 6px;font-weight:500;">{s.get("service_name","")}</td>
          <td style="padding:8px 6px;color:{u_color};font-weight:bold;">{uptime:.2f}%</td>
          <td style="padding:8px 6px;">{_reliability_badge(s.get("reliability_rating",""))}</td>
          <td style="padding:8px 6px;">{s.get("avg_latency_ms", 0):.0f} ms</td>
          <td style="padding:8px 6px;">{s.get("failure_count",0)}</td>
          <td style="padding:8px 6px;">{sla_icon} {sla_target:.0f}%</td>
        </tr>"""

    avg_lat_str = f"{avg_latency:.0f} ms" if avg_latency is not None else "N/A"

    return f"""
    <html>
      <body style="font-family:Arial,sans-serif;line-height:1.6;color:#1e293b;background:#f8fafc;">
        <div style="max-width:680px;margin:0 auto;padding:24px;">

          <div style="background:linear-gradient(135deg,#1e293b,#334155);border-radius:12px;padding:24px;color:#fff;margin-bottom:24px;">
            <h1 style="margin:0;font-size:22px;">📊 ServerGuardian Pro</h1>
            <p style="margin:4px 0 0;color:#94a3b8;">{period_label} Platform Health Report</p>
            <p style="margin:4px 0 0;font-size:13px;color:#64748b;">{period}</p>
          </div>

          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px;">
            <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e2e8f0;text-align:center;">
              <div style="font-size:28px;font-weight:bold;color:{uptime_color};">{overall_uptime:.2f}%</div>
              <div style="color:#64748b;font-size:12px;margin-top:4px;">Platform Uptime</div>
            </div>
            <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e2e8f0;text-align:center;">
              <div style="font-size:28px;font-weight:bold;color:#6366f1;">{total_checks:,}</div>
              <div style="color:#64748b;font-size:12px;margin-top:4px;">Total Checks</div>
            </div>
            <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e2e8f0;text-align:center;">
              <div style="font-size:28px;font-weight:bold;color:{'#ef4444' if incidents > 0 else '#22c55e'};">{incidents}</div>
              <div style="color:#64748b;font-size:12px;margin-top:4px;">Incidents</div>
            </div>
          </div>

          <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e2e8f0;margin-bottom:24px;">
            <table style="width:100%;font-size:13px;">
              <tr>
                <td style="color:#64748b;">Avg Latency</td>
                <td style="font-weight:bold;text-align:right;">{avg_lat_str}</td>
                <td style="color:#64748b;padding-left:24px;">Avg MTTR</td>
                <td style="font-weight:bold;text-align:right;">{_fmt_mttr(avg_mttr)}</td>
              </tr>
              <tr>
                <td style="color:#64748b;padding-top:8px;">Failures</td>
                <td style="font-weight:bold;text-align:right;">{total_failures}</td>
                <td style="color:#64748b;padding-left:24px;padding-top:8px;">SLA Compliance</td>
                <td style="font-weight:bold;text-align:right;">{sla_above}/{sla_above+sla_below} services</td>
              </tr>
            </table>
          </div>

          <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e2e8f0;margin-bottom:24px;">
            <h3 style="margin:0 0 12px;font-size:15px;color:#1e293b;">Service Performance</h3>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <thead>
                <tr style="background:#f1f5f9;text-align:left;">
                  <th style="padding:8px 6px;">Service</th>
                  <th style="padding:8px 6px;">Uptime</th>
                  <th style="padding:8px 6px;">Rating</th>
                  <th style="padding:8px 6px;">Avg Latency</th>
                  <th style="padding:8px 6px;">Failures</th>
                  <th style="padding:8px 6px;">SLA</th>
                </tr>
              </thead>
              <tbody>{svc_rows}</tbody>
            </table>
          </div>

          <p style="font-size:11px;color:#94a3b8;text-align:center;margin:0;">
            Auto-generated by ServerGuardian Pro · Do not reply to this email
          </p>
        </div>
      </body>
    </html>
    """
