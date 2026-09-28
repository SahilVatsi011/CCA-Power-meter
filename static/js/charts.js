window.CCA = window.CCA || {};

(function () {
    const MAX_LIVE = 360;
    let powerChart = null;
    let vcChart = null;
    let eventChart = null;
    let mode = '1h';

    const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || null;
    const gridStyle = { color: cssVar('--chart-grid') || 'rgba(51, 65, 85, 0.5)' };
    const tickStyle = { color: cssVar('--chart-tick') || '#94a3b8' };
    let legendColor = cssVar('--legend-color') || '#94a3b8';
    const timeFmt = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    const histTimeFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    const dayFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: '2-digit', hour12: false });
    const evTimeFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

    function labelFor(ms, range) {
        return range === 'live' || range === '1h' || range === '6h' || range === '24h'
            ? timeFmt.format(new Date(ms))
            : histTimeFmt.format(new Date(ms));
    }

    function applyTheme() {
        gridStyle.color = cssVar('--chart-grid') || 'rgba(51, 65, 85, 0.5)';
        tickStyle.color = cssVar('--chart-tick') || '#94a3b8';
        legendColor = cssVar('--legend-color') || '#94a3b8';
        [powerChart, vcChart, eventChart].forEach(ch => {
            if (!ch) return;
            ch.options.plugins.legend.labels.color = legendColor;
            ch.update('none');
        });
    }

    function initCharts() {
        if (typeof Chart === 'undefined') return;
        const commonOpts = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: { legend: { labels: { color: legendColor } } },
        };

        powerChart = new Chart(document.getElementById('powerChart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Power (W)',
                    data: [],
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 1,
                    borderWidth: 2,
                }]
            },
            options: {
                ...commonOpts,
                scales: {
                    x: { grid: gridStyle, ticks: { ...tickStyle, maxTicksLimit: 12 } },
                    y: { grid: gridStyle, ticks: tickStyle, beginAtZero: true, title: { display: true, text: 'Watts', color: '#94a3b8' } },
                },
            }
        });

        vcChart = new Chart(document.getElementById('vcChart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Voltage (V)', data: [], borderColor: '#eab308', backgroundColor: 'rgba(234,179,8,0.1)', fill: false, tension: 0.3, pointRadius: 1, borderWidth: 2, yAxisID: 'yV' },
                    { label: 'Current (A)', data: [], borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', fill: false, tension: 0.3, pointRadius: 1, borderWidth: 2, yAxisID: 'yA' }
                ]
            },
            options: {
                ...commonOpts,
                scales: {
                    x: { grid: gridStyle, ticks: { ...tickStyle, maxTicksLimit: 12 } },
                    yV: { type: 'linear', position: 'left', grid: gridStyle, ticks: tickStyle, title: { display: true, text: 'Volts', color: '#eab308' } },
                    yA: { type: 'linear', position: 'right', grid: { display: false }, ticks: tickStyle, title: { display: true, text: 'Amps', color: '#22c55e' } },
                },
            }
        });

        eventChart = new Chart(document.getElementById('eventChart'), {
            type: 'bar',
            data: {
                labels: [],
                datasets: [
                    { label: 'Power Cuts', data: [], backgroundColor: 'rgba(239,68,68,0.85)', borderRadius: 2 },
                    { label: 'Faults', data: [], backgroundColor: 'rgba(249,115,22,0.85)', borderRadius: 2 },
                    { label: 'Restores', data: [], backgroundColor: 'rgba(34,197,94,0.85)', borderRadius: 2 },
                ]
            },
            options: {
                ...commonOpts,
                scales: {
                    x: { grid: gridStyle, ticks: { ...tickStyle, maxTicksLimit: 8 } },
                    y: { grid: gridStyle, ticks: tickStyle, beginAtZero: true, title: { display: true, text: 'Events', color: '#94a3b8' } },
                },
            }
        });
    }

    const EVENT_META = {
        power_cut: ['Power Cut', 'cut'],
        power_restored: ['Power Restored', 'restore'],
        fault: ['Fault', 'fault'],
        fault_cleared: ['Fault Cleared', 'clear'],
    };

    function renderEventList(data) {
        const summary = document.getElementById('eventSummary');
        const list = document.getElementById('eventList');
        if (!summary || !list) return;
        const t = data.totals || {};
        const plc = t.cuts === 1 ? '' : 's';
        const plf = t.faults === 1 ? '' : 's';
        summary.innerHTML = `Last ${data.range_hours}h &mdash; <span class="ev-num cut">${t.cuts} power cut${plc}</span> &middot; <span class="ev-num fault">${t.faults} fault${plf}</span> &middot; <span class="ev-num restore">${t.restores} restore${t.restores === 1 ? '' : 's'}</span>`;
        if (!data.recent || !data.recent.length) {
            list.innerHTML = '<div class="loading-inline">No events recorded yet — events appear when the switch turns off/on or a fault triggers.</div>';
            return;
        }
        const sorted = data.recent.slice().sort((a, b) => b.ts_ms - a.ts_ms);
        list.innerHTML = sorted.map(ev => {
            const meta = EVENT_META[ev.event_type] || [ev.event_type || 'event', 'other'];
            const time = evTimeFmt.format(new Date(ev.ts_ms));
            return `<div class="event-item ${meta[1]}"><span class="ev-time">${time}</span><span class="ev-badge ${meta[1]}">${meta[0]}</span><span class="ev-detail">${window.CCA ? String(ev.detail || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) : ''}</span></div>`;
        }).join('');
    }

    async function loadEvents(window) {
        if (!eventChart) return;
        let resp;
        try {
            resp = await fetch('/api/events?window=' + encodeURIComponent(window));
        } catch {
            return;
        }
        const data = await resp.json();
        if (!data.success) return;
        const bucketFmt = data.bucket_sec === 3600 ? timeFmt : (data.bucket_sec === 21600 ? histTimeFmt : dayFmt);
        const labels = [], cuts = [], faults = [], restores = [];
        (data.series || []).forEach(b => {
            labels.push(bucketFmt.format(new Date(b.ts_ms)));
            cuts.push(b.cuts);
            faults.push(b.faults);
            restores.push(b.restores);
        });
        eventChart.data.labels = labels;
        eventChart.data.datasets[0].data = cuts;
        eventChart.data.datasets[1].data = faults;
        eventChart.data.datasets[2].data = restores;
        eventChart.update('none');
        renderEventList(data);
    }

    function appendLive(voltage, current, power) {
        if (mode !== 'live' || !powerChart || !vcChart) return;
        const label = timeFmt.format(new Date());
        powerChart.data.labels.push(label);
        powerChart.data.datasets[0].data.push(power);
        if (powerChart.data.labels.length > MAX_LIVE) {
            powerChart.data.labels.shift();
            powerChart.data.datasets[0].data.shift();
        }
        powerChart.update('none');

        vcChart.data.labels.push(label);
        vcChart.data.datasets[0].data.push(voltage);
        vcChart.data.datasets[1].data.push(current);
        if (vcChart.data.labels.length > MAX_LIVE) {
            vcChart.data.labels.shift();
            vcChart.data.datasets[0].data.shift();
            vcChart.data.datasets[1].data.shift();
        }
        vcChart.update('none');
    }

    function applySeries(data, rangeKey) {
        if (!data.success) return;
        const devices = data.devices || {};
        const did = Object.keys(devices)[0];
        const labels = [], v = [], a = [], w = [];
        if (did) {
            const s = devices[did];
            s.labels.forEach((ms, i) => {
                labels.push(labelFor(ms, rangeKey));
                v.push(s.v[i]);
                a.push(s.a[i]);
                w.push(s.w[i]);
            });
        }
        powerChart.data.labels = labels;
        powerChart.data.datasets[0].data = w;
        powerChart.update('none');
        vcChart.data.labels = labels;
        vcChart.data.datasets[0].data = v;
        vcChart.data.datasets[1].data = a;
        vcChart.update('none');
        CCA.historyDeviceId = did;
    }

    async function loadHistory(range) {
        if (!powerChart || !vcChart) return;
        mode = range;
        let resp;
        try {
            resp = await fetch('/api/history?range=' + encodeURIComponent(range));
        } catch {
            return;
        }
        const data = await resp.json();
        if (!data.success) return;
        applySeries(data, range);
    }

    async function loadCustomRange(fromSec, toSec) {
        if (!powerChart || !vcChart) return;
        mode = 'custom';
        let resp;
        try {
            resp = await fetch(`/api/history?from=${fromSec}&to=${toSec}`);
        } catch {
            return;
        }
        const data = await resp.json();
        if (!data.success) return;
        const hours = (toSec - fromSec) / 3600;
        const rangeKey = hours <= 2 ? '1h' : hours <= 48 ? '24h' : '7d';
        applySeries(data, rangeKey);
    }

    function setLive() {
        mode = 'live';
        resetLiveCharts();
    }

    function resetLiveCharts() {
        if (powerChart) { powerChart.data.labels = []; powerChart.data.datasets[0].data = []; powerChart.update('none'); }
        if (vcChart) { vcChart.data.labels = []; vcChart.data.datasets[0].data = []; vcChart.data.datasets[1].data = []; vcChart.update('none'); }
    }

    CCA.initCharts = initCharts;
    CCA.appendLive = appendLive;
    CCA.loadHistory = loadHistory;
    CCA.loadCustomRange = loadCustomRange;
    CCA.loadEvents = loadEvents;
    CCA.setLive = setLive;
    CCA.applyTheme = applyTheme;
})();