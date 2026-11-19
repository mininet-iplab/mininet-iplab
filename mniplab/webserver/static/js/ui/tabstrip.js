// Bounded Lab navigation: Overview, the selected Lab and a searchable Group selector. The Learner's own binding is retained without a redundant visible tab.
// Selecting a Group tab binds the page to that Group's Lab; Overview shows
// the class-wide listing and leaves the page bound to whatever Lab it is on.

import { createGroupPicker } from './group-picker.js';
import { appState } from '../state/app-state.js';
import { getDashboard, getLab, getStatus } from '../services/api.js';
import { updateStatusFromData, loadTopology } from '../lab/lifecycle.js';
import { closeWorkspaceMonitor, syncWorkspaceView } from './workspace-view.js';
import { labKeyFromLocation, syncUrlToLabKey } from '../lab/routing.js';
import { inspectGroup } from '../lab/instructor-dashboard.js';
import { renderInstructorConnections } from '../lab/instructor-connections.js';

const tabs = [];
let keyboardBound = false;
let groups = [];
let currentTab = null;
let groupPicker = null;
let bindingRevision = 0;
let bindingUpdate = Promise.resolve();


export async function startLabTabstrip() {
    const requestedKey = labKeyFromLocation();
    const strip = document.getElementById('lab-tabstrip');
    if (!strip) return;
    strip.classList.toggle('hidden', appState.currentUserRole !== 'instructor');
    tabs.length = 0;
    groups = [];
    currentTab = null;
    groupPicker?.destroy();
    groupPicker = null;
    strip.textContent = '';
    const tablist = document.createElement('div');
    tablist.className = 'lab-navigation-tabs';
    tablist.setAttribute('role', 'tablist');
    tablist.setAttribute('aria-label', 'Labs');
    strip.appendChild(tablist);
    if (appState.currentUserRole === 'instructor') {
        tabs.push(makeTab('overview', 'Overview', 'instructor-dashboard', undefined));
        tablist.appendChild(tabs[0]);
        // Overview needs no Group lookup. Select it before the dashboard
        // round-trip so a reload never paints the own-Lab workspace first.
        if (!requestedKey) markSelected(tabs[0]);
        try {
            const res = await getDashboard();
            groups = res.ok ? (await res.json()).groups || [] : [];
        } catch (_) { groups = []; }
        currentTab = makeTab('current', 'Selected Lab', 'lab-tabpanel', '');
        currentTab.hidden = true;
        tabs.push(currentTab);
    } else {
        tabs.push(makeTab(appState.currentGroup, appState.currentGroup, 'lab-tabpanel', undefined));
    }
    for (const tab of tabs) tablist.appendChild(tab);
    initTabstripKeyboard(strip);
    if (appState.currentUserRole === 'instructor') {
        const controls = document.createElement('div');
        controls.className = 'group-navigation-controls';
        groupPicker = createGroupPicker({
            id: 'group-navigation-select', label: 'Select Group',
            placeholder: 'Find or select a Group…', onSelect: selectGroupTab,
        });
        controls.append(groupPicker.element);
        strip.appendChild(controls);
        filterGroupOptions();
    }
    const requested = groups.find(group => requestedKey && group.lab?.key === requestedKey);
    if (requested && currentTab) selectGroupTab(requested.name);
    else markSelected(tabs[0]);
}

export function stopLabTabstrip() {
    bindingRevision++;
    groupPicker?.destroy();
    groupPicker = null;
    const strip = document.getElementById('lab-tabstrip');
    if (strip) strip.classList.add('hidden');
    tabs.length = 0;
    groups = [];
    currentTab = null;
}

// Keep each Group tab's key in step with the dashboard poll: a Group that
// starts (or loses) its Lab between polls gets its tab's key corrected.
export function updateLabTabs(dashboard) {
    groups = dashboard?.groups || [];
    filterGroupOptions();
    if (!currentTab || currentTab.hidden) return;
    const group = groups.find(item => item.name === currentTab.dataset.labGroup);
    const key = group?.lab?.key || '';
    if (!key) currentTab.hidden = true;
    if (currentTab.getAttribute('aria-selected') === 'true' && !key) {
        const groupName = currentTab.dataset.labGroup;
        selectTab(tabs[0]);
        const revision = bindingRevision;
        bindingUpdate = bindingUpdate.then(async () => {
            if (revision === bindingRevision) await updateStatusFromData({ running: false });
        });
        inspectGroup(groupName);
    } else if (currentTab.getAttribute('aria-selected') === 'true' && key !== currentTab.dataset.labKey) {
        currentTab.dataset.labKey = key;
        appState.boundGroup = group.name;
        void bindToLab(key);
    }
    currentTab.dataset.labKey = key;
}

function filterGroupOptions() {
    groupPicker?.setOptions([...groups]
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
        .map(group => ({ value: group.name, label: group.name,
            description: group.lab ? 'Lab running' : 'No Lab running' })));
}

export function selectGroupTab(groupName) {
    if (groupName === 'overview') {
        selectTab(tabs[0]);
        return;
    }
    const group = groups.find(item => item.name === groupName);
    if (!group || !currentTab) return;
    groupPicker?.select(groupName);
    if (!group.lab) {
        selectTab(tabs[0]);
        inspectGroup(groupName);
        return;
    }
    currentTab.hidden = false;
    currentTab.textContent = groupName;
    currentTab.dataset.labGroup = groupName;
    currentTab.dataset.labKey = group.lab.key;
    return selectTab(currentTab);
}

function makeTab(id, label, controls, labKey) {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'lab-tab';
    tab.setAttribute('role', 'tab');
    tab.id = `lab-tab-${id}`;
    tab.dataset.labGroup = id;
    tab.setAttribute('aria-selected', 'false');
    tab.setAttribute('aria-controls', controls);
    tab.tabIndex = -1;
    tab.textContent = label;
    if (labKey !== undefined) tab.dataset.labKey = labKey;
    tab.addEventListener('click', () => selectTab(tab));
    return tab;
}

function initTabstripKeyboard(strip) {
    if (keyboardBound) return;
    strip.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        if (!event.target.matches('[role=tab]')) return;
        event.preventDefault();
        const available = tabs.filter(tab => !tab.hidden);
        const currentIndex = available.indexOf(document.activeElement);
        if (currentIndex === -1) return;
        const delta = event.key === 'ArrowRight' ? 1 : -1;
        const next = available[(currentIndex + delta + available.length) % available.length];
        next.focus();
        next.click();
        next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    });
    keyboardBound = true;
}

function markSelected(tab) {
    closeWorkspaceMonitor();
    for (const t of tabs) {
        t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
        t.tabIndex = t === tab ? 0 : -1;
    }
    // Show exactly the selected tab's panel. markSelected runs for the
    // initial selection too (startLabTabstrip), so the panel visibility
    // cannot ride only on the click handler: on Instructor login the
    // Overview is selected and the Lab panel must already be hidden.
    const overview = document.getElementById('instructor-dashboard');
    const main = document.getElementById('main');
    if (tab.dataset.labGroup === 'overview') {
        syncUrlToLabKey(null);
        // The Overview claims the space between the tab strip and the footer;
        // #main (flex:1 1 0) must be collapsed or it steals the height and the
        // Overview's internal scroll never engages. Collapse to zero height
        // rather than display:none: the modal overlays (assignment, tools,
        // etc.) live inside #lab-tabpanel → #main, and display:none
        // would hide them too. A zero-height, overflow-hidden box still lets
        // the position:fixed modals reach the viewport.
        if (overview) overview.style.display = '';
        if (main) {
            main.style.flex = '0 0 0';
            main.style.height = '0';
            main.style.overflow = 'hidden';
            main.style.padding = '0';
        }
    } else {
        if (overview) overview.style.display = 'none';
        if (main) {
            main.style.flex = '';
            main.style.height = '';
            main.style.overflow = '';
            main.style.padding = '';
        }
        const labPanel = document.getElementById('lab-tabpanel');
        if (labPanel) {
            labPanel.style.display = '';
            labPanel.setAttribute('aria-labelledby', tab.id);
        }
    }
    syncWorkspaceView();
    renderInstructorConnections();
}

async function selectTab(tab) {
    const revision = ++bindingRevision;
    if (tab.dataset.labGroup !== 'overview') groupPicker?.select(tab.dataset.labGroup);
    if (tab.dataset.labGroup === 'overview') {
        // Back on the Instructor's own Lab: the Labs feed is authoritative
        // again, so lift the binding guard.
        appState.boundGroup = null;
        markSelected(tab);
        return false;
    }
    const key = tab.dataset.labKey;
    // The Instructor viewing another Group's *running* Lab: mark the binding
    // so the Labs feed (which reports only the viewer's OWN Lab) cannot
    // clobber the key. A stopped Group (no key) and a Learner's own Lab
    // leave the feed authoritative, so no guard.
    appState.boundGroup = (appState.currentUserRole === 'instructor' && key)
        ? tab.dataset.labGroup : null;
    markSelected(tab);
    if (key === undefined) return bindToLab(null, revision); // Learner: own Lab
    if (key) return bindToLab(key, revision); // that Group's Lab
    await updateStatusFromData({ running: false });
    return false;
}

async function bindToLab(key, revision = ++bindingRevision) {
    let data;
    try {
        const res = key ? await getLab(key) : await getStatus();
        data = res.ok ? await res.json() : { running: false };
        if (key && res.ok) data.running = true; // the Lab row omits it
    } catch (_) {
        return false; // network error — stay put
    }
    // A slow response must never replace a more recently selected Group.
    // Serialize lifecycle updates too: they await Guide/sidebar work internally.
    bindingUpdate = bindingUpdate.catch(() => {}).then(async () => {
        if (revision !== bindingRevision) return;
        const targetKey = data.running ? (data.key || null) : null;
        const switching = appState.isLabRunning && appState.labKey !== targetKey;
        if (switching) await updateStatusFromData({ running: false });
        if (revision !== bindingRevision) return;
        await updateStatusFromData(data);
        if (revision === bindingRevision && data.running) loadTopology();
    });
    await bindingUpdate;
    return revision === bindingRevision && !!data.running && appState.labKey === key;
}
