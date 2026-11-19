import { appState } from '../state/app-state.js';
import { ticked } from './renderer.js';
import { showConfirmModal } from '../utils/modal.js';
import { trapFocus } from '../utils/focus-trap.js';

// Dynamic accessible name for a Group: the group's name plus how many Nodes it
// contains, so a screen-reader user can tell which cluster is focused and its
// size without relying on the visual box. Matches the pattern Nodes and Links
// already use (role + a descriptive aria-label).
function groupAccessibleName(d) {
    const count = d.nodes ? d.nodes.length : 0;
    const countText = count === 1 ? '1 node' : `${count} nodes`;
    return `Group ${d.name}, containing ${countText}`;
}

export function loadGroups() {
    try {
        const saved = localStorage.getItem('mniplab-topology-groups');
        if (saved) {
            appState.groups = JSON.parse(saved);
        }
    } catch (e) {
        console.error('Failed to load groups:', e);
    }
}

export function saveGroups() {
    localStorage.setItem('mniplab-topology-groups', JSON.stringify(appState.groups));
}

export function handleCreateGroup() {
    if (appState.selectedNodes.size === 0) {
        showConfirmModal({
            title: 'Selection Required',
            message: 'Please select nodes first (Ctrl+Click on nodes) before creating a group.',
            confirmText: 'OK',
            cancelText: null // Hides cancel button, acting as Alert
        });
        return;
    }

    const modal = document.getElementById('input-modal');
    const input = document.getElementById('input-group-name');
    const confirmBtn = document.getElementById('input-confirm');
    const cancelBtn = document.getElementById('input-cancel');

    if (!modal || !input) return;

    // Clear previous input
    input.value = '';
    modal.style.display = 'flex';

    let trap = null;

    // Define cleanup to remove listeners and release the focus trap
    const cleanup = () => {
        modal.style.display = 'none';
        confirmBtn.removeEventListener('click', onConfirm);
        cancelBtn.removeEventListener('click', onCancel);
        input.removeEventListener('keydown', onKeydown);
        if (trap) {
            trap.release();
            trap = null;
        }
    };

    const onConfirm = () => {
        const name = input.value.trim();
        if (name) {
            createGroup(name);
        }
        cleanup();
    };

    const onCancel = () => {
        cleanup();
    };

    // Enter submits from the name input (its natural "submit" behavior); the
    // Create button confirms natively on Enter. Escape is owned by the focus
    // trap below, so it works from any focused element in the modal — the fix
    // for it dying once focus left the name field.
    const onKeydown = (e) => {
        if (e.key === 'Enter') onConfirm();
    };

    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    input.addEventListener('keydown', onKeydown);

    // Confine focus to the modal while open; Escape (via the trap) closes it.
    trap = trapFocus(modal, { onClose: onCancel });
}

export function createGroup(name) {
    const newGroup = {
        id: 'group-' + Date.now(),
        name: name,
        nodes: Array.from(appState.selectedNodes)
    };

    appState.groups.push(newGroup);
    saveGroups();

    // Clear selection
    appState.selectedNodes.clear();
    import('./interactions.js').then(({ updateSelectionVisuals }) => {
        updateSelectionVisuals();
    });

    // Update view
    updateGroups();
}

export function handleDeleteGroup() {
    if (!appState.selectedGroup) return;

    showConfirmModal({
        title: 'Delete Group',
        message: `Are you sure you want to delete group "${appState.selectedGroup.name}"? The nodes will remain.`,
        confirmText: 'Delete',
        onConfirm: () => {
            appState.groups = appState.groups.filter(g => g.id !== appState.selectedGroup.id);
            appState.selectedGroup = null;
            saveGroups();

            // Disable delete button
            const btn = document.getElementById('delete-group-btn');
            if (btn) {
                btn.disabled = true;
                btn.style.display = 'none';
            }

            updateGroups();
        }
    });
}

export function updateGroups() {
    if (!appState.g) return;

    const layer = appState.g.select('.layer-groups');

    // Bind data
    const groupItems = layer.selectAll('.group-item')
        .data(appState.groups, d => d.id);

    groupItems.exit().remove();

    const enter = groupItems.enter()
        .append('g')
        .attr('class', 'group-item')
        .attr('tabindex', '0')
        .attr('role', 'button')
        .attr('aria-label', groupAccessibleName)
        .on('click', function (event, d) {
            // Select group
            event.stopPropagation();
            selectGroup(d);
        })
        .on('keydown', function (event, d) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectGroup(d);
            }
        })
        .call(d3.drag()
            .on('start', groupDragStarted)
            .on('drag', groupDragged)
            .on('end', groupDragEnded));

    enter.append('rect')
        .attr('class', 'group-box');

    enter.append('text')
        .attr('class', 'group-label')
        .attr('dy', -5);

    appState.groupElements = enter.merge(groupItems);

    // The accessible name depends on the group's node count, which can change
    // (a node added to the group, or the group re-rendered), so re-assert it on
    // every update, not only at enter.
    appState.groupElements.attr('aria-label', groupAccessibleName);

    // Update selection state
    appState.groupElements.select('.group-box')
        .classed('selected', d => appState.selectedGroup && d.id === appState.selectedGroup.id);

    // Update text
    appState.groupElements.select('.group-label')
        .text(d => d.name);

    // Initial position update
    ticked();
}

export function selectGroup(group) {
    appState.selectedGroup = group;

    // Update UI
    const btn = document.getElementById('delete-group-btn');
    if (btn) {
        btn.disabled = false;
        btn.style.display = 'inline-flex';
        btn.title = `Delete group "${group.name}"`;
    }

    updateGroups();
}

export function groupDragStarted(event, d) {
    if (!appState.simulation) return;
    d3.select(this).select('.group-box').classed('dragging', true);
}

export function groupDragged(event, d) {
    // Move all nodes in the group by dx, dy
    const dx = event.dx;
    const dy = event.dy;

    if (appState.simulation) {
        const nodes = appState.simulation.nodes();
        const groupNodeIds = new Set(d.nodes);

        nodes.forEach(node => {
            if (groupNodeIds.has(node.id)) {
                node.fx = (node.fx || node.x) + dx;
                node.fy = (node.fy || node.y) + dy;
                node.x = node.fx;
                node.y = node.fy;
            }
        });

        ticked();
    }
}

export function groupDragEnded(event, d) {
    d3.select(this).select('.group-box').classed('dragging', false);
    // Dragging a group is a Personal Layout edit: the nodes' fx/fy stay in
    // memory only and are never persisted.
}
