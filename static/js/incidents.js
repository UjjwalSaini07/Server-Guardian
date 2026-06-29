(function () {
  'use strict';

  // Global variables to hold active report data and current state
  let currentReportTab = 'weekly';
  let activeReportData = null;
  let activeIncidentId = null;

  // Cache TTL settings (matches existing index.html strategy)
  const CACHE_TTL = {
    incidents: 10000,       // 10s
    incidentMetrics: 30000, // 30s
    reports: 60000          // 60s
  };

  const cache = {};

  async function fetchWithCache(url, ttl) {
    const now = Date.now();
    if (cache[url] && (now - cache[url].timestamp < ttl)) {
      return cache[url].data;
    }
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Fetch failed for ${url} with status ${res.status}`);
    }
    const data = await res.json();
    cache[url] = {
      timestamp: now,
      data: data
    };
    return data;
  }

  // Clear cache for a specific URL prefix (useful on mutate)
  function clearCachePrefix(prefix) {
    for (const key in cache) {
      if (key.startsWith(prefix)) {
        delete cache[key];
      }
    }
  }

  // ── Incidents Dashboard ───────────────────────────────────────────────────
  async function loadIncidentsDashboard() {
    try {
      // 1. Fetch and render last 20 incidents
      const incidents = await fetchWithCache('/api/incidents?limit=20', CACHE_TTL.incidents);
      renderIncidentTimelineTable(incidents);

      // 2. Fetch and render incident metrics for MTTD / MTTR / Total Alerts
      const metrics = await fetchWithCache('/api/incidents/metrics?days=30', CACHE_TTL.incidentMetrics);
      renderIncidentMetrics(metrics);
    } catch (err) {
      console.error("[IncidentsJS] Failed to load incidents dashboard:", err);
      if (typeof addToTerminal === 'function') {
        addToTerminal(`Failed to update Incident Timeline: ${err.message || err}`, "error");
      }
    }
  }

  function renderIncidentTimelineTable(incidents) {
    const body = document.getElementById('incidents-table-body');
    if (!body) return;

    if (!incidents || incidents.length === 0) {
      body.innerHTML = `
        <tr>
          <td colspan="6" class="py-8 text-center text-slate-500 font-outfit">
            No incidents logged in the timeline.
          </td>
        </tr>
      `;
      return;
    }

    body.innerHTML = incidents.map(inc => {
      let severityClass = 'analytics-badge-excellent'; // Default fallback
      if (inc.severity === 'critical') severityClass = 'analytics-badge-critical';
      else if (inc.severity === 'warning') severityClass = 'analytics-badge-warning';
      else if (inc.severity === 'info') severityClass = 'analytics-badge-good';

      let statusClass = 'bg-slate-500/10 text-slate-400 border border-slate-500/15';
      if (inc.status === 'open') {
        statusClass = 'bg-rose-500/10 text-rose-400 border border-rose-500/20';
      } else if (inc.status === 'acknowledged') {
        statusClass = 'bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse';
      } else if (inc.status === 'resolved') {
        statusClass = 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
      }

      const startedStr = typeof formatIST === 'function' ? formatIST(inc.started_at) : inc.started_at;
      
      // Calculate duration/MTTR display
      let mttrDisplay = '--';
      if (inc.status === 'resolved' && inc.mttr_seconds !== null && inc.mttr_seconds !== undefined) {
        mttrDisplay = formatDuration(inc.mttr_seconds);
      } else {
        const start = new Date(inc.started_at);
        const durationSec = Math.floor((Date.now() - start.getTime()) / 1000);
        mttrDisplay = `<span class="text-rose-400 font-bold">${formatDuration(durationSec)} (Active)</span>`;
      }

      return `
        <tr onclick="openIncidentModal('${inc.incident_id}')" class="cursor-pointer hover:bg-white/5 transition-all h-11">
          <td class="py-2.5 px-4 font-mono font-bold text-slate-400">${inc.incident_id}</td>
          <td class="py-2.5 px-4 font-semibold text-slate-100 font-outfit">${inc.service_name}</td>
          <td class="py-2.5 px-4 text-center">
            <span class="px-2 py-0.5 rounded text-[8px] font-bold ${severityClass} uppercase">${inc.severity}</span>
          </td>
          <td class="py-2.5 px-4 font-mono text-slate-400">${startedStr}</td>
          <td class="py-2.5 px-4 text-right font-mono text-slate-200">${mttrDisplay}</td>
          <td class="py-2.5 px-4 text-center">
            <span class="px-2 py-0.5 rounded text-[8px] font-bold ${statusClass} uppercase">${inc.status}</span>
          </td>
        </tr>
      `;
    }).join('');
  }

  function renderIncidentMetrics(metrics) {
    const totalEl = document.getElementById('analytics-total-alerts');
    const mttdEl = document.getElementById('analytics-mttd');
    const mttrEl = document.getElementById('analytics-mttr');

    if (totalEl) {
      totalEl.textContent = metrics.total_incidents !== undefined ? metrics.total_incidents : '-';
    }
    if (mttdEl) {
      if (metrics.avg_mttd_seconds !== null && metrics.avg_mttd_seconds !== undefined) {
        mttdEl.textContent = formatDuration(metrics.avg_mttd_seconds);
      } else {
        mttdEl.textContent = '--';
      }
    }
    if (mttrEl) {
      if (metrics.avg_mttr_seconds !== null && metrics.avg_mttr_seconds !== undefined) {
        mttrEl.textContent = formatDuration(metrics.avg_mttr_seconds);
      } else {
        mttrEl.textContent = '--';
      }
    }
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    if (mins < 60) return `${mins}m ${secs}s`;
    const hrs = Math.floor(mins / 60);
    const rMins = mins % 60;
    return `${hrs}h ${rMins}m`;
  }

  // ── Incident Modal & Lifecycle actions ─────────────────────────────────────
  async function openIncidentModal(incidentId) {
    try {
      activeIncidentId = incidentId;
      const modal = document.getElementById('incident-modal');
      if (!modal) return;

      modal.classList.remove('hidden');

      // Fetch live data (avoiding long cache for details)
      const data = await fetchWithCache(`/api/incidents/${incidentId}`, 1000);

      document.getElementById('inc-modal-title').textContent = data.incident_id || 'Incident Details';
      document.getElementById('inc-modal-service-name').textContent = `Service: ${data.service_name || 'N/A'} (${data.service_id || 'N/A'})`;

      let ratingClass = 'analytics-badge-excellent';
      if (data.status === 'open') ratingClass = 'analytics-badge-critical';
      else if (data.status === 'acknowledged') ratingClass = 'analytics-badge-warning';
      else if (data.status === 'resolved') ratingClass = 'analytics-badge-excellent';

      const statusBadge = document.getElementById('inc-modal-status-badge');
      statusBadge.textContent = data.status || 'UNKNOWN';
      statusBadge.className = `px-2 py-0.5 rounded border text-[9px] font-bold tracking-wider uppercase ${ratingClass}`;

      let severityBadgeColor = 'text-slate-400 bg-white/5 border border-white/10';
      if (data.severity === 'critical') severityBadgeColor = 'text-rose-400 bg-rose-500/10 border border-rose-500/20';
      else if (data.severity === 'warning') severityBadgeColor = 'text-amber-400 bg-amber-500/10 border border-amber-500/20';
      else if (data.severity === 'info') severityBadgeColor = 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/20';

      const sevEl = document.getElementById('inc-modal-severity');
      sevEl.textContent = data.severity || '--';
      sevEl.className = `text-xs font-bold block mt-1 uppercase ${sevEl.className.split(' ').filter(c => !c.includes('text-') && !c.includes('bg-') && !c.includes('border-')).join(' ')} ${severityBadgeColor} p-1 rounded text-center`;

      document.getElementById('inc-modal-mttd').textContent = data.mttd_seconds !== null ? formatDuration(data.mttd_seconds) : '--';
      
      let durationStr = '--';
      if (data.resolved_at) {
        durationStr = formatDuration(data.mttr_seconds);
      } else {
        const start = new Date(data.started_at);
        const activeSec = Math.floor((Date.now() - start.getTime()) / 1000);
        durationStr = `${formatDuration(activeSec)} (Active)`;
      }
      document.getElementById('inc-modal-mttr').textContent = durationStr;

      document.getElementById('inc-modal-trigger').textContent = data.trigger_alert_type || '--';
      document.getElementById('inc-modal-reason').textContent = data.failure_reason || 'No failure reason captured.';

      // AI Diagnostics (Groq) rendering
      const aiContainer = document.getElementById('inc-modal-ai-analysis-container');
      const aiText = document.getElementById('inc-modal-ai-analysis');
      if (aiContainer && aiText) {
        if (data.ai_analysis) {
          aiText.textContent = data.ai_analysis;
          aiContainer.classList.remove('hidden');
        } else {
          aiContainer.classList.add('hidden');
        }
      }

      // Timeline events rendering
      const timelineContainer = document.getElementById('inc-modal-timeline');
      if (timelineContainer && data.timeline) {
        timelineContainer.innerHTML = data.timeline.map(ev => {
          const evTime = typeof formatIST === 'function' ? formatIST(ev.timestamp) : ev.timestamp;
          let dotColor = 'bg-slate-500';
          if (ev.event === 'INCIDENT_OPENED') dotColor = 'bg-rose-500';
          else if (ev.event === 'ACKNOWLEDGED') dotColor = 'bg-amber-500 animate-pulse';
          else if (ev.event === 'RESOLVED') dotColor = 'bg-emerald-500';

          return `
            <div class="relative pl-6 pb-4 last:pb-0">
              <span class="absolute left-[-21px] top-1.5 flex h-2.5 w-2.5 rounded-full ${dotColor}"></span>
              <span class="text-[9px] uppercase font-bold text-slate-500 block font-mono">${evTime}</span>
              <span class="text-xs font-bold font-outfit text-slate-200 mt-0.5 block">${ev.event}</span>
              ${ev.note ? `<p class="text-[11px] text-slate-400 mt-0.5">${ev.note}</p>` : ''}
            </div>
          `;
        }).join('');
      }

      // "Acknowledge" action block
      const actionContainer = document.getElementById('inc-modal-action-container');
      if (actionContainer) {
        if (data.status === 'open') {
          actionContainer.classList.remove('hidden');
        } else {
          actionContainer.classList.add('hidden');
        }
      }
    } catch (err) {
      console.error("[IncidentsJS] Failed to open incident modal:", err);
    }
  }

  function closeIncidentModal() {
    const modal = document.getElementById('incident-modal');
    if (modal) modal.classList.add('hidden');
    activeIncidentId = null;
  }

  async function acknowledgeActiveIncident() {
    if (!activeIncidentId) return;
    const btn = document.getElementById('inc-modal-ack-btn');
    if (btn) {
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-1"></i> Acknowledging...`;
      btn.disabled = true;
    }

    try {
      const res = await fetch(`/api/incidents/${activeIncidentId}/acknowledge?acknowledged_by=SRE_Dashboard`, {
        method: 'POST'
      });

      if (!res.ok) {
        throw new Error("Acknowledgment request failed.");
      }

      if (typeof addToTerminal === 'function') {
        addToTerminal(`Incident ${activeIncidentId} acknowledged successfully by SRE_Dashboard.`, "success");
      }

      clearCachePrefix('/api/incidents');
      
      // Refresh timeline table and close modal
      await loadIncidentsDashboard();
      closeIncidentModal();
    } catch (err) {
      console.error("[IncidentsJS] Acknowledge failed:", err);
      if (typeof addToTerminal === 'function') {
        addToTerminal(`Failed to acknowledge incident ${activeIncidentId}: ${err.message || err}`, "error");
      }
    } finally {
      if (btn) {
        btn.innerHTML = `<i class="fa-solid fa-check"></i> Acknowledge Incident`;
        btn.disabled = false;
      }
    }
  }

  // ── Executive Reports & SLA Panel ─────────────────────────────────────────
  let reportsPanelVisible = false;

  function toggleReportsPanel() {
    const content = document.getElementById('reports-panel-content');
    const icon = document.getElementById('reports-toggle-icon');
    if (!content || !icon) return;

    reportsPanelVisible = !reportsPanelVisible;

    if (reportsPanelVisible) {
      content.classList.remove('hidden');
      icon.className = 'fa-solid fa-chevron-up text-sm';
      // Load reports initial view if not done
      loadReportsDashboard();
    } else {
      content.classList.add('hidden');
      icon.className = 'fa-solid fa-chevron-down text-sm';
    }
  }

  async function loadReportsDashboard() {
    try {
      if (currentReportTab === 'weekly') {
        const data = await fetchWithCache('/api/reports/weekly', CACHE_TTL.reports);
        activeReportData = data;
        renderReportSummary(data, 'Weekly');
      } else if (currentReportTab === 'monthly') {
        const data = await fetchWithCache('/api/reports/monthly', CACHE_TTL.reports);
        activeReportData = data;
        renderReportSummary(data, 'Monthly');
      } else if (currentReportTab === 'benchmarks') {
        const data = await fetchWithCache('/api/reports/benchmarks', CACHE_TTL.reports);
        activeReportData = data;
        renderBenchmarks(data);
      }
    } catch (err) {
      console.error("[IncidentsJS] Failed to load reports:", err);
      if (typeof addToTerminal === 'function') {
        addToTerminal(`Failed to update Executive Reports: ${err.message || err}`, "error");
      }
    }
  }

  function renderReportSummary(report, typeLabel) {
    document.getElementById('report-summary-view').classList.remove('hidden');
    document.getElementById('report-benchmarks-view').classList.add('hidden');

    document.getElementById('report-period-label').textContent = `${typeLabel} Report Period: ${report.period || 'N/A'}`;
    document.getElementById('report-sla-target-label').textContent = `${(report.sla_target_pct || 99.0).toFixed(1)}%`;

    document.getElementById('rep-kpi-uptime').textContent = report.overall_uptime_pct !== undefined ? `${report.overall_uptime_pct.toFixed(2)}%` : '--%';
    document.getElementById('rep-kpi-audits').textContent = report.total_checks !== undefined ? report.total_checks.toLocaleString() : '--';
    document.getElementById('rep-kpi-failures').textContent = report.total_failures !== undefined ? report.total_failures.toLocaleString() : '--';
    document.getElementById('rep-kpi-incidents').textContent = report.incidents_this_period !== undefined ? report.incidents_this_period : '--';
    document.getElementById('rep-kpi-mttr').textContent = report.avg_mttr_seconds !== null && report.avg_mttr_seconds !== undefined ? formatDuration(report.avg_mttr_seconds) : '--';

    const tableBody = document.getElementById('report-services-table-body');
    if (!tableBody) return;

    if (!report.services || report.services.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="6" class="py-8 text-center text-slate-500 font-outfit">No service report data.</td></tr>`;
      return;
    }

    tableBody.innerHTML = report.services.map(s => {
      let ratingClass = 'analytics-badge-excellent';
      if (s.reliability_rating === 'Excellent') ratingClass = 'analytics-badge-excellent';
      else if (s.reliability_rating === 'Good') ratingClass = 'analytics-badge-good';
      else if (s.reliability_rating === 'Warning') ratingClass = 'analytics-badge-warning';
      else if (s.reliability_rating === 'Critical') ratingClass = 'analytics-badge-critical';

      const metSla = s.sla_met === true;
      const slaClass = metSla ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
      const slaText = metSla ? 'MET' : 'BREACHED';

      const formatLatency = (val) => (val !== null && val !== undefined) ? `${val.toFixed(1)} ms` : '-- ms';

      return `
        <tr class="h-10 border-b border-white/5 last:border-0 hover:bg-white/5 transition-all">
          <td class="py-2 px-4 font-semibold text-slate-100 font-outfit">${s.service_name}</td>
          <td class="py-2 px-4 text-right font-mono font-bold text-slate-200">${(s.uptime_7d || s.uptime_30d || 100.0).toFixed(2)}%</td>
          <td class="py-2 px-4 text-center">
            <span class="px-2 py-0.5 rounded text-[8px] font-bold ${ratingClass}">${s.reliability_rating}</span>
          </td>
          <td class="py-2 px-4 text-right font-mono text-slate-400">${formatLatency(s.avg_latency_ms)}</td>
          <td class="py-2 px-4 text-right font-mono text-slate-400">${formatLatency(s.p95_latency_ms)}</td>
          <td class="py-2 px-4 text-center font-mono ${slaClass}">${slaText}</td>
        </tr>
      `;
    }).join('');
  }

  function renderBenchmarks(benchmarks) {
    document.getElementById('report-summary-view').classList.add('hidden');
    document.getElementById('report-benchmarks-view').classList.remove('hidden');

    const tableBody = document.getElementById('report-benchmarks-table-body');
    if (!tableBody) return;

    if (!benchmarks || Object.keys(benchmarks).length === 0) {
      tableBody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-slate-500 font-outfit">No benchmark metrics calculated.</td></tr>`;
      return;
    }

    // Transform dictionary benchmarks into array
    const servicesList = Object.keys(benchmarks);
    tableBody.innerHTML = servicesList.map(name => {
      const stats = benchmarks[name];
      const metSla = stats.sla_target_met === true;
      const slaClass = metSla ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
      const slaText = metSla ? 'YES' : 'NO';

      return `
        <tr class="h-10 border-b border-white/5 last:border-0 hover:bg-white/5 transition-all">
          <td class="py-2 px-4 font-semibold text-slate-100 font-outfit">${name}</td>
          <td class="py-2 px-4 text-right font-mono text-slate-200">#${stats.uptime_rank}</td>
          <td class="py-2 px-4 text-right font-mono text-slate-200">#${stats.latency_rank}</td>
          <td class="py-2 px-4 text-right font-mono text-slate-200">#${stats.incident_frequency_rank}</td>
          <td class="py-2 px-4 text-center font-mono ${slaClass}">${slaText}</td>
        </tr>
      `;
    }).join('');
  }

  function switchReportTab(tab) {
    const tabs = ['weekly', 'monthly', 'benchmarks'];
    tabs.forEach(t => {
      const el = document.getElementById(`tab-${t}`);
      if (el) {
        if (t === tab) {
          el.className = 'px-4 py-2 border-b-2 border-indigo-500 text-indigo-300 font-medium text-xs font-outfit transition-all';
        } else {
          el.className = 'px-4 py-2 border-b-2 border-transparent text-slate-400 hover:text-slate-200 font-medium text-xs font-outfit transition-all';
        }
      }
    });

    currentReportTab = tab;
    loadReportsDashboard();
  }

  function downloadActiveReport() {
    if (!activeReportData) return;
    const blob = new Blob([JSON.stringify(activeReportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ServerGuardian_${currentReportTab}_report_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Expose functions globally so they can be called from HTML onclicks
  window.loadIncidentsDashboard = loadIncidentsDashboard;
  window.loadReportsDashboard = loadReportsDashboard;
  window.openIncidentModal = openIncidentModal;
  window.closeIncidentModal = closeIncidentModal;
  window.acknowledgeActiveIncident = acknowledgeActiveIncident;
  window.toggleReportsPanel = toggleReportsPanel;
  window.switchReportTab = switchReportTab;
  window.downloadActiveReport = downloadActiveReport;

})();
