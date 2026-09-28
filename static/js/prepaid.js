window.CCA = window.CCA || {};

(function () {
    const esc = (s) => String(s == null ? '--' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function renderPrepaid(dev) {
        const el = document.getElementById('prepaidCard');
        if (!el) return;
        if (!dev || !dev.online) {
            el.innerHTML = '<div class="loading-inline">No online device</div>';
            return;
        }
        const prep = (dev.metrics && dev.metrics.prepaid) || {};
        const countdown = prep.countdown && typeof prep.countdown === 'object' ? prep.countdown.human : esc(prep.countdown);

        el.innerHTML = `
            <div class="kv-grid">
                <div class="k">Prepaid Mode</div>
                <div class="v" style="color:${prep.switch_prepayment ? 'var(--accent-green)' : 'var(--text-secondary)'}">${prep.switch_prepayment ? 'ON' : 'OFF'}</div>
                <div class="k">Credit Balance</div>
                <div class="v">${esc(prep.balance_kwh)} <small style="color:var(--text-secondary)">kWh</small></div>
                <div class="k">Last Charge</div>
                <div class="v">${esc(prep.charge_kwh)} <small style="color:var(--text-secondary)">kWh</small></div>
                <div class="k">Countdown</div>
                <div class="v">${countdown}</div>
                <div class="k">Energy Reset</div>
                <div class="v">${esc(prep.energy_reset || 'empty')}</div>
            </div>
            <div style="margin-top:0.6rem;font-size:0.75rem;color:var(--text-secondary)">
                dd_raw: ${esc(prep.balance_kwh)} kWh &middot; charge_raw: ${esc(prep.charge_kwh)} kWh
                <br>Credit and charge units are divided by 100 per Tuya scale-2 spec.
            </div>
        `;
    }

    function renderAlarms(dev) {
        const el = document.getElementById('alarmCard');
        if (!el) return;
        if (!dev || !dev.online) {
            el.innerHTML = '<div class="loading-inline">No online device</div>';
            return;
        }
        const prep = (dev.metrics && dev.metrics.prepaid) || {};
        const set1 = prep.alarm_set_1 || { alarms: [] };
        const set2 = prep.alarm_set_2 || { alarms: [] };
        const fault = (dev.metrics && dev.metrics.fault) || {};

        const rows = [1, 2].map((n) => {
            const set = n === 1 ? set1 : set2;
            if (!set.alarms.length) return `<div class="loading-inline">No alarm data</div>`;
            return set.alarms.map(al => `
                <div class="alarm-row">
                    <div>
                        <span class="a-name">${esc(al.label)}</span>
                        <span class="a-tag ${al.trip ? 'trip' : 'alarm'}">${al.trip ? 'TRIP' : 'ALARM'}</span>
                        <div style="font-size:0.75rem;color:var(--text-secondary)">threshold raw: ${esc(al.threshold_raw)} &middot; presence 0x${al.presence.toString(16).padStart(2, '0')}</div>
                    </div>
                    <div class="raw-hex">${esc(set.raw_hex || '')}</div>
                </div>
            `).join('');
        });

        const faultChip = fault.active && fault.active.length
            ? fault.active.map(esc).join(', ')
            : 'No active faults';

        el.innerHTML = `
            <div style="margin-bottom:0.6rem;font-size:0.85rem">
                <span style="color:${fault.active && fault.active.length ? 'var(--accent-red)' : 'var(--accent-green)'}">${faultChip}</span>
                <div style="font-size:0.75rem;color:var(--text-secondary);margin-top:0.2rem">fault raw ${esc(fault.value)} (bitmap)</div>
            </div>
            <div style="display:grid;gap:0.5rem">
                <div>
                    <div class="metric-label">alarm_set_1</div>
                    ${rows[0]}
                </div>
                <div>
                    <div class="metric-label" style="margin-top:0.6rem">alarm_set_2</div>
                    ${rows[1]}
                </div>
                <div style="font-size:0.7rem;color:var(--text-secondary)">
                    Byte layout: [presence 1B][action 1B][threshold 2B BE] per alarm. Threshold units pending app-side verification.
                </div>
            </div>
        `;
    }

    CCA.renderPrepaid = renderPrepaid;
    CCA.renderAlarms = renderAlarms;
})();