// Bulk Stop uses the existing keyed lifecycle endpoint. Capture the confirmed
// keys so a later start (even for the same Group) cannot be stopped by it.
import { appState } from '../state/app-state.js';
import { getDashboard, stopLabByKey } from '../services/api.js';
import { showConfirmModal } from '../utils/modal.js';

let busy = false;
let runningCount = 0;
let failedLabs = [];
let revision = 0;

export function bindStopAllLabs(refresh) {
    document.getElementById('overview-stop-all').addEventListener('click', () => confirmStops(null, refresh));
    document.getElementById('overview-stop-all-retry').addEventListener('click', () => confirmStops(failedLabs, refresh));
}

export function syncStopAllLabs(dashboard) {
    runningCount = dashboard.labs.length;
    updateControls();
}

function updateControls() {
    document.getElementById('overview-stop-all').disabled = busy || !runningCount;
    const retry = document.getElementById('overview-stop-all-retry');
    retry.hidden = !failedLabs.length;
    retry.disabled = busy;
}

export function resetStopAllLabs() {
    revision++;
    busy = false;
    failedLabs = [];
    runningCount = 0;
    document.getElementById('overview-stop-all-status').textContent = '';
    document.getElementById('overview-stop-all-feedback').hidden = true;
    updateControls();
}

function report(message) {
    document.getElementById('overview-stop-all-feedback').hidden = false;
    document.getElementById('overview-stop-all-status').textContent = message;
}

async function confirmStops(retryLabs, refresh) {
    if (busy || appState.currentUserRole !== 'instructor' || appState.labMode !== 'multi') return;
    const current = revision;
    busy = true;
    updateControls();
    try {
        const response = await getDashboard();
        if (!response.ok) throw new Error('Dashboard unavailable');
        const dashboard = await response.json();
        if (current !== revision) return;
        runningCount = dashboard.labs.length;
        const labs = dashboard.labs.filter(lab => !retryLabs || retryLabs.some(failed => failed.key === lab.key));
        if (!labs.length) {
            if (retryLabs) failedLabs = [];
            report('No Labs remain to stop.');
            return;
        }
        const names = labs.map(lab => `Group ${lab.group}: ${dashboard.example_names?.[lab.example] || lab.example}`).join('\n');
        showConfirmModal({
            title: retryLabs ? 'Retry failed stops' : 'Stop all Labs',
            message: `Stop these ${labs.length} running Lab${labs.length === 1 ? '' : 's'}?\n\n${names}\n\nAll their Terminals will close and temporary Lab configurations will be lost. Saved Router Config Copies will remain. Users will stay signed in. Keep this page open until stops finish.`,
            confirmText: retryLabs ? 'Retry stops' : 'Stop all Labs',
            onConfirm: () => {
                if (current === revision) void stopLabs(labs, refresh, current);
            },
        });
    } catch (_) {
        if (current === revision) report('Could not load running Labs. Please try again. No stop requests were sent.');
    } finally {
        if (current === revision) {
            busy = false;
            updateControls();
        }
    }
}

async function stopLabs(labs, refresh, current) {
    if (busy) return;
    busy = true;
    failedLabs = [];
    updateControls();
    let stopped = 0;
    for (const [index, lab] of labs.entries()) {
        if (current !== revision) return; // Logout cancels requests not yet sent.
        report(`Stopping Labs: ${index} of ${labs.length} complete · Group ${lab.group}…`);
        try {
            const response = await stopLabByKey(lab.key);
            const data = await response.json().catch(() => ({}));
            // A second Instructor may have stopped this exact key already.
            if (!response.ok && response.status !== 404
                && !(response.status === 409 && data.detail === 'No lab running')) {
                throw new Error('Stop failed');
            }
            stopped++;
        } catch (_) {
            if (current === revision) failedLabs.push(lab);
        }
    }
    if (current !== revision) return;
    report(`Stopped ${stopped} of ${labs.length} Labs.` + (failedLabs.length
        ? ` Could not confirm stops for Groups: ${failedLabs.map(lab => lab.group).join(', ')}. Retry failed stops to check again.` : ''));
    busy = false;
    updateControls();
    await refresh();
}
