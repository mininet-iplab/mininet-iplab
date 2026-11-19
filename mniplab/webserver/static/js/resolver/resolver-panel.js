// Recursive Caching Resolver panel — the modal a Learner opens from a Node's
// RESOLVER badge to watch the cache the Resolver is holding and to author the
// Access Control rules it enforces.
//
// Two tabs, because the two halves answer different questions and are read at
// different rates. The Cache Inspector is a live view of Unbound's own cache
// dump (see mniplab/resolver/cache_dump.py): it polls, and between polls it
// ticks each row's TTL down locally so the countdown a Learner is watching
// moves every second rather than jumping every poll. The tick is display-only
// — the next poll replaces it with what the daemon actually reports, so a
// clock that drifts cannot make an entry look alive after eviction. That
// correction is the tick's whole warrant, which is why turning auto-refresh
// off stops it too (see tickTtls).
//
// The TTL cells are deliberately NOT inside an aria-live region: a countdown
// that announced itself every second would make the table unusable with a
// screen reader. The row's own status text carries what matters instead.
//
// A validating Resolver records one more thing about every RRset it holds:
// whether the signatures over it verified. That verdict rides along in the
// cache dump (see mniplab/resolver/cache_dump.py) and is rendered as its own
// badge — kept separate from the HIT/NXDOMAIN badge beside it, because "the
// Resolver can answer this from memory" and "the Resolver proved this answer
// genuine" are different claims, and a Learner staging a failure needs to
// watch one change while the other does not.
//
// Access Control rules are Exercise Configuration. The whole
// policy is PUT at once, so add, edit, and delete each reach the daemon as a
// single reload_keep_cache — which is what lets a Learner change policy
// without dropping the cache the other tab is showing. Every applied policy
// is broadcast as "RESOLVER_ACCESS_UPDATED", and the handler below is
// last-write-wins, the same contract the DNS records panel has.

import {
    getResolverCache,
    flushResolverCache,
    getResolverAccess,
    setResolverAccess,
} from '../services/api.js';
import { formatResponseError, patchTableRows, setElementText } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

const POLL_INTERVAL_MS = 5000;
const TICK_INTERVAL_MS = 1000;

let currentNode = null;
let pollTimer = null;
let tickTimer = null;
let trap = null;
let cacheRefreshId = 0;
// The cache as last polled, with each row's TTL decremented locally by the
// tick. Held here rather than read back out of the DOM so a re-render always
// starts from numbers, not from the strings it last printed.
let cacheEntries = [];
// The rule being edited, or null when the form is in Add mode. Editing
// re-uses the Add form and is keyed by the original subnet, because an edit
// may change the subnet itself.
let editingRule = null;
let editTriggerButton = null;
// The policy as last applied, which the form mutates a copy of before PUTting
// the whole thing back.
let accessRules = [];

export function initResolverPanel() {
    const modal = document.getElementById('resolver-modal');
    if (!modal) return;

    const closeBtn = document.getElementById('resolver-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeResolverPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeResolverPanel();
    });

    initTabs();

    const flushAll = document.getElementById('resolver-flush-all');
    if (flushAll) {
        flushAll.addEventListener('click', () => { void flushEntireCache(); });
    }

    const form = document.getElementById('resolver-access-form');
    if (form) {
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            void handleAccessSubmit();
        });
    }

    const cancel = document.getElementById('resolver-access-cancel');
    if (cancel) cancel.addEventListener('click', () => resetAccessForm());
}

/** Wire the two tabs as an ARIA tablist with a roving tabindex: only the
 * selected tab is in the page's tab order, and Left/Right (plus Home/End)
 * move between them, which is what a screen-reader user expects of tabs. */
function initTabs() {
    const tabs = Array.from(document.querySelectorAll('.resolver-tab'));
    if (tabs.length === 0) return;

    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectTab(tab.dataset.tab));
        tab.addEventListener('keydown', (event) => {
            let next = null;
            if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
            else if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
            else if (event.key === 'Home') next = tabs[0];
            else if (event.key === 'End') next = tabs[tabs.length - 1];
            if (!next) return;
            event.preventDefault();
            selectTab(next.dataset.tab);
            next.focus();
        });
    });
}

function selectTab(name) {
    document.querySelectorAll('.resolver-tab').forEach((tab) => {
        const selected = tab.dataset.tab === name;
        tab.classList.toggle('active', selected);
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll('.resolver-panel').forEach((panel) => {
        const selected = panel.id === `resolver-panel-${name}`;
        panel.classList.toggle('active', selected);
        panel.hidden = !selected;
    });
}

export function openResolverPanel(nodeName) {
    const modal = document.getElementById('resolver-modal');
    if (!modal) return;

    // Whatever the Learner pressed to get here — the badge, or the context
    // menu item. Captured before the old trap is released, because releasing
    // moves focus back to the *previous* trigger, and the new trap would
    // otherwise adopt that stale element as the one to return focus to.
    const trigger = document.activeElement;

    currentNode = nodeName;
    cacheRefreshId += 1;
    cacheEntries = [];
    accessRules = [];
    const title = document.getElementById('resolver-modal-title');
    if (title) title.textContent = `${nodeName} — Recursive Caching Resolver`;
    selectTab('cache');
    resetAccessForm();
    renderCache([]);
    renderMetrics(null);
    renderAccessRules([]);
    setCacheStatus('Reading the cache…', false);
    setAccessStatus('', false);

    modal.style.display = 'flex';
    // Re-opening while already open (another badge press) must not leak the
    // previous trap's document-level listener.
    if (trap) {
        trap.release();
        if (trigger && trigger.isConnected) trigger.focus();
    }
    trap = trapFocus(modal, { onClose: closeResolverPanel });

    void refreshCache(true);
    void refreshAccess();

    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshCache(); }, POLL_INTERVAL_MS);
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(tickTtls, TICK_INTERVAL_MS);
}

export function closeResolverPanel() {
    const modal = document.getElementById('resolver-modal');
    if (modal) modal.style.display = 'none';
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    if (tickTimer) {
        clearInterval(tickTimer);
        tickTimer = null;
    }
    currentNode = null;
    cacheRefreshId += 1;
    cacheEntries = [];
    accessRules = [];
    editingRule = null;
    if (trap) {
        trap.release();
        trap = null;
    }
}

async function readJson(response) {
    try {
        return await response.json();
    } catch (_) {
        return null;
    }
}

// ── Cache Inspector ─────────────────────────────────────────────────────────

async function refreshCache(force = false) {
    const node = currentNode;
    if (!node) return;

    const autoRefresh = document.getElementById('resolver-cache-autorefresh');
    if (!force && autoRefresh && !autoRefresh.checked) return;

    const requestId = ++cacheRefreshId;

    let response;
    try {
        response = await getResolverCache(node);
    } catch (error) {
        if (node === currentNode) {
            setCacheStatus(`Can't reach the server: ${error.message || error}`, true);
        }
        return;
    }

    if (node !== currentNode || requestId !== cacheRefreshId) return;

    const data = await readJson(response);
    if (node !== currentNode || requestId !== cacheRefreshId) return;

    if (!response.ok) {
        setCacheStatus(
            formatResponseError(data, `Request failed (${response.status})`),
            true,
        );
        return;
    }

    cacheEntries = ((data && data.entries) || []).map((entry) => ({ ...entry }));
    renderMetrics((data && data.metrics) || null);
    setCacheStatus(
        cacheEntries.length === 0
            ? 'The cache is empty. Query a name through this Resolver to fill it.'
            : '',
        false,
    );
    renderCache(cacheEntries);
}

/** Count every cached row down by one second between polls.
 *
 * Rows that reach zero are kept, not dropped: Unbound evicts on its own
 * schedule, and hiding a row the daemon still reports would tell a Learner
 * the entry is gone a moment before it actually is. The next poll is what
 * removes it.
 *
 * Which is why the tick stops when auto-refresh is off. The countdown is only
 * honest because a poll is coming to correct it; without one it would walk
 * every row down to 0s and leave it there, showing a cache full of expired
 * entries that the Resolver may well still be serving. A frozen table of
 * last-known TTLs is the truthful thing to show instead. */
function tickTtls() {
    if (!currentNode || cacheEntries.length === 0) return;
    const autoRefresh = document.getElementById('resolver-cache-autorefresh');
    if (autoRefresh && !autoRefresh.checked) return;
    cacheEntries.forEach((entry) => {
        entry.ttl = Math.max(0, (Number(entry.ttl) || 0) - 1);
    });
    renderCache(cacheEntries);
}

function renderMetrics(metrics) {
    const values = metrics || {};
    setElementText('resolver-stat-queries', String(values.queries ?? 0));
    setElementText('resolver-stat-hits', String(values.hits ?? 0));
    setElementText('resolver-stat-hit-ratio', `${values.hit_ratio ?? 0}%`);
    setElementText('resolver-stat-recursion', `${values.recursion_time_ms ?? 0} ms`);
    setElementText('resolver-stat-secure', String(values.dnssec_secure ?? 0));
    setElementText('resolver-stat-bogus', String(values.dnssec_bogus ?? 0));
    setElementText('resolver-stat-insecure', String(values.dnssec_insecure ?? 0));
}

function entryKey(entry) {
    return `${entry.name}|${entry.type}|${entry.data}`;
}

function renderCache(entries) {
    const tbody = document.getElementById('resolver-cache-tbody');
    if (!tbody) return;
    patchTableRows(tbody, entries.map((entry) => ({
        key: entryKey(entry),
        cells: [
            entry.name || '—',
            entry.type || '—',
            `${entry.ttl}s`,
            statusCell(tbody, entry),
            validationCell(tbody, entry),
            entry.data || '—',
            flushCell(tbody, entry),
        ],
    })));
}

/** The row's status badge: green HIT for an answer the Resolver can serve
 * from cache, yellow NXDOMAIN/NODATA for a cached denial. The badge carries
 * its own text, so the distinction survives without colour. */
function statusCell(tbody, entry) {
    const key = entryKey(entry);
    const existing = tbody.querySelector(
        `.resolver-status-badge[data-key="${CSS.escape(key)}"]`
    );
    const label = entry.negative ? (entry.status || 'NXDOMAIN') : 'HIT';
    const className = entry.negative
        ? 'resolver-status-badge resolver-status-negative'
        : 'resolver-status-badge resolver-status-hit';
    if (existing) {
        if (existing.textContent !== label) existing.textContent = label;
        if (existing.className !== className) existing.className = className;
        return existing;
    }

    const badge = document.createElement('span');
    badge.className = className;
    badge.dataset.key = key;
    badge.textContent = label;
    if (entry.negative && entry.soa) {
        badge.title = `Cached against the SOA of ${entry.soa} (RFC 2308)`;
    }
    return badge;
}

/** How one cached RRset's validation verdict reads.
 *
 * Unbound records three: ``secure`` for an RRset whose signatures chained back
 * to a trust anchor, ``bogus`` for one whose signatures were present but did
 * not verify, and ``insecure`` for one covered by no signature at all — an
 * unsigned Zone, which is not a failure. Colour separates them, and the badge
 * carries its own word so it survives without colour.
 *
 * Args:
 *     entry: One cache entry from the Resolver cache dump.
 *
 * Returns:
 *     ``{ label, className, title }`` for the badge to render.
 */
export function validationBadge(entry) {
    const security = String((entry && entry.security) || 'insecure').toLowerCase();
    if (security === 'secure') {
        return {
            label: 'SECURE',
            className: 'resolver-validation-badge resolver-validation-secure',
            title: 'Signatures over this RRset verified back to the trust anchor.',
        };
    }
    if (security === 'bogus') {
        return {
            label: 'BOGUS',
            className: 'resolver-validation-badge resolver-validation-bogus',
            title: 'This RRset is signed, but the signatures did not verify.',
        };
    }
    return {
        label: 'INSECURE',
        className: 'resolver-validation-badge resolver-validation-insecure',
        title: 'This RRset is in an unsigned Zone, so there was nothing to verify.',
    };
}

/** The row's validation cell: the verdict badge, plus an AD marker when the
 * Resolver would set the Authenticated Data flag on an answer served from this
 * entry. The marker sits beside the badge rather than replacing it, so the row
 * says both what the Resolver concluded and what a client would see of it. */
function validationCell(tbody, entry) {
    const key = entryKey(entry);
    const badge = validationBadge(entry);
    const ad = Boolean(entry.authenticated_data);
    const existing = tbody.querySelector(
        `.resolver-validation-cell[data-key="${CSS.escape(key)}"]`
    );
    const adFlag = ad ? '1' : '';
    if (existing && existing.dataset.verdict === badge.label
        && existing.dataset.ad === adFlag) {
        return existing;
    }

    const cell = existing || document.createElement('span');
    if (!existing) {
        cell.className = 'resolver-validation-cell';
        cell.dataset.key = key;
    }
    cell.dataset.verdict = badge.label;
    cell.dataset.ad = adFlag;
    cell.replaceChildren();

    const badgeEl = document.createElement('span');
    badgeEl.className = badge.className;
    badgeEl.textContent = badge.label;
    badgeEl.title = badge.title;
    cell.appendChild(badgeEl);

    if (ad) {
        const adEl = document.createElement('span');
        adEl.className = 'resolver-ad-flag';
        adEl.textContent = 'AD';
        adEl.title = 'The Resolver set the Authenticated Data flag on this answer.';
        cell.appendChild(adEl);
    }

    return cell;
}

/** The row's Flush button, reused across ticks so a Learner's focus and a
 * screen reader's place in the table survive the countdown. */
function flushCell(tbody, entry) {
    const key = entryKey(entry);
    const existing = tbody.querySelector(
        `.resolver-flush-btn[data-key="${CSS.escape(key)}"]`
    );
    if (existing) return existing;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'resolver-row-btn resolver-flush-btn';
    button.dataset.key = key;
    button.textContent = 'Flush';
    // Unbound flushes by name, not by name-and-type, so pressing this evicts
    // every type cached under it — the AAAA denial next to an A record goes
    // too. The label says the name alone rather than promising otherwise.
    button.setAttribute('aria-label', `Flush ${entry.name} from the cache`);
    button.addEventListener('click', () => { void handleFlush(entry.name, 'name'); });
    return button;
}

/** Clear the whole cache.
 *
 * Unbound has no "flush everything" command; the root zone is an ancestor of
 * every cached name, so flushing that subtree is one. Sending no target at all
 * is what asks the server for exactly that (see ResolverQueryService). */
function flushEntireCache() {
    return handleFlush(null, 'zone');
}

async function handleFlush(target, scope) {
    const node = currentNode;
    if (!node) return;

    const hadFocusInTable = Boolean(
        document.activeElement && document.activeElement.closest('#resolver-cache-table')
    );

    try {
        const response = await flushResolverCache(node, target, scope);
        const data = await readJson(response);
        if (!response.ok) {
            setCacheStatus(
                formatResponseError(data, `Request failed (${response.status})`),
                true,
            );
            return;
        }
        setCacheStatus(
            target ? `Flushed ${target}.` : 'Flushed the entire cache.',
            false,
        );
        await refreshCache(true);
        // The row that owned the focused button is gone; without this, focus
        // is stranded on <body> behind the still-open modal.
        if (hadFocusInTable && (!document.activeElement || document.activeElement === document.body)) {
            const flushAll = document.getElementById('resolver-flush-all');
            if (flushAll) flushAll.focus();
        }
    } catch (error) {
        setCacheStatus(`Network error: ${error.message || error}`, true);
    }
}

function setCacheStatus(message, isError) {
    setStatus('resolver-cache-status', message, isError);
}

// ── Access Control ──────────────────────────────────────────────────────────

async function refreshAccess() {
    const node = currentNode;
    if (!node) return;

    let response;
    try {
        response = await getResolverAccess(node);
    } catch (error) {
        if (node === currentNode) {
            setAccessStatus(`Can't reach the server: ${error.message || error}`, true);
        }
        return;
    }

    if (node !== currentNode) return;

    const data = await readJson(response);
    if (node !== currentNode) return;

    if (!response.ok) {
        setAccessStatus(
            formatResponseError(data, `Request failed (${response.status})`),
            true,
        );
        return;
    }

    accessRules = (data && data.rules) || [];
    setAccessStatus(
        accessRules.length === 0 ? 'No Access Control rules are in force.' : '',
        false,
    );
    renderAccessRules(accessRules);
}

function renderAccessRules(rules) {
    const tbody = document.getElementById('resolver-access-tbody');
    if (!tbody) return;
    patchTableRows(tbody, rules.map((rule) => ({
        key: rule.subnet,
        cells: [
            rule.subnet,
            rule.action,
            ruleActionsCell(tbody, rule),
        ],
    })));
}

function ruleActionsCell(tbody, rule) {
    const existing = tbody.querySelector(
        `.resolver-rule-actions[data-key="${CSS.escape(rule.subnet)}"]`
    );
    if (existing && existing.dataset.action === rule.action) return existing;

    const cell = existing || document.createElement('div');
    if (!existing) {
        cell.className = 'resolver-rule-actions';
        cell.dataset.key = rule.subnet;
    }
    cell.dataset.action = rule.action;
    cell.replaceChildren();

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'resolver-row-btn';
    editBtn.textContent = 'Edit';
    editBtn.setAttribute('aria-label', `Edit the rule for ${rule.subnet}`);
    editBtn.addEventListener('click', (event) => startEditRule(rule, event.currentTarget));
    cell.appendChild(editBtn);

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'resolver-row-btn resolver-row-delete-btn';
    deleteBtn.textContent = 'Delete';
    deleteBtn.setAttribute('aria-label', `Delete the rule for ${rule.subnet}`);
    deleteBtn.addEventListener('click', () => { void handleRuleDelete(rule); });
    cell.appendChild(deleteBtn);

    return cell;
}

function startEditRule(rule, triggerBtn = null) {
    editTriggerButton = triggerBtn || document.activeElement;
    editingRule = { ...rule };
    const subnet = document.getElementById('resolver-access-subnet');
    const action = document.getElementById('resolver-access-action');
    if (subnet) subnet.value = rule.subnet || '';
    if (action) action.value = rule.action || 'allow';
    const submit = document.getElementById('resolver-access-submit');
    if (submit) submit.textContent = 'Save Rule';
    const cancel = document.getElementById('resolver-access-cancel');
    if (cancel) cancel.hidden = false;
    showAccessError('');
    if (subnet) subnet.focus();
}

function resetAccessForm() {
    const cancel = document.getElementById('resolver-access-cancel');
    const wasCancelFocused = cancel && document.activeElement === cancel;
    editingRule = null;
    const form = document.getElementById('resolver-access-form');
    if (form) form.reset();
    const action = document.getElementById('resolver-access-action');
    if (action) action.value = 'allow';
    const submit = document.getElementById('resolver-access-submit');
    if (submit) submit.textContent = 'Add Rule';
    if (cancel) cancel.hidden = true;
    if (wasCancelFocused) {
        if (editTriggerButton && document.contains(editTriggerButton)) {
            editTriggerButton.focus();
        } else {
            const subnet = document.getElementById('resolver-access-subnet');
            if (subnet) subnet.focus();
        }
    }
    editTriggerButton = null;
}

/** Reject a malformed CIDR before the round trip, so the common typo is
 * named on the form rather than coming back as a 400. The server validates
 * again — ipaddress is the authority, this is only the fast path. */
function validateSubnet(subnet) {
    if (!subnet) return 'A client Subnet in CIDR notation is required.';
    const [address, prefix, ...rest] = subnet.split('/');
    if (rest.length > 0 || prefix === undefined || prefix === '') {
        return `Subnet must carry a prefix length, e.g. "${address || '10.0.5.0'}/24".`;
    }
    if (!/^\d+$/.test(prefix)) {
        return `Prefix length must be a whole number, got "${prefix}".`;
    }
    const isIpv6 = address.includes(':');
    const maxPrefix = isIpv6 ? 128 : 32;
    if (Number(prefix) > maxPrefix) {
        return `Prefix length must be at most /${maxPrefix} for this address family.`;
    }
    if (isIpv6) {
        if (!/^[0-9a-fA-F:]+$/.test(address)) return `Not a valid IPv6 address: "${address}".`;
        return null;
    }
    const octets = address.split('.');
    if (octets.length !== 4 || !octets.every((o) => /^\d{1,3}$/.test(o) && Number(o) <= 255)) {
        return `Not a valid IPv4 address: "${address}".`;
    }
    return null;
}

/** The policy the form's current state describes, in match order.
 *
 * A Subnet carries at most one rule — Unbound would never reach a second — so
 * re-declaring one replaces it where it already sits rather than appending a
 * duplicate, and the table does not jump under a Learner who only changed an
 * action. An edit that renames a Subnet onto one that already has a rule
 * collapses the two, for the same reason. */
function policyWith(subnet, action) {
    const replacing = editingRule ? editingRule.subnet : null;
    const rule = { subnet, action };
    const next = [];
    let placed = false;
    accessRules.forEach((existing) => {
        if (existing.subnet !== replacing && existing.subnet !== subnet) {
            next.push(existing);
            return;
        }
        if (!placed) {
            next.push(rule);
            placed = true;
        }
    });
    if (!placed) next.push(rule);
    return next;
}

async function handleAccessSubmit() {
    const node = currentNode;
    if (!node) return;

    const subnet = document.getElementById('resolver-access-subnet')?.value.trim() || '';
    const action = document.getElementById('resolver-access-action')?.value || 'allow';

    const validationError = validateSubnet(subnet);
    if (validationError) {
        showAccessError(validationError);
        return;
    }

    const wasEditing = Boolean(editingRule);
    const submit = document.getElementById('resolver-access-submit');
    if (submit) {
        submit.disabled = true;
        submit.textContent = wasEditing ? 'Saving…' : 'Adding…';
    }
    try {
        await applyPolicy(policyWith(subnet, action), wasEditing
            ? 'Could not save the rule'
            : 'Could not add the rule');
    } finally {
        if (submit) {
            submit.disabled = false;
            submit.textContent = editingRule ? 'Save Rule' : 'Add Rule';
        }
    }
}

async function handleRuleDelete(rule) {
    const node = currentNode;
    if (!node) return;

    const hadFocusInTable = Boolean(
        document.activeElement && document.activeElement.closest('#resolver-access-table')
    );

    const remaining = accessRules.filter((candidate) => candidate.subnet !== rule.subnet);
    const applied = await applyPolicy(remaining, 'Could not delete the rule');
    if (!applied) return;

    if (editingRule && editingRule.subnet === rule.subnet) resetAccessForm();
    if (hadFocusInTable && (!document.activeElement || document.activeElement === document.body)) {
        const subnetInput = document.getElementById('resolver-access-subnet');
        if (subnetInput) subnetInput.focus();
    }
}

/** PUT one whole policy and adopt whatever the daemon ended up enforcing.
 *
 * A rejected reload leaves the previous rules in force (see
 * ResolverAccessService.set_rules), so on failure the panel re-reads rather
 * than keeping the policy it optimistically built. */
async function applyPolicy(rules, fallbackMessage) {
    const node = currentNode;
    if (!node) return false;

    try {
        const response = await setResolverAccess(node, rules);
        const data = await readJson(response);
        if (node !== currentNode) return false;
        if (!response.ok) {
            showAccessError(formatResponseError(
                data, `${fallbackMessage} (${response.status})`
            ));
            void refreshAccess();
            return false;
        }
        showAccessError('');
        resetAccessForm();
        accessRules = (data && data.rules) || [];
        setAccessStatus(
            accessRules.length === 0 ? 'No Access Control rules are in force.' : '',
            false,
        );
        renderAccessRules(accessRules);
        // A reload restarts Unbound's counters while keeping every cached
        // entry, so the metric cards are stale the moment a rule lands.
        void refreshCache(true);
        return true;
    } catch (error) {
        showAccessError(`Network error: ${error.message || error}`);
        return false;
    }
}

function showAccessError(message) {
    const banner = document.getElementById('resolver-access-error');
    if (!banner) return;
    banner.textContent = message;
    banner.hidden = !message;
    if (message) banner.focus();
}

function setAccessStatus(message, isError) {
    setStatus('resolver-access-status', message, isError);
}

// ── Shared helpers ──────────────────────────────────────────────────────────

function setStatus(id, message, isError) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('resolver-status-error', Boolean(isError && message));
}

/** Adopt the policy another session put in force, last-write-wins. An edit in
 * progress is abandoned rather than silently re-targeted, because the rule it
 * was editing may no longer exist. */
export function handleResolverAccessBroadcast(data) {
    if (!data || !currentNode || data.node !== currentNode) return;
    accessRules = data.rules || [];
    if (editingRule) {
        resetAccessForm();
        setAccessStatus('The policy changed in another session; the edit form was reset.', false);
    } else {
        setAccessStatus(
            accessRules.length === 0 ? 'No Access Control rules are in force.' : '',
            false,
        );
    }
    renderAccessRules(accessRules);
}
