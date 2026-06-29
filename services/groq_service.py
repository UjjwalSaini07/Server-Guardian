import os
import requests
import logging

logger = logging.getLogger(__name__)

def generate_groq_analysis(service_id: str, service_name: str, failure_reason: str, mongo_client) -> str:
    """
    Generate SRE-level root cause analysis and diagnostics using Groq AI.
    """
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("[GroqService] GROQ_API_KEY is not set. Skipping AI diagnostics.")
        return ""

    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Gather historical context from last 5 runs to analyze trends (Phase 3 addition)
    history_context = []
    try:
        db = mongo_client["ServerAutomation"]
        recent_logs = list(
            db["monitoring_history"]
            .find({"service_id": service_id})
            .sort("timestamp", -1)
            .limit(5)
        )
        for log in recent_logs:
            ts = log.get("timestamp")
            status = log.get("status")
            code = log.get("status_code")
            lat = log.get("latency_ms")
            reason = log.get("failure_reason")
            lat_str = f"{lat:.1f}ms" if lat is not None else "N/A"
            history_context.append(
                f"- Time: {ts}, Status: {status}, HTTP Code: {code}, Latency: {lat_str}, Error: {reason or 'None'}"
            )
    except Exception as db_err:
        logger.error(f"[GroqService] Failed to fetch history context: {db_err}")
        
    history_str = "\n".join(history_context) if history_context else "- No historical logs available."

    prompt = f"""You are ServerGuardian AI, an elite Site Reliability Engineer (SRE).
A server failure has occurred. Provide a concise, professional, and technical diagnostic analysis.

Service Name: {service_name}
Service ID: {service_id}
Reported Failure: {failure_reason}

Recent Monitoring History (last 5 runs):
{history_str}

Please respond with exactly two sections in plain text (no markdown headings, but bullet points are fine):

DIAGNOSTIC SUMMARY:
[Provide 2-3 sentences explaining the likely root cause based on the error and history]

RECOMMENDED ACTIONS:
[Provide 2-3 bullet points of SRE troubleshooting steps or resolution actions]
"""

    headers = {
        "Authorization": f"Bearer {groq_api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": groq_model,
        "messages": [
            {"role": "system", "content": "You are a helpful SRE assistant. Be concise, direct, and structured."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 500
    }
    
    try:
        logger.info(f"[GroqService] Requesting AI diagnostics for {service_name} using {groq_model}...")
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=15
        )
        
        if response.status_code == 200:
            res_json = response.json()
            analysis = res_json["choices"][0]["message"]["content"].strip()
            logger.info(f"[GroqService] Diagnostics generated successfully.")
            return analysis
        else:
            logger.error(f"[GroqService] Groq API returned error status {response.status_code}: {response.text}")
            return f"Failed to generate AI analysis. Groq API returned status {response.status_code}."
    except Exception as e:
        logger.error(f"[GroqService] Exception querying Groq API: {e}")
        return f"Failed to generate AI analysis due to connection/timeout error: {str(e)}"
