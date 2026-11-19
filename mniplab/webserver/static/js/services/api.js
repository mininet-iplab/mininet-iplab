// API fetch wrappers

import { appState } from '../state/app-state.js';
import { handleSessionLost } from './websocket.js';

// Everything that reads or writes a Lab lives under its key. The key is
// kept in lockstep with the page URL's `/labs/{key}` segment (see
// lab/routing.js): seeded from it on load, confirmed or corrected by the
// server's status, and mirrored back into it on every change. With no Lab
// bound the segment is '-', which no key can be, so the server answers "No
// lab running" rather than "Not Found".
function labPath(path) {
    return `/api/labs/${encodeURIComponent(appState.labKey || '-')}${path}`;
}

// Every request but login goes through here. A 401 means the Session is
// gone, so the page stops and returns to login rather than keep asking;
// login's own 401 means wrong credentials and is the login form's to show.
async function apiFetch(url, options) {
    const response = await fetch(url, options);
    if (response.status === 401) handleSessionLost();
    return response;
}

export async function getTopology() {
    return apiFetch(labPath('/topology'));
}

export async function getStatus() {
    return apiFetch('/api/status');
}

export async function getLabs() {
    return apiFetch('/api/labs');
}

// One running Lab by its key, whatever the caller's own Lab is — the
// Instructor's way to bind the page to another Group's Lab. 404 when the
// key holds no running Lab.
export async function getLab(key) {
    return apiFetch(`/api/labs/${encodeURIComponent(key)}`);
}

export async function getExamples() {
    return apiFetch('/api/examples');
}

// `group` names another Group the Instructor starts the Lab for; omitted,
// the Lab is the caller's own.  `clean` is "start clean": the Group's copy
// of the Lab Example's Persistent Config is discarded, so it starts from
// the seed.
export async function startLab(exampleId, group = null, clean = false) {
    const body = { example_id: exampleId };
    if (group) body.group = group;
    if (clean) body.clean = true;
    return apiFetch('/api/labs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

// Stop one Lab by its key — any Lab the caller may reach, not only the page's.
export async function stopLabByKey(key) {
    return apiFetch(`/api/labs/${encodeURIComponent(key)}/stop`, { method: 'POST' });
}

// The Instructor's dashboard: every Group with its Lab, every Lab with its key.
export async function getDashboard() {
    return apiFetch('/api/admin/dashboard');
}

export async function getAssignments() {
    return apiFetch('/api/admin/assignments');
}

// Reset a Group's copy of a Lab Example's Persistent Config to the seed —
// every Group's copy when `group` is omitted.  Instructor only.
export async function resetConfig(exampleId, group = null) {
    const body = { example_id: exampleId };
    if (group) body.group = group;
    return apiFetch('/api/admin/config/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

export async function setAssignment(group, exampleIds) {
    return apiFetch(`/api/admin/assignments/${encodeURIComponent(group)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ example_ids: exampleIds })
    });
}

// The Limits an Instructor may change in Server settings (serve only).
export async function getServerSettings() {
    return apiFetch('/api/admin/server-settings');
}

// Save some Limits, all or none; a 422 names the field refused.
export async function saveServerSettings(limits) {
    return apiFetch('/api/admin/server-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limits })
    });
}

// Every login the server accepts, without passwords (serve only).
export async function getLogins() {
    return apiFetch('/api/admin/logins');
}

// Add a Learner login; the answer carries its generated password.
export async function addLogin(name) {
    return apiFetch('/api/admin/logins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
}

// A page-added login's password, read back for the Instructor.
export async function getLoginPassword(name) {
    return apiFetch(`/api/admin/logins/${encodeURIComponent(name)}/password`);
}

// Replace a page-added login's password; the answer carries the new one.
export async function resetLoginPassword(name) {
    return apiFetch(`/api/admin/logins/${encodeURIComponent(name)}/reset`, { method: 'POST' });
}

// Remove a page-added login, ending its Sessions.
export async function removeLogin(name) {
    return apiFetch(`/api/admin/logins/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

export async function stopLab() {
    return apiFetch(labPath('/stop'), { method: 'POST' });
}

export async function getMe() {
    return apiFetch('/api/me');
}

export async function renameSessionNickname(nickname) {
    return apiFetch('/api/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname })
    });
}

export async function login(credentials) {
    return fetch('/api/login', {
        method: 'POST',
        headers: { 'Authorization': `Basic ${credentials}` }
    });
}

export async function logout() {
    return apiFetch('/api/logout', { method: 'POST' });
}

export async function getTerminals() {
    return apiFetch('/api/admin/terminals');
}

// Sessions — one person's authenticated connection — as opposed to
// getTerminals above, which lists the shells they hold.
export async function getSessions() {
    return apiFetch('/api/admin/sessions');
}

export async function forceLogoutSession(sessionId) {
    return apiFetch('/api/admin/sessions/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
    });
}

// Close one Terminal by its id — the Instructor's way to end a shell a
// Learner holds.  Instructor only.
export async function closeTerminal(terminalId) {
    return apiFetch('/api/admin/terminals/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ terminal_id: terminalId })
    });
}

export async function loadGuide(labId, key = null) {
    const guidePath = `/api/load_guide?example=${encodeURIComponent(labId)}`
        + (key ? `&key=${encodeURIComponent(key)}` : '');
    return apiFetch(guidePath, { cache: 'no-store' });
}

export async function loadLayout(labName, key = null) {
    return apiFetch('/api/load_layout?example=' + encodeURIComponent(labName)
        + (key ? `&key=${encodeURIComponent(key)}` : ''));
}

export async function saveLayoutToServer(labName, positions) {
    return apiFetch('/api/save_layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ example: labName, positions })
    });
}

// Console / test endpoints
export async function linkAction(action, node1, node2) {
    return apiFetch(labPath(`/link/${action}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node1, node2 })
    });
}

export async function configureLink(params) {
    return apiFetch(labPath('/link/configure'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
}

export async function getRouting(type, node) {
    return apiFetch(labPath(`/routing/${type}/${node}`));
}

export async function getLeases(node) {
    return apiFetch(labPath(`/dhcp/leases/${encodeURIComponent(node)}`));
}

export async function getRelayActivity(node) {
    return apiFetch(labPath(`/dhcp/relay/activity/${encodeURIComponent(node)}`));
}

export async function getPools(node) {
    return apiFetch(labPath(`/dhcp/pools/${encodeURIComponent(node)}`));
}

export async function getSpeaker(node) {
    return apiFetch(labPath(`/speakers/${encodeURIComponent(node)}`));
}

export async function announceSpeakerRoute(node, body) {
    return apiFetch(labPath(`/speakers/${encodeURIComponent(node)}/announce`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

export async function generateSpeakerRoutes(node, body) {
    return apiFetch(labPath(`/speakers/${encodeURIComponent(node)}/generate`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

export async function withdrawSpeakerRoute(node, body) {
    return apiFetch(labPath(`/speakers/${encodeURIComponent(node)}/withdraw`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

export async function updatePool(node, pool) {
    return apiFetch(labPath(`/dhcp/pools/${encodeURIComponent(node)}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pool)
    });
}

export async function getDnsNodes() {
    return apiFetch(labPath('/dns/nodes'));
}

export async function getDnsRecords(node, zone) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/records`));
}

export async function setDnsRecord(node, zone, record) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/records`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(record)
    });
}

export async function deleteDnsRecord(node, zone, record) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/records`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(record)
    });
}

export async function getDnsStats(node) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/stats`));
}

export async function resetDnsStats(node) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/stats/reset`), { method: 'POST' });
}

export async function getDnsZoneStatus(node, zone) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/status`));
}

export async function getDnsTransfers(node, zone = null, includeNotify = true) {
    const url = zone
        ? labPath(`/dns/${encodeURIComponent(node)}/transfers?zone=${encodeURIComponent(zone)}&include_notify=${includeNotify}`)
        : labPath(`/dns/${encodeURIComponent(node)}/transfers?include_notify=${includeNotify}`);
    return apiFetch(url);
}

export async function refreshDnsZone(node, zone) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/refresh`), {
        method: 'POST',
    });
}

export async function retransferDnsZone(node, zone) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/retransfer`), {
        method: 'POST',
    });
}

export async function getDnsZoneDnssec(node, zone) {
    return apiFetch(labPath(`/dns/${encodeURIComponent(node)}/zones/${encodeURIComponent(zone)}/dnssec`));
}

export async function getRpkiCas(node) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/cas`));
}

export async function saveRpkiRoa(node, asn, roa) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/cas/${encodeURIComponent(asn)}/roas`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(roa)
    });
}

export async function deleteRpkiRoa(node, asn, roa) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/cas/${encodeURIComponent(asn)}/roas`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(roa)
    });
}

export async function saveRpkiAspa(node, asn, aspa) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/cas/${encodeURIComponent(asn)}/aspas`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(aspa)
    });
}

export async function deleteRpkiAspa(node, asn, customer) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/cas/${encodeURIComponent(asn)}/aspas`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer })
    });
}

export async function getRpkiVrps(node) {
    return apiFetch(labPath(`/rpki/${encodeURIComponent(node)}/vrps`));
}

export async function getResolverCache(node) {
    return apiFetch(labPath(`/nodes/${encodeURIComponent(node)}/resolver/cache`));
}

export async function flushResolverCache(node, target = null, scope = 'name') {
    return apiFetch(labPath(`/nodes/${encodeURIComponent(node)}/resolver/cache/flush`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, scope })
    });
}

export async function getResolverAccess(node) {
    return apiFetch(labPath(`/nodes/${encodeURIComponent(node)}/resolver/access`));
}

export async function setResolverAccess(node, rules) {
    return apiFetch(labPath(`/nodes/${encodeURIComponent(node)}/resolver/access`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rules })
    });
}

export async function getSwitchVlans(switchName) {
    return apiFetch(labPath(`/switches/${encodeURIComponent(switchName)}/vlans`));
}

export async function getSwitchMacTable(switchName) {
    return apiFetch(labPath(`/switches/${encodeURIComponent(switchName)}/mac-table`));
}

export async function flushSwitchMacTable(switchName) {
    return apiFetch(labPath(`/switches/${encodeURIComponent(switchName)}/mac-table/flush`), {
        method: 'POST',
    });
}

export async function getSwitchSpanningTree(switchName) {
    return apiFetch(labPath(`/switches/${encodeURIComponent(switchName)}/spanning-tree`));
}

export async function setSwitchPortVlan(switchName, portName, data) {
    return apiFetch(labPath(`/switches/${encodeURIComponent(switchName)}/ports/${encodeURIComponent(portName)}/vlan`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}


// Every Interface of a Node that may be captured, including those whose
// Link is not drawn.
export async function getCaptureInterfaces(node) {
    return apiFetch(labPath(`/capture/interfaces/${encodeURIComponent(node)}`));
}

// A Packet Capture's pcap: the Interface and the filter name the Capture.
export async function getCapturePcap(node, intf, filter) {
    const params = new URLSearchParams({ node, interface: intf, filter: filter || '' });
    return apiFetch(labPath(`/capture/pcap?${params}`));
}

// Every translating Router's NAT Table; polled.
export async function getNatTable() {
    return apiFetch(labPath('/nat-table'));
}
