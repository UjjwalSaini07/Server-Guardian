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

def format_service_down_template(service_name, url, timestamp, reason, last_success_time):
    """Format HTML email template for SERVICE_DOWN alerts."""
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
                        <td style="padding: 6px 0; font-weight: bold;">Timestamp (UTC):</td>
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
                <p style="font-size: 11px; color: #999; border-top: 1px solid #eee; padding-top: 10px; margin-bottom: 0;">
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
                        <td style="padding: 6px 0; font-weight: bold;">Recovery Time (UTC):</td>
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
                        <td style="padding: 6px 0; font-weight: bold;">Timestamp (UTC):</td>
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
