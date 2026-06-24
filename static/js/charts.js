(function (global) {
  'use strict';

  // ── Color palette (matches existing dashboard tokens) ─────────────────
  const COLORS = {
    indigo:    '#6366f1',
    pink:      '#d946ef',
    emerald:   '#10b981',
    amber:     '#f59e0b',
    rose:      '#f43f5e',
    slate:     '#94a3b8',
    // Per-service colours (consistent with existing latency chart)
    quillix:   '#6366f1',
    affiliate: '#d946ef',
    sentinel:  '#10b981',
    vision:    '#f59e0b',
  };

  // ── Base chart defaults (dark glassmorphism theme) ─────────────────────
  const CHART_DEFAULTS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 550, easing: 'easeOutQuart' },
    plugins: {
      legend: {
        labels: {
          color: '#94a3b8',
          boxWidth: 10,
          boxHeight: 10,
          padding: 12,
          font: { family: 'Outfit', size: 10, weight: '500' },
        },
      },
      tooltip: {
        backgroundColor: 'rgba(10, 10, 12, 0.95)',
        titleColor: '#e2e8f0',
        bodyColor:  '#cbd5e1',
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.08)',
        padding: 10,
        displayColors: true,
      },
    },
    scales: {
      x: {
        grid:  { color: 'rgba(255,255,255,0.02)' },
        ticks: { color: '#475569', font: { family: 'Space Mono', size: 9 } },
      },
      y: {
        grid:  { color: 'rgba(255,255,255,0.04)' },
        ticks: { color: '#475569', font: { family: 'Space Mono', size: 9 } },
      },
    },
  };

  // ── Utility: safe deep-merge (avoids full lodash dep) ─────────────────
  function deepMerge(base, override) {
    const result = Object.assign({}, base);
    for (const key in override) {
      if (
        override[key] !== null &&
        typeof override[key] === 'object' &&
        !Array.isArray(override[key]) &&
        typeof result[key] === 'object'
      ) {
        result[key] = deepMerge(result[key] || {}, override[key]);
      } else {
        result[key] = override[key];
      }
    }
    return result;
  }

  // ── destroyIfExists ────────────────────────────────────────────────────
  function destroyIfExists(instance) {
    if (instance && typeof instance.destroy === 'function') {
      try { instance.destroy(); } catch (_) {}
    }
    return null;
  }

  // ── Base line chart factory ────────────────────────────────────────────
  function createLineChart(canvasId, cfg) {
    const {
      labels      = [],
      datasets    = [],
      yLabel      = '',
      yMin        = null,
      yMax        = null,
      optOverride = {},
    } = cfg;

    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return null;
    const ctx = canvas.getContext('2d');

    const options = deepMerge(CHART_DEFAULTS, {
      plugins: {
        tooltip: {
          callbacks: {
            label: (c) =>
              ` ${c.dataset.label}: ${c.raw !== null ? Number(c.raw).toFixed(1) : 'N/A'}${yLabel}`,
          },
        },
      },
      scales: {
        y: Object.assign(
          {},
          yMin !== null ? { min: yMin } : {},
          yMax !== null ? { max: yMax } : {},
          { ticks: { callback: (v) => v + yLabel } }
        ),
      },
      ...optOverride,
    });

    return new Chart(ctx, { type: 'line', data: { labels, datasets }, options });
  }

  // ── Base bar chart factory ─────────────────────────────────────────────
  function createBarChart(canvasId, cfg) {
    const {
      labels      = [],
      datasets    = [],
      yLabel      = '',
      stacked     = true,
      optOverride = {},
    } = cfg;

    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return null;
    const ctx = canvas.getContext('2d');

    const options = deepMerge(CHART_DEFAULTS, {
      plugins: {
        tooltip: {
          callbacks: {
            label: (c) => ` ${c.dataset.label}: ${c.raw !== null ? c.raw : 0}${yLabel}`,
          },
        },
      },
      scales: {
        x: { stacked },
        y: { stacked, ticks: { callback: (v) => v + yLabel } },
      },
      ...optOverride,
    });

    return new Chart(ctx, { type: 'bar', data: { labels, datasets }, options });
  }

  // ── Trend chart: uptime % per day ─────────────────────────────────────
  function renderTrendChart(canvasId, trendData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    if (!trendData || trendData.length === 0) {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return null;
    }

    const labels = trendData.map((d) => {
      const dt = new Date(d.date + 'T00:00:00Z');
      return dt.toLocaleDateString('en-IN', {
        month: 'short', day: '2-digit', timeZone: 'UTC',
      });
    });
    const uptimes = trendData.map((d) => d.uptime_pct);
    const validUptimes = uptimes.filter((v) => v !== null && !isNaN(v));
    const minVal = validUptimes.length > 0 ? Math.min(...validUptimes) : 0;
    const yMin  = Math.max(0, Math.floor(minVal - 5));

    return createLineChart(canvasId, {
      labels,
      datasets: [{
        label: 'Uptime %',
        data:  uptimes,
        borderColor:     COLORS.indigo,
        backgroundColor: 'rgba(99, 102, 241, 0.09)',
        borderWidth:  2,
        tension:      0.4,
        pointRadius:  trendData.length === 1 ? 5 : 2,
        pointBackgroundColor: COLORS.indigo,
        pointHoverRadius: 5,
        fill: true,
        spanGaps: true,
      }],
      yLabel: '%',
      yMin,
      yMax: 100,
    });
  }

  // ── Success / Failure stacked bar chart ───────────────────────────────
  function renderSuccessFailChart(canvasId, trendData) {
    if (!trendData || trendData.length === 0) return null;

    const labels = trendData.map((d) => {
      const dt = new Date(d.date + 'T00:00:00Z');
      return dt.toLocaleDateString('en-IN', {
        month: 'short', day: '2-digit', timeZone: 'UTC',
      });
    });

    return createBarChart(canvasId, {
      labels,
      datasets: [
        {
          label: 'Success',
          data:  trendData.map((d) => d.success_checks || 0),
          backgroundColor: 'rgba(16, 185, 129, 0.65)',
          borderColor:     COLORS.emerald,
          borderWidth: 1,
          borderRadius: 3,
        },
        {
          label: 'Failures',
          data:  trendData.map((d) => d.failure_checks || 0),
          backgroundColor: 'rgba(244, 63, 94, 0.55)',
          borderColor:     COLORS.rose,
          borderWidth: 1,
          borderRadius: 3,
        },
      ],
      yLabel: '',
      stacked: true,
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────
  global.ChartHelpers = {
    COLORS,
    destroyIfExists,
    createLineChart,
    createBarChart,
    renderTrendChart,
    renderSuccessFailChart,
  };

})(window);
