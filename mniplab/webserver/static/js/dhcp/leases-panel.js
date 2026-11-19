// DHCP Service panel — the modal a Learner opens from a Node's DHCP badge to
// configure the Address Pool, watch the Service's Lease table
// live, and tell relayed Leases from direct ones.
//
// Leases are the server's own record, not Interface state (see
// mniplab/dhcp/leases.py), so "Applied" here is a client-side comparison
// against the current Topology's node IPs — a Lease whose address matches no
// Node is one the server holds but no Interface has applied yet.

import { appState } from '../state/app-state.js';
import { getLeases, getPools, updatePool } from '../services/api.js';
import { patchTableRows } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

// Leases are a server record with no push notification (Kea's control
// channel is poll-only, as KeaControlClient reflects); this interval is
// how "updates without a page reload" is satisfied, matching the interval
// already used for the instructor Sessions/Terminals panels.
const POLL_INTERVAL_MS = 3000;

let currentNode = null;
let pollTimer = null;
let trap = null;

export function initLeasesPanel() {
    const modal = document.getElementById('dhcp-leases-modal');
    const closeBtn = document.getElementById('dhcp-leases-modal-close');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', closeLeasesPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeLeasesPanel();
    });
}

export function openLeasesPanel(nodeName) {
    const modal = document.getElementById('dhcp-leases-modal');
    const title = document.getElementById('dhcp-leases-modal-title');
    if (!modal) return;

    currentNode = nodeName;
    if (title) title.textContent = `${nodeName} — DHCP Service`;
    setStatus('Loading Leases…', false);
    clearRows();
    clearPools();

    modal.style.display = 'flex';
    trap = trapFocus(modal, { onClose: closeLeasesPanel });

    void refreshPools();
    void refreshLeases();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshLeases(); }, POLL_INTERVAL_MS);
}

export function closeLeasesPanel() {
    const modal = document.getElementById('dhcp-leases-modal');
    if (modal) modal.style.display = 'none';
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    currentNode = null;
    if (trap) {
        trap.release();
        trap = null;
    }
    clearPools();
}

async function refreshPools() {
    const node = currentNode;
    if (!node) return;

    const container = document.getElementById('dhcp-pools-container');
    if (!container) return;

    let response;
    try {
        response = await getPools(node);
    } catch (error) {
        if (node === currentNode) {
            renderPoolsError(`Can't load pool configuration: ${error.message}`);
        }
        return;
    }

    if (node !== currentNode) return;

    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }

    if (!response.ok) {
        const detail = (data && data.detail) || `Request failed (${response.status})`;
        renderPoolsError(detail);
        return;
    }

    const pools = (data && data.pools) || [];
    renderPools(pools);
}

function renderPools(pools) {
    const container = document.getElementById('dhcp-pools-container');
    if (!container) return;

    container.innerHTML = '';
    if (!pools || pools.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'dhcp-pool-empty';
        empty.textContent = 'No DHCP pools configured for this Service.';
        container.appendChild(empty);
        return;
    }

    pools.forEach((pool, index) => {
        const card = document.createElement('div');
        card.className = 'dhcp-pool-card';

        const form = document.createElement('form');
        form.className = 'dhcp-pool-form';
        form.dataset.cidr = pool.cidr;

        // Family-dependent fields: router is IPv4-only, preferred lifetime is IPv6-only.
        const isV6 = pool.family === 6 || (pool.cidr && pool.cidr.includes(':'));

        const header = document.createElement('div');
        header.className = 'dhcp-pool-header';

        const labelSpan = document.createElement('span');
        labelSpan.className = 'dhcp-pool-subnet-label';
        labelSpan.textContent = 'Subnet:';

        const valueSpan = document.createElement('span');
        valueSpan.className = 'dhcp-pool-subnet-value';
        valueSpan.textContent = pool.cidr;

        const badge = document.createElement('span');
        badge.className = 'dhcp-pool-readonly-badge';
        badge.textContent = '(read-only)';

        header.appendChild(labelSpan);
        header.appendChild(valueSpan);
        header.appendChild(badge);

        if (pool.interface) {
            const ifaceLabel = document.createElement('span');
            ifaceLabel.className = 'dhcp-pool-interface-label';
            ifaceLabel.textContent = 'Interface:';

            const ifaceValue = document.createElement('span');
            ifaceValue.className = 'dhcp-pool-interface-value';
            ifaceValue.textContent = pool.interface;

            const ifaceBadge = document.createElement('span');
            ifaceBadge.className = 'dhcp-pool-readonly-badge';
            ifaceBadge.textContent = '(read-only)';

            header.appendChild(ifaceLabel);
            header.appendChild(ifaceValue);
            header.appendChild(ifaceBadge);
        }

        const fields = document.createElement('div');
        fields.className = 'dhcp-pool-fields';

        // Start address input group
        const startGroup = document.createElement('div');
        startGroup.className = 'dhcp-form-group';
        const startLabel = document.createElement('label');
        const startId = `dhcp-pool-start-${index}`;
        startLabel.htmlFor = startId;
        startLabel.textContent = 'Start address';
        const startInput = document.createElement('input');
        startInput.type = 'text';
        startInput.id = startId;
        startInput.name = 'start';
        startInput.value = pool.start || '';
        startInput.autocomplete = 'off';
        startInput.spellcheck = false;
        startInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);

        startGroup.appendChild(startLabel);
        startGroup.appendChild(startInput);

        // End address input group
        const endGroup = document.createElement('div');
        endGroup.className = 'dhcp-form-group';
        const endLabel = document.createElement('label');
        const endId = `dhcp-pool-end-${index}`;
        endLabel.htmlFor = endId;
        endLabel.textContent = 'End address';
        const endInput = document.createElement('input');
        endInput.type = 'text';
        endInput.id = endId;
        endInput.name = 'end';
        endInput.value = pool.end || '';
        endInput.autocomplete = 'off';
        endInput.spellcheck = false;
        endInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);

        endGroup.appendChild(endLabel);
        endGroup.appendChild(endInput);

        // Lease time input group: seconds until a Lease must renew.
        const leaseTimeGroup = document.createElement('div');
        leaseTimeGroup.className = 'dhcp-form-group';
        const leaseTimeLabel = document.createElement('label');
        const leaseTimeId = `dhcp-pool-lease-time-${index}`;
        leaseTimeLabel.htmlFor = leaseTimeId;
        leaseTimeLabel.textContent = 'Lease time (seconds)';
        const leaseTimeInput = document.createElement('input');
        leaseTimeInput.type = 'number';
        leaseTimeInput.id = leaseTimeId;
        leaseTimeInput.name = 'lease_time';
        leaseTimeInput.min = '1';
        leaseTimeInput.value = pool.lease_time != null ? String(pool.lease_time) : '';
        leaseTimeInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);
        leaseTimeGroup.appendChild(leaseTimeLabel);
        leaseTimeGroup.appendChild(leaseTimeInput);

        // Router option input group — IPv4 only.  DHCPv6 has no router option:
        // the default gateway comes from the Router Advertisement.  Hiding beats
        // disabling: a field a Learner cannot use is noise under time pressure.
        let routerInput = null;
        let routerGroup = null;
        if (!isV6) {
            routerGroup = document.createElement('div');
            routerGroup.className = 'dhcp-form-group';
            const routerLabel = document.createElement('label');
            const routerId = `dhcp-pool-router-${index}`;
            routerLabel.htmlFor = routerId;
            routerLabel.textContent = 'Router';
            routerInput = document.createElement('input');
            routerInput.type = 'text';
            routerInput.id = routerId;
            routerInput.name = 'router';
            routerInput.value = pool.router || '';
            routerInput.autocomplete = 'off';
            routerInput.spellcheck = false;
            routerInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);
            routerGroup.appendChild(routerLabel);
            routerGroup.appendChild(routerInput);
        }

        // Preferred lifetime — IPv6 only.  The gap between preferred and valid
        // is observable inside a class period and has no IPv4 analogue.
        let preferredLifetimeInput = null;
        let prefGroup = null;
        if (isV6) {
            prefGroup = document.createElement('div');
            prefGroup.className = 'dhcp-form-group';
            const prefLabel = document.createElement('label');
            const prefId = `dhcp-pool-preferred-lifetime-${index}`;
            prefLabel.htmlFor = prefId;
            prefLabel.textContent = 'Preferred lifetime (seconds)';
            preferredLifetimeInput = document.createElement('input');
            preferredLifetimeInput.type = 'number';
            preferredLifetimeInput.id = prefId;
            preferredLifetimeInput.name = 'preferred_lifetime';
            preferredLifetimeInput.min = '1';
            preferredLifetimeInput.value = pool.preferred_lifetime != null ? String(pool.preferred_lifetime) : '';
            preferredLifetimeInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);
            prefGroup.appendChild(prefLabel);
            prefGroup.appendChild(preferredLifetimeInput);
        }

        // DNS server option input group
        const dnsServerGroup = document.createElement('div');
        dnsServerGroup.className = 'dhcp-form-group';
        const dnsServerLabel = document.createElement('label');
        const dnsServerId = `dhcp-pool-dns-server-${index}`;
        dnsServerLabel.htmlFor = dnsServerId;
        dnsServerLabel.textContent = 'DNS server';
        const dnsServerInput = document.createElement('input');
        dnsServerInput.type = 'text';
        dnsServerInput.id = dnsServerId;
        dnsServerInput.name = 'dns_server';
        dnsServerInput.value = pool.dns_server || '';
        dnsServerInput.autocomplete = 'off';
        dnsServerInput.spellcheck = false;
        dnsServerInput.setAttribute('aria-describedby', `dhcp-pool-feedback-${index}`);
        dnsServerGroup.appendChild(dnsServerLabel);
        dnsServerGroup.appendChild(dnsServerInput);

        // Submit button
        const actions = document.createElement('div');
        actions.className = 'dhcp-pool-actions';
        const submitBtn = document.createElement('button');
        submitBtn.type = 'submit';
        submitBtn.className = 'dhcp-pool-submit-btn';
        submitBtn.textContent = 'Apply';
        actions.appendChild(submitBtn);

        fields.appendChild(startGroup);
        fields.appendChild(endGroup);
        fields.appendChild(leaseTimeGroup);
        if (routerGroup) {
            fields.appendChild(routerGroup);
        }
        if (prefGroup) {
            fields.appendChild(prefGroup);
        }
        fields.appendChild(dnsServerGroup);
        fields.appendChild(actions);

        // Reservations: a repeatable list of clients pinned to a fixed address,
        // the exception to this Pool rather than a Lease drawn from it.
        const reservationsFieldset = document.createElement('fieldset');
        reservationsFieldset.className = 'dhcp-reservations-fieldset';
        const reservationsLegend = document.createElement('legend');
        reservationsLegend.textContent = 'Reservations';
        reservationsFieldset.appendChild(reservationsLegend);

        const reservationsList = document.createElement('div');
        reservationsList.className = 'dhcp-reservation-list';
        reservationsList.dataset.poolIndex = String(index);
        reservationsList.dataset.nextRow = '0';
        reservationsList.dataset.family = isV6 ? '6' : '4';
        renderReservationRows(reservationsList, pool.reservations || []);
        reservationsFieldset.appendChild(reservationsList);

        const addReservationBtn = document.createElement('button');
        addReservationBtn.type = 'button';
        addReservationBtn.className = 'dhcp-reservation-add-btn';
        addReservationBtn.textContent = 'Add reservation';
        addReservationBtn.addEventListener('click', () => {
            reservationsList.appendChild(buildReservationRow(reservationsList, null));
        });
        reservationsFieldset.appendChild(addReservationBtn);

        // Feedback / Error container (announced via role="alert" and aria-live="polite")
        const feedback = document.createElement('div');
        feedback.id = `dhcp-pool-feedback-${index}`;
        feedback.className = 'dhcp-pool-feedback';
        feedback.setAttribute('role', 'alert');
        feedback.setAttribute('aria-live', 'polite');

        form.appendChild(header);
        form.appendChild(fields);
        form.appendChild(reservationsFieldset);
        form.appendChild(feedback);

        form.addEventListener('submit', (e) => {
            e.preventDefault();
            void handlePoolSubmit(form, pool.cidr, submitBtn, feedback);
        });

        card.appendChild(form);
        container.appendChild(card);
    });
}

/** Clear and rebuild a pool's Reservation rows, e.g. from a fresh fetch or a broadcast. */
function renderReservationRows(listEl, reservations) {
    listEl.innerHTML = '';
    (reservations || []).forEach((reservation) => {
        listEl.appendChild(buildReservationRow(listEl, reservation));
    });
}

/** The labelled hardware-address group, shared by the IPv4 row and the
 * IPv6 row's "Hardware address" branch. */
function buildMacGroup(poolIndex, rowId, value) {
    const macGroup = document.createElement('div');
    macGroup.className = 'dhcp-form-group';
    const macLabel = document.createElement('label');
    const macId = `dhcp-reservation-mac-${poolIndex}-${rowId}`;
    macLabel.htmlFor = macId;
    macLabel.textContent = 'MAC address';
    const macInput = document.createElement('input');
    macInput.type = 'text';
    macInput.id = macId;
    macInput.name = 'reservation-mac';
    macInput.value = value || '';
    macInput.autocomplete = 'off';
    macInput.spellcheck = false;
    macGroup.appendChild(macLabel);
    macGroup.appendChild(macInput);
    return macGroup;
}

/** Build one Reservation row: client identifier, reserved address, optional
 * hostname, and a Remove button. The row's family comes from its list, so
 * callers never thread it separately.
 *
 * The identifier field is family-shaped: an IPv4 row carries a
 * MAC address, because DHCPv4's chaddr is the only identity the protocol
 * has; an IPv6 row carries a DUID *or* a hardware address — exactly one —
 * because DHCPv6 replaces chaddr with the Client Identifier option's DUID.
 * The DUID is the baseline (a Learner reads it out of the Lease table); the
 * hardware-address branch exists for the relayed client and is the
 * convenience for a directly attached one. A select switches which field
 * the row submits, and switching clears both so an ambiguous Reservation
 * can never be gathered.
 */
function buildReservationRow(listEl, reservation) {
    const isV6 = listEl.dataset.family === '6';
    const poolIndex = listEl.dataset.poolIndex;
    const rowId = Number(listEl.dataset.nextRow || '0');
    listEl.dataset.nextRow = String(rowId + 1);

    const row = document.createElement('div');
    row.className = 'dhcp-reservation-row';

    let macInput = null;
    let duidInput = null;

    if (isV6) {
        const idTypeGroup = document.createElement('div');
        idTypeGroup.className = 'dhcp-form-group';
        const idTypeLabel = document.createElement('label');
        const idTypeId = `dhcp-reservation-id-type-${poolIndex}-${rowId}`;
        idTypeLabel.htmlFor = idTypeId;
        idTypeLabel.textContent = 'Identify client by';
        const idTypeSelect = document.createElement('select');
        idTypeSelect.id = idTypeId;
        idTypeSelect.name = 'reservation-id-type';
        for (const [value, label] of [['duid', 'DUID'], ['mac', 'Hardware address']]) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            idTypeSelect.appendChild(option);
        }
        idTypeGroup.appendChild(idTypeLabel);
        idTypeGroup.appendChild(idTypeSelect);

        const duidGroup = document.createElement('div');
        duidGroup.className = 'dhcp-form-group';
        const duidLabel = document.createElement('label');
        const duidId = `dhcp-reservation-duid-${poolIndex}-${rowId}`;
        duidLabel.htmlFor = duidId;
        duidLabel.textContent = 'DUID';
        duidInput = document.createElement('input');
        duidInput.type = 'text';
        duidInput.id = duidId;
        duidInput.name = 'reservation-duid';
        duidInput.autocomplete = 'off';
        duidInput.spellcheck = false;
        duidInput.placeholder = '00:01:00:01:…';
        duidGroup.appendChild(duidLabel);
        duidGroup.appendChild(duidInput);

        const macGroup = buildMacGroup(poolIndex, rowId, '');
        macInput = macGroup.querySelector('input');

        const applyIdType = () => {
            const byMac = idTypeSelect.value === 'mac';
            duidGroup.hidden = byMac;
            macGroup.hidden = !byMac;
        };
        idTypeSelect.addEventListener('change', () => {
            // Clearing both fields keeps the gathered Reservation
            // unambiguous; say so when something typed was discarded.
            if (duidInput.value || macInput.value) {
                announce('Identifier switched; the value you typed was cleared.');
            }
            duidInput.value = '';
            macInput.value = '';
            applyIdType();
        });

        // An existing Reservation renders by the identifier it carries; a
        // new blank row defaults to the DUID, the IPv6 baseline.
        if (reservation && reservation.mac) {
            idTypeSelect.value = 'mac';
            macInput.value = reservation.mac;
        } else {
            if (reservation && reservation.duid) duidInput.value = reservation.duid;
            idTypeSelect.value = 'duid';
        }
        applyIdType();

        row.appendChild(idTypeGroup);
        row.appendChild(duidGroup);
        row.appendChild(macGroup);
    } else {
        row.appendChild(buildMacGroup(poolIndex, rowId, reservation && reservation.mac));
    }

    const ipGroup = document.createElement('div');
    ipGroup.className = 'dhcp-form-group';
    const ipLabel = document.createElement('label');
    const ipId = `dhcp-reservation-ip-${poolIndex}-${rowId}`;
    ipLabel.htmlFor = ipId;
    ipLabel.textContent = 'Reserved address';
    const ipInput = document.createElement('input');
    ipInput.type = 'text';
    ipInput.id = ipId;
    ipInput.name = 'reservation-ip';
    ipInput.value = (reservation && reservation.ip) || '';
    ipInput.autocomplete = 'off';
    ipInput.spellcheck = false;
    ipGroup.appendChild(ipLabel);
    ipGroup.appendChild(ipInput);

    const hostnameGroup = document.createElement('div');
    hostnameGroup.className = 'dhcp-form-group';
    const hostnameLabel = document.createElement('label');
    const hostnameId = `dhcp-reservation-hostname-${poolIndex}-${rowId}`;
    hostnameLabel.htmlFor = hostnameId;
    hostnameLabel.textContent = 'Hostname (optional)';
    const hostnameInput = document.createElement('input');
    hostnameInput.type = 'text';
    hostnameInput.id = hostnameId;
    hostnameInput.name = 'reservation-hostname';
    hostnameInput.value = (reservation && reservation.hostname) || '';
    hostnameInput.autocomplete = 'off';
    hostnameInput.spellcheck = false;
    hostnameGroup.appendChild(hostnameLabel);
    hostnameGroup.appendChild(hostnameInput);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'dhcp-reservation-remove-btn';
    removeBtn.textContent = 'Remove';
    removeBtn.setAttribute('aria-label', 'Remove this reservation');
    removeBtn.addEventListener('click', () => row.remove());

    row.appendChild(ipGroup);
    row.appendChild(hostnameGroup);
    row.appendChild(removeBtn);
    return row;
}

/** Reservations left blank (an "Add reservation" row nobody filled in) are not submitted. */
function gatherReservations(listEl) {
    if (!listEl) return [];
    const isV6 = listEl.dataset.family === '6';
    const reservations = [];
    listEl.querySelectorAll('.dhcp-reservation-row').forEach((row) => {
        const ip = row.querySelector('input[name="reservation-ip"]').value.trim();
        const hostname = row.querySelector('input[name="reservation-hostname"]').value.trim();
        let mac = '';
        let duid = '';
        if (isV6) {
            const byMac = row.querySelector('select[name="reservation-id-type"]').value === 'mac';
            const identity = row.querySelector(
                byMac ? 'input[name="reservation-mac"]' : 'input[name="reservation-duid"]'
            );
            if (byMac) mac = identity.value.trim();
            else duid = identity.value.trim();
        } else {
            mac = row.querySelector('input[name="reservation-mac"]').value.trim();
        }
        if (!mac && !duid && !ip) return;
        reservations.push({ mac, duid, ip, hostname });
    });
    return reservations;
}

async function handlePoolSubmit(form, cidr, submitBtn, feedback) {
    const node = currentNode;
    if (!node) return;

    const startInput = form.querySelector('input[name="start"]');
    const endInput = form.querySelector('input[name="end"]');
    const leaseTimeInput = form.querySelector('input[name="lease_time"]');
    const routerInput = form.querySelector('input[name="router"]');
    const preferredLifetimeInput = form.querySelector('input[name="preferred_lifetime"]');
    const dnsServerInput = form.querySelector('input[name="dns_server"]');
    const reservationsList = form.querySelector('.dhcp-reservation-list');

    const start = startInput.value.trim();
    const end = endInput.value.trim();
    const leaseTimeRaw = leaseTimeInput.value.trim();
    const lease_time = leaseTimeRaw ? Number(leaseTimeRaw) : null;
    const router = routerInput ? routerInput.value.trim() : '';
    const dns_server = dnsServerInput.value.trim();
    const reservations = gatherReservations(reservationsList);

    // Preferred lifetime is IPv6-only; omit it entirely for IPv4 Pools so
    // the backend does not receive a field the Pool model rejects.
    const prefRaw = preferredLifetimeInput ? preferredLifetimeInput.value.trim() : '';
    const preferred_lifetime = prefRaw ? Number(prefRaw) : null;

    clearFormFeedback(form, feedback);
    submitBtn.disabled = true;
    submitBtn.textContent = 'Applying…';

    try {
        const response = await updatePool(node, {
            cidr, start, end, lease_time, preferred_lifetime,
            router, dns_server, reservations,
        });
        let data = null;
        try {
            data = await response.json();
        } catch (_) { /* ignore */ }

        if (node !== currentNode) return;

        if (!response.ok) {
            const detail = (data && data.detail) || `Failed to update pool (${response.status})`;
            showFormError(form, feedback, detail);
            return;
        }

        showFormSuccess(form, feedback, 'Pool applied successfully');
        if (data && data.pool) {
            startInput.value = data.pool.start;
            endInput.value = data.pool.end;
            leaseTimeInput.value = data.pool.lease_time != null ? String(data.pool.lease_time) : '';
            if (routerInput) routerInput.value = data.pool.router || '';
            if (preferredLifetimeInput) {
                preferredLifetimeInput.value = data.pool.preferred_lifetime != null
                    ? String(data.pool.preferred_lifetime) : '';
            }
            dnsServerInput.value = data.pool.dns_server || '';
            if (reservationsList) {
                renderReservationRows(reservationsList, data.pool.reservations || []);
            }
        }
    } catch (error) {
        if (node === currentNode) {
            showFormError(form, feedback, `Network error: ${error.message}`);
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Apply';
    }
}

/** Every field a pool's form carries, for aria-invalid toggling on submit. */
function formInputs(form) {
    return form.querySelectorAll('.dhcp-pool-fields input, .dhcp-reservation-row input');
}

function clearFormFeedback(form, feedback) {
    formInputs(form).forEach((input) => input.removeAttribute('aria-invalid'));
    feedback.textContent = '';
    feedback.className = 'dhcp-pool-feedback';
}

function showFormError(form, feedback, message) {
    formInputs(form).forEach((input) => input.setAttribute('aria-invalid', 'true'));
    feedback.textContent = message;
    feedback.className = 'dhcp-pool-feedback dhcp-pool-error';
}

function showFormSuccess(form, feedback, message) {
    formInputs(form).forEach((input) => input.removeAttribute('aria-invalid'));
    feedback.textContent = message;
    feedback.className = 'dhcp-pool-feedback dhcp-pool-success';
}

function renderPoolsError(message) {
    const container = document.getElementById('dhcp-pools-container');
    if (!container) return;
    container.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'dhcp-pool-feedback dhcp-pool-error';
    p.textContent = message;
    container.appendChild(p);
}

function clearPools() {
    const container = document.getElementById('dhcp-pools-container');
    if (container) container.innerHTML = '';
}

export function handlePoolBroadcast(data) {
    if (!data || !currentNode || data.node !== currentNode) return;
    const pools = Array.isArray(data.pools) ? data.pools : (data.pool ? [data.pool] : []);
    updatePoolsFromBroadcast(pools);
}

function updatePoolsFromBroadcast(pools) {
    const container = document.getElementById('dhcp-pools-container');
    if (!container) return;

    pools.forEach((pool) => {
        const form = container.querySelector(`form[data-cidr="${CSS.escape(pool.cidr)}"]`);
        if (form) {
            const startInput = form.querySelector('input[name="start"]');
            const endInput = form.querySelector('input[name="end"]');
            const leaseTimeInput = form.querySelector('input[name="lease_time"]');
            const routerInput = form.querySelector('input[name="router"]');
            const dnsServerInput = form.querySelector('input[name="dns_server"]');
            const reservationsList = form.querySelector('.dhcp-reservation-list');
            const feedback = form.querySelector('.dhcp-pool-feedback');
            if (startInput) startInput.value = pool.start || '';
            if (endInput) endInput.value = pool.end || '';
            if (leaseTimeInput) {
                leaseTimeInput.value = pool.lease_time != null ? String(pool.lease_time) : '';
            }
            if (routerInput) routerInput.value = pool.router || '';
            const preferredLifetimeInput = form.querySelector('input[name="preferred_lifetime"]');
            if (preferredLifetimeInput) {
                preferredLifetimeInput.value = pool.preferred_lifetime != null
                    ? String(pool.preferred_lifetime) : '';
            }
            if (dnsServerInput) dnsServerInput.value = pool.dns_server || '';
            if (reservationsList) {
                renderReservationRows(reservationsList, pool.reservations || []);
            }
            formInputs(form).forEach((input) => input.removeAttribute('aria-invalid'));
            if (feedback && !feedback.classList.contains('dhcp-pool-success')) {
                feedback.textContent = '';
                feedback.className = 'dhcp-pool-feedback';
            }
        } else {
            renderPools(pools);
        }
    });
}

async function refreshLeases() {
    const node = currentNode;
    if (!node) return;

    let response;
    try {
        response = await getLeases(node);
    } catch (error) {
        if (node === currentNode) {
            setStatus(`Can't reach the server: ${error.message}`, true);
            clearRows();
        }
        return;
    }

    // The panel may have been closed (or reopened for a different Node)
    // while this request was in flight.
    if (node !== currentNode) return;

    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }

    if (!response.ok) {
        const detail = (data && data.detail) || `Request failed (${response.status})`;
        setStatus(detail, true);
        clearRows();
        return;
    }

    const hasV4 = 'v4' in (data || {}) || ('leases' in (data || {}) && !('v6' in (data || {})));
    const hasV6 = 'v6' in (data || {});
    const v4Leases = (data && (data.v4 !== undefined ? data.v4 : data.leases)) || [];
    const v6Leases = (data && data.v6) || [];

    const totalLeases = (hasV4 ? v4Leases.length : 0) + (hasV6 ? v6Leases.length : 0);
    if (totalLeases === 0) {
        setStatus('No Leases yet.', false);
    } else {
        setStatus('', false);
    }

    // "Via" labelling (relayed vs direct) appears only when the Lab stages a
    // Relay for that family — the LeaseQueryService adds `relayed` to every
    // Lease of that family and none otherwise, so a Lab with no Relay shows
    // no relay labelling at all rather than a "Direct" on every row. The
    // check is per family: a dual-stack Service with only the v4 Subnet
    // relayed labels the v4 table and leaves the v6 table exactly as it was.
    // A relayed Lease is otherwise displayed exactly as a direct one: the
    // label is one extra cell, never a restructured row or a second table.
    const v4Via = hasV4 && v4Leases.some((lease) => 'relayed' in lease);
    const v6Via = hasV6 && v6Leases.some((lease) => 'relayed' in lease);
    setViaColumn('dhcp-v4-via-col', v4Via);
    setViaColumn('dhcp-v6-via-col', v6Via);

    const v4Container = document.getElementById('dhcp-v4-leases-container');
    const v6Container = document.getElementById('dhcp-v6-leases-container');

    if (v4Container) {
        v4Container.style.display = hasV4 ? '' : 'none';
    }
    if (v6Container) {
        v6Container.style.display = hasV6 ? '' : 'none';
    }

    const tbody4 = document.getElementById('dhcp-leases-tbody');
    if (tbody4 && hasV4) {
        const rows4 = v4Leases.map((lease) => {
            const address = lease['ip-address'] || '—';
            const cells = [
                address,
                leaseClient(lease),
                formatExpiry(lease),
                appliedCell(tbody4, address, isApplied(address)),
            ];
            if (v4Via) cells.push(viaCell(tbody4, address, lease));
            return { key: address, cells };
        });
        patchTableRows(tbody4, rows4);
        trimExtraCells(tbody4, v4Via ? 5 : 4);
    } else if (tbody4) {
        patchTableRows(tbody4, []);
    }

    const tbody6 = document.getElementById('dhcp-v6-leases-tbody');
    if (tbody6 && hasV6) {
        const rows6 = v6Leases.map((lease) => {
            const address = lease['ip-address'] || '—';
            const cells = [
                address,
                v6ClientCell(tbody6, lease),
                formatPreferred(lease),
                formatExpiry(lease),
                appliedCell(tbody6, address, isApplied(address)),
            ];
            if (v6Via) cells.push(viaCell(tbody6, address, lease));
            return { key: address, cells };
        });
        patchTableRows(tbody6, rows6);
        trimExtraCells(tbody6, v6Via ? 6 : 5);
    } else if (tbody6) {
        patchTableRows(tbody6, []);
    }
}

function leaseClient(lease) {
    return lease['hostname'] || lease['hw-address'] || lease['client-id'] || '—';
}

/** The IPv6 Lease's client cell, reused across polls the way the Applied
 * cell is: a long DUID must stay put — focus on its Copy button, and a
 * screen reader's place in the table, survive an unchanged poll.
 *
 * The DUID is the identity a Learner copies into a Reservation, so it needs
 * more than a narrow column: it sits on its own line, wraps anywhere, and
 * selects as one unit (user-select: all), with a Copy button for the
 * clipboard path. */
function v6ClientCell(tbody, lease) {
    const address = lease['ip-address'] || '—';
    const hostname = lease['hostname'] || '';
    const duid = lease['duid'] || '';
    const hwAddress = lease['hw-address'] || '';
    const existing = tbody.querySelector(
        `.dhcp-lease-client[data-ip="${CSS.escape(address)}"]`
    );
    const cell = existing || document.createElement('div');
    if (!existing) {
        cell.className = 'dhcp-lease-client';
        cell.dataset.ip = address;
    }
    if (cell.dataset.hostname === hostname && cell.dataset.duid === duid && cell.dataset.hwAddress === hwAddress) {
        return cell;
    }
    cell.dataset.hostname = hostname;
    cell.dataset.duid = duid;
    cell.dataset.hwAddress = hwAddress;
    cell.replaceChildren();

    if (hostname) {
        const name = document.createElement('span');
        name.className = 'dhcp-lease-hostname';
        name.textContent = hostname;
        cell.appendChild(name);
    }
    if (!duid && !hwAddress) {
        if (!hostname) cell.textContent = '—';
        return cell;
    }
    if (duid) {
        const code = document.createElement('code');
        code.className = 'dhcp-lease-duid';
        code.textContent = duid;
        cell.appendChild(code);

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'dhcp-lease-copy-btn';
        copyBtn.textContent = 'Copy DUID';
        copyBtn.setAttribute('aria-label', `Copy DUID ${duid}`);
        copyBtn.addEventListener('click', () => { void copyDuid(duid, copyBtn); });
        cell.appendChild(copyBtn);
    }
    if (hwAddress) {
        const hwContainer = document.createElement('div');
        hwContainer.className = 'dhcp-lease-hw-container';
        const hwLabel = document.createElement('span');
        hwLabel.className = 'dhcp-lease-hw-label';
        hwLabel.textContent = 'MAC: ';
        const hwCode = document.createElement('code');
        hwCode.className = 'dhcp-lease-hw';
        hwCode.textContent = hwAddress;
        hwContainer.appendChild(hwLabel);
        hwContainer.appendChild(hwCode);

        const copyMacBtn = document.createElement('button');
        copyMacBtn.type = 'button';
        copyMacBtn.className = 'dhcp-lease-copy-btn dhcp-lease-copy-mac-btn';
        copyMacBtn.textContent = 'Copy MAC';
        copyMacBtn.setAttribute('aria-label', `Copy MAC ${hwAddress}`);
        copyMacBtn.addEventListener('click', () => { void copyMac(hwAddress, copyMacBtn); });
        hwContainer.appendChild(copyMacBtn);
        cell.appendChild(hwContainer);
    }
    return cell;
}

async function copyDuid(duid, button) {
    try {
        await navigator.clipboard.writeText(duid);
        button.textContent = 'Copied';
        announce('DUID copied to the clipboard.');
    } catch (_) {
        announce('Could not copy the DUID; select it and copy it manually.');
    }
    setTimeout(() => { button.textContent = 'Copy DUID'; }, 2000);
}

async function copyMac(mac, button) {
    try {
        await navigator.clipboard.writeText(mac);
        button.textContent = 'Copied';
        announce('Hardware address copied to the clipboard.');
    } catch (_) {
        announce('Could not copy the hardware address; select it and copy it manually.');
    }
    setTimeout(() => { button.textContent = 'Copy MAC'; }, 2000);
}

/** Polite announcement through the panel's shared screen-reader-only status
 * region (copy results, cleared identifier fields). */
function announce(message) {
    const status = document.getElementById('dhcp-lease-copy-status');
    if (status) status.textContent = message;
}

/** "when it deprecates", from Kea's `cltt` + `preferred-lft` (seconds held). */
function formatPreferred(lease) {
    const cltt = lease['cltt'];
    const prefLft = lease['preferred-lft'] !== undefined ? lease['preferred-lft'] : lease['preferred_lft'];
    if (typeof cltt !== 'number' || typeof prefLft !== 'number') return '—';

    const deprecatesAt = new Date((cltt + prefLft) * 1000);
    if (Number.isNaN(deprecatesAt.getTime())) return '—';

    const label = deprecatesAt.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    return deprecatesAt.getTime() <= Date.now() ? `${label} (deprecated)` : label;
}

/** "when it expires", from Kea's `cltt` (last transaction) + `valid-lft` (seconds held). */
function formatExpiry(lease) {
    const cltt = lease['cltt'];
    const validLft = lease['valid-lft'] !== undefined ? lease['valid-lft'] : lease['valid_lft'];
    if (typeof cltt !== 'number' || typeof validLft !== 'number') return '—';

    const expiresAt = new Date((cltt + validLft) * 1000);
    if (Number.isNaN(expiresAt.getTime())) return '—';

    const label = expiresAt.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    return expiresAt.getTime() <= Date.now() ? `${label} (expired)` : label;
}

/**
 * A Lease the current Topology's Nodes don't show an address for is the
 * server's record alone. Checked against every Interface a Node has, not
 * just its default one — a multi-homed Node's DHCP-client Interface is often
 * not the default (one Interface managed, another static), so `ip` alone
 * would misreport that Interface as unapplied.
 */
function isApplied(address) {
    const nodes = appState.currentTopology?.nodes || [];
    const target = (address || '').toLowerCase();
    return nodes.some((n) => (n.ips || [n.ip]).some((ip) => (ip || '').toLowerCase() === target));
}

/**
 * The row's "Applied" cell, reused across polls the same way the instructor
 * Sessions panel reuses its logout button — mutating an existing node avoids
 * re-announcing an unchanged cell to a screen reader on every poll.
 */
function appliedCell(tbody, address, applied) {
    const label = applied ? 'Applied' : 'Not applied (server record only)';
    const existing = tbody.querySelector(
        `.dhcp-lease-applied[data-ip="${CSS.escape(address)}"]`
    );
    const span = existing || document.createElement('span');
    if (!existing) {
        span.className = 'dhcp-lease-applied';
        span.dataset.ip = address;
    }
    if (span.textContent !== label) span.textContent = label;
    span.classList.toggle('dhcp-lease-not-applied', !applied);
    return span;
}

/** Show or hide a Lease table's "Via" column header. */
function setViaColumn(colId, visible) {
    const col = document.getElementById(colId);
    if (col) col.style.display = visible ? '' : 'none';
}

/** The "Via" cell: which path the Lease took — Relayed or Direct. The
 * backend labels every Lease once the Lab stages a Relay; a Lease whose
 * subnet identifier matches no Pool the framework knows about is absent
 * data, shown as '—' rather than guessed. Reused across polls like the
 * "Applied" cell so an unchanged label is not re-announced. */
function viaCell(tbody, address, lease) {
    const label = typeof lease.relayed === 'boolean'
        ? (lease.relayed ? 'Relayed' : 'Direct')
        : '—';
    const existing = tbody.querySelector(
        `.dhcp-lease-via[data-ip="${CSS.escape(address)}"]`
    );
    const span = existing || document.createElement('span');
    if (!existing) {
        span.className = 'dhcp-lease-via';
        span.dataset.ip = address;
    }
    if (span.textContent !== label) span.textContent = label;
    return span;
}

/** patchTableRows patches only the cells a row provides, so a row rendered
 * while the "Via" column was visible keeps a stale cell when the column
 * disappears; drop anything past the expected count. */
function trimExtraCells(tbody, cellCount) {
    for (const tr of tbody.rows) {
        while (tr.children.length > cellCount) {
            tr.lastElementChild.remove();
        }
    }
}

function setStatus(message, isError) {
    const el = document.getElementById('dhcp-leases-status');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('dhcp-leases-status-error', Boolean(isError && message));
}

function clearRows() {
    const tbody4 = document.getElementById('dhcp-leases-tbody');
    if (tbody4) patchTableRows(tbody4, []);
    const tbody6 = document.getElementById('dhcp-v6-leases-tbody');
    if (tbody6) patchTableRows(tbody6, []);
}
