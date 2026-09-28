window.CCA = window.CCA || {};

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '--' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function setStatus(cls, text) {
        const badge = $('statusBadge');
        badge.className = 'status-badge ' + cls;
        $('statusText').textContent = text;
    }

    function addLogRow(voltage, current, power, energy, temp, pf) {
        const tbody = $('logBody');
        const placeholder = tbody.querySelector('td[colspan]');
        if (placeholder) placeholder.parentElement.remove();
        const time = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        const row = document.createElement('tr');
        row.innerHTML = `<td>${time}</td><td>${voltage}</td><td>${current}</td><td>${power}</td><td>${energy}</td><td>${temp}</td><td>${pf}</td>`;
        tbody.insertBefore(row, tbody.firstChild);
        while (tbody.children.length > 10) tbody.removeChild(tbody.lastChild);
    }

    function pickPrimary(devices) {
        if (!Array.isArray(devices) || !devices.length) return null;
        return devices.find(d => d.online && d.metrics && d.metrics.phase_a) || null;
    }

    async function fetchData() {
        let data;
        try {
            const resp = await fetch('/api/all-status');
            data = await resp.json();
        } catch (err) {
            setStatus('error', 'Error');
            return;
        }

        if (!data.success) {
            setStatus('warn', 'API error');
            return;
        }

        const devices = data.devices || [];
        const primary = pickPrimary(devices);
        const anyOnline = devices.some(d => d.online);
        setStatus(anyOnline ? '' : 'warn', anyOnline ? 'Connected' : 'All offline');
        $('lastUpdated').textContent = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

        const pa = primary && primary.metrics.phase_a;
        let voltage = '--', current = '--', power = '--', energy = '--', temp = '--', pf = '--';
        if (pa) {
            voltage = pa.voltage;
            current = pa.current;
            power = pa.power;
            pf = (voltage * current > 0 && power) ? (power / (voltage * current)).toFixed(2) : '--';
            $('sumPower').innerHTML = `${power}<span class="unit">W</span>`;
            $('sumVoltage').innerHTML = `${voltage}<span class="unit">V</span>`;
            $('sumCurrent').innerHTML = `${current}<span class="unit">A</span>`;
        }
        $('sumPF').innerHTML = pf === '--' ? '--' : `${pf}<span class="unit">${voltage && current ? '' : ''}</span>`;

        const m = primary ? primary.metrics : {};
        if (m.total_energy) {
            energy = m.total_energy.value;
            $('sumEnergy').innerHTML = `${energy}<span class="unit">kWh</span>`;
        }
        if (m.temperature) {
            temp = m.temperature.value;
            $('sumTemp').innerHTML = `${temp}<span class="unit">°C</span>`;
        }
        if (m.fault) {
            const active = m.fault.active || [];
            $('sumFault').textContent = active.length ? active.join(', ') : (m.fault.value ? 'Fault ' + m.fault.value : 'No faults');
            $('sumFault').style.color = (active.length || m.fault.value) ? 'var(--accent-red)' : 'var(--text-secondary)';
        }

        if (CCA.appendLive) CCA.appendLive(voltage, current, power);

        if (pa) {
            $('sumVoltageSub').textContent = 'Phase A';
            $('sumCurrentSub').textContent = 'Phase A';
        }

        const sw = primary ? primary.metrics.switch : null;
        const sb = $('switchBadge');
        if (sw && sw.value != null) {
            const on = !!sw.value;
            sb.hidden = false;
            sb.className = 'switch-badge ' + (on ? 'on' : 'off');
            $('switchText').textContent = on ? 'Switch: ON' : 'Switch: OFF';
        } else {
            sb.hidden = true;
        }

        CCA.renderPrepaid(primary);
        CCA.renderAlarms(primary);

        addLogRow(voltage, current, power, energy, temp, pf);
    }

    function fmtBytes(n) {
        if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
        if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
        return n + ' B';
    }

    async function loadDbStats() {
        const badge = $('dbBadge');
        let data;
        try {
            const resp = await fetch('/api/db-stats');
            data = await resp.json();
        } catch {
            badge.className = 'db-badge err';
            $('dbText').textContent = 'DB: unreachable';
            return;
        }
        if (!data.success || !data.connected) {
            badge.className = 'db-badge err';
            $('dbText').textContent = 'DB: not connected';
            return;
        }
        const total = Object.values(data.collections || {}).reduce((s, c) => s + (c.count || 0), 0);
        badge.className = 'db-badge ' + (data.used_percent > 80 ? 'warn' : 'ok');
        $('dbText').textContent =
            `DB: ${fmtBytes(data.used_bytes)} / ${fmtBytes(data.limit_bytes)} · ${total.toLocaleString()} readings`;

        let strip = document.getElementById('storageStrip');
        if (!strip) {
            strip = document.createElement('div');
            strip.id = 'storageStrip';
            strip.className = 'storage-strip';
            const anchor = document.querySelector('.chart-section');
            document.querySelector('.container').insertBefore(strip, anchor);
        }
        strip.innerHTML = `
            <span>MongoDB Atlas · ${esc(data.database)}</span>
            <div class="storage-bar"><div style="width:${Math.min(data.used_percent, 100)}%"></div></div>
            <span>${data.used_percent}% of free tier · raw ${data.ttl_raw_days}d / 1-min ${data.ttl_1min_days}d</span>
        `;
    }

    function bindRangeButtons() {
        document.querySelectorAll('.controls .btn[data-range]').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.controls .btn[data-range]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const range = btn.dataset.range;
                if (range === 'live') {
                    CCA.setLive();
                } else if (CCA.loadHistory) {
                    CCA.loadHistory(range);
                }
            });
        });
    }

    function setLocalInput(did, tid, d) {
        const p = (n) => String(n).padStart(2, '0');
        $(did).value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
        $(tid).value = `${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    function readLocalInput(did, tid) {
        const datePart = $(did).value;
        if (!datePart) return NaN;
        const timePart = $(tid).value || '00:00';
        return new Date(`${datePart}T${timePart}:00`).getTime();
    }

    function initExport() {
        const modal = $('exportModal');
        $('openExportBtn').addEventListener('click', () => {
            const to = new Date();
            const from = new Date(to.getTime() - 86400000);
            setLocalInput('expToDate', 'expToTime', to);
            setLocalInput('expFromDate', 'expFromTime', from);
            $('exportStatus').textContent = '';
            $('exportStatus').className = 'export-status';
            modal.hidden = false;
        });
        modal.querySelectorAll('[data-close-export]').forEach(el =>
            el.addEventListener('click', () => { modal.hidden = true; })
        );

        $('doExport').addEventListener('click', async () => {
            const status = $('exportStatus');
            const fromMs = readLocalInput('expFromDate', 'expFromTime');
            const toMs = readLocalInput('expToDate', 'expToTime');
            if (isNaN(fromMs) || isNaN(toMs)) {
                status.textContent = 'Please pick both From and To dates.';
                status.className = 'export-status err';
                return;
            }
            const from = Math.round(fromMs / 1000);
            const to = Math.round(toMs / 1000);
            if (to <= from) {
                status.textContent = 'To time must be after From time.';
                status.className = 'export-status err';
                return;
            }
            const fields = Array.from(document.querySelectorAll('.exp-field:checked')).map(c => c.value);
            if (!fields.length) {
                status.textContent = 'Pick at least one field.';
                status.className = 'export-status err';
                return;
            }
            const format = $('expFormat').value;
            const interval = $('expInterval').value;
            let qs = new URLSearchParams({
                format, interval,
                from: String(from), to: String(to),
                fields: fields.join(','),
            });
            const pass = await askPasscode();
            if (!pass) return;
            qs.set('pass', pass);
            status.textContent = 'Checking password...';
            status.className = 'export-status';
            try {
                const resp = await fetch('/api/export?' + qs.toString());
                if (!resp.ok) {
                    let msg = 'Export failed (server error).';
                    try {
                        const err = await resp.json();
                        if (err && err.error) msg = err.error;
                    } catch { }
                    status.textContent = msg;
                    status.className = 'export-status err';
                    return;
                }
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `readings_${interval}.${format === 'xlsx' ? 'xlsx' : 'csv'}`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
                status.textContent = `Downloaded: ${fields.length} fields, ${interval} interval.`;
                modal.hidden = true;
            } catch {
                status.textContent = 'Export failed (network error).';
                status.className = 'export-status err';
            }
        });
    }

    function initCustomRange() {
        const bar = $('customRangeBar');
        $('openCustomBtn').addEventListener('click', () => {
            const to = new Date();
            const from = new Date(to.getTime() - 86400000);
            setLocalInput('cFromDate', 'cFromTime', from);
            setLocalInput('cToDate', 'cToTime', to);
            bar.hidden = !bar.hidden;
        });
        $('closeCustom').addEventListener('click', () => { bar.hidden = true; });
        $('applyCustom').addEventListener('click', () => {
            const fromMs = readLocalInput('cFromDate', 'cFromTime');
            const toMs = readLocalInput('cToDate', 'cToTime');
            if (isNaN(fromMs) || isNaN(toMs)) return;
            const from = Math.round(fromMs / 1000);
            const to = Math.round(toMs / 1000);
            if (to <= from) return;
            document.querySelectorAll('.controls .btn[data-range]').forEach(b => b.classList.remove('active'));
            bar.hidden = true;
            if (CCA.loadCustomRange) CCA.loadCustomRange(from, to);
        });
    }

    function initEvents() {
        document.querySelectorAll('.ev-window').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.ev-window').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (CCA.loadEvents) CCA.loadEvents(btn.dataset.ewin);
            });
        });
        $('exportEventsBtn').addEventListener('click', async () => {
            const active = document.querySelector('.ev-window.active');
            const win = active ? active.dataset.ewin : '24h';
            const hours = { '24h': 24, '7d': 168, '30d': 720 }[win] || 24;
            const to = Math.round(Date.now() / 1000);
            const from = to - hours * 3600;
            const fmt = $('evFormat').value;
            const status = $('exportStatus');
            const pass = await askPasscode();
            if (!pass) return;
            if (status) { status.textContent = 'Checking password...'; status.className = 'export-status'; }
            try {
                const resp = await fetch(`/api/export-events?from=${from}&to=${to}&format=${fmt}&pass=${encodeURIComponent(pass)}`);
                if (!resp.ok) {
                    let msg = 'Export failed.';
                    try {
                        const err = await resp.json();
                        if (err && err.error) msg = err.error;
                    } catch { }
                    if (status) { status.textContent = msg; status.className = 'export-status err'; }
                    return;
                }
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `events_${win}.${fmt === 'xlsx' ? 'xlsx' : 'csv'}`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            } catch {
                if (status) { status.textContent = 'Export failed (network).'; status.className = 'export-status err'; }
            }
        });
    }

    function initTheme() {
        const root = document.documentElement;
        const saved = localStorage.getItem('cca-theme');
        const theme = (saved === 'dark' || saved === 'light') ? saved : 'light';
        root.dataset.theme = theme;
        updateToggleLabel(theme);
        $('themeToggle').addEventListener('click', () => {
            const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
            root.dataset.theme = next;
            localStorage.setItem('cca-theme', next);
            updateToggleLabel(next);
            if (CCA.applyTheme) CCA.applyTheme();
        });
    }

    function updateToggleLabel(theme) {
        $('themeToggle').textContent = theme === 'dark' ? 'Dark' : 'Light';
    }

    function askPasscode() {
        return new Promise((resolve) => {
            const modal = $('passcodeModal');
            const input = $('passInput');
            const status = $('passStatus');
            input.value = '';
            status.textContent = '';
            status.className = 'export-status';
            modal.hidden = false;
            let done = false;
            const finish = (val) => {
                if (done) return;
                done = true;
                modal.hidden = true;
                resolve(val);
            };
            modal.querySelectorAll('[data-close-pass]').forEach(el =>
                el.addEventListener('click', () => finish(null), { once: true })
            );
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    const v = input.value.trim();
                    if (!v) { status.textContent = 'Enter a password.'; status.className = 'export-status err'; return; }
                    $('passOk').click();
                }
            });
            $('passOk').addEventListener('click', () => {
                const v = input.value.trim();
                if (!v) { status.textContent = 'Enter a password.'; status.className = 'export-status err'; return; }
                finish(v);
            }, { once: true });
            setTimeout(() => input.focus(), 50);
        });
    }

    function initConsumption() {
        const unitFmt = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 });
        const moneyFmt = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const whenFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

        async function loadConsumption(fromSec, toSec) {
            document.querySelectorAll('.cons-range').forEach(b => b.classList.remove('active'));
            $('consUnits').textContent = 'Calculating...';
            $('consCost').textContent = '--';
            try {
                const resp = await fetch(`/api/consumption?from=${fromSec}&to=${toSec}`);
                const data = await resp.json();
                if (!data.success) {
                    $('consUnits').textContent = 'Error';
                    return;
                }
                const kwh = data.total_kwh;
                const rate = parseFloat($('calcRate').value) || 6.39;
                $('consUnits').textContent = `${unitFmt.format(kwh)} units (kWh)`;
                $('consFromLabel').textContent = whenFmt.format(new Date(fromSec * 1000));
                $('consToLabel').textContent = whenFmt.format(new Date(toSec * 1000));
                $('consCost').textContent = `₹ ${moneyFmt.format(kwh * rate)}`;
                $('calcUnits').value = kwh;
            } catch {
                $('consUnits').textContent = 'Failed';
            }
        }

        document.querySelectorAll('.cons-range').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.cons-range').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const hours = parseFloat(btn.dataset.conh) || 24;
                const to = Math.round(Date.now() / 1000);
                loadConsumption(to - hours * 3600, to);
            });
        });

        $('consCustomBtn').addEventListener('click', () => {
            const to = new Date();
            const from = new Date(to.getTime() - 86400000);
            setLocalInput('cFromDate2', 'cFromTime2', from);
            setLocalInput('cToDate2', 'cToTime2', to);
            $('consRangeBar').hidden = !$('consRangeBar').hidden;
        });
        $('closeConsCustom').addEventListener('click', () => { $('consRangeBar').hidden = true; });
        $('applyConsCustom').addEventListener('click', () => {
            const fromMs = readLocalInput('cFromDate2', 'cFromTime2');
            const toMs = readLocalInput('cToDate2', 'cToTime2');
            if (isNaN(fromMs) || isNaN(toMs)) return;
            if (toMs <= fromMs) return;
            $('consRangeBar').hidden = true;
            loadConsumption(Math.round(fromMs / 1000), Math.round(toMs / 1000));
        });

        const calcBtn = $('calcBtn');
        calcBtn.addEventListener('click', () => {
            const units = parseFloat($('calcUnits').value);
            const rate = parseFloat($('calcRate').value);
            if (isNaN(units) || units < 0) { $('calcResult').textContent = 'Enter valid units.'; return; }
            if (isNaN(rate) || rate < 0) { $('calcResult').textContent = 'Enter valid rate.'; return; }
            const price = units * rate;
            $('calcResult').innerHTML = `${unitFmt.format(units)} unit${units === 1 ? '' : 's'} &times; &#8377;${moneyFmt.format(rate)}/unit = <strong>&#8377; ${moneyFmt.format(price)}</strong>`;
        });
        $('calcRate').addEventListener('change', () => {
            const active = document.querySelector('.cons-range.active');
            if (active) active.click();
        });

        const defRange = document.querySelector('.cons-range.active');
        if (defRange) defRange.click();
    }

    function init() {
        if (typeof Chart === 'undefined') {
            document.querySelectorAll('.chart-section').forEach(el => {
                el.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary)">Chart.js failed to load</div>';
            });
        } else if (CCA.initCharts) {
            CCA.initCharts();
        }
        initTheme();
        bindRangeButtons();
        initExport();
        initCustomRange();
        initEvents();
        initConsumption();
        fetchData();
        loadDbStats();
        if (CCA.loadHistory) CCA.loadHistory('1h');
        if (CCA.loadEvents) CCA.loadEvents('24h');
        setInterval(fetchData, 10000);
        setInterval(loadDbStats, 30000);
        setInterval(() => { if (CCA.loadEvents) CCA.loadEvents(document.querySelector('.ev-window.active').dataset.ewin); }, 30000);
    }

    CCA.init = init;
})();

window.addEventListener('DOMContentLoaded', () => {
    try {
        if (CCA.init) CCA.init();
    } catch (err) {
        const b = document.getElementById('jsErrorBanner');
        if (b) { b.textContent = '⛔ init failed: ' + (err && err.message || err); b.hidden = false; }
        console.error(err);
    }
});