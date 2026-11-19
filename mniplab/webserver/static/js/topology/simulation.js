import { appState } from '../state/app-state.js';
import { ticked } from './renderer.js';

export function dragStarted(event, d) {
    // Do not raise() here. Raising re-appends the <g> to its parent, and a
    // mousedown target that leaves the document — even momentarily — makes
    // Chromium drop the click that follows, so no Node except the last one
    // drawn could be clicked. Raise on the first real movement instead.
    d3.select(this).classed('dragging', true);
}

export function dragged(event, d) {
    d3.select(this).raise();
    // Update both position and fixed position
    d.x = event.x;
    d.y = event.y;
    d.fx = event.x;
    d.fy = event.y;

    // Update all elements
    ticked();
}

export function dragEnded(event, d) {
    d3.select(this).classed('dragging', false);
    // Keep node pinned at its dragged position. This is a Personal Layout: it
    // lives only in memory for the life of the page and is never persisted —
    // a reload returns the canvas to the Authored Layout.
    d.fx = event.x;
    d.fy = event.y;
}
