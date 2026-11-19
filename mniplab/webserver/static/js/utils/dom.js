// Safe DOM helpers

/** XSS-safe HTML escape */
export function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

/** Simple element selector helper that returns null instead of throwing */
export function $(selector, context = document) {
    return context.querySelector(selector);
}

/** Simple elements selector helper */
export function $$(selector, context = document) {
    return context.querySelectorAll(selector);
}

/** The current Lab Example ID, kept separate from its displayed name. */
export function getCurrentLabName() {
    const lab = document.getElementById('lab-name');
    return lab?.dataset.example || lab?.textContent || '';
}

/**
 * Turn a FastAPI error body into one line a Learner can read.
 *
 * FastAPI answers with `detail` in three shapes: a plain string for a raised
 * HTTPException or a ValueError the app maps to 400, a list of per-field
 * objects for a 422 schema rejection, and an object for the structured
 * service errors (Knot's semantic checks). Each is unwrapped to a sentence,
 * falling back to `fallback` when the body carries nothing usable.
 *
 * Args:
 *     data: The parsed JSON body, or null when it did not parse.
 *     fallback: What to say when the body names no reason.
 *
 * Returns:
 *     A single human-readable string, never empty.
 */
export function formatResponseError(data, fallback) {
    const detail = data && data.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail)) {
        const messages = detail.map((item) => {
            if (typeof item === 'string') return item;
            if (item && typeof item.msg === 'string') return item.msg;
            if (item && typeof item.message === 'string') return item.message;
            return JSON.stringify(item);
        });
        return messages.filter(Boolean).join('; ') || fallback;
    }
    if (detail && typeof detail === 'object') {
        if (typeof detail.message === 'string') return detail.message;
        if (typeof detail.error === 'string') return detail.error;
        return JSON.stringify(detail);
    }
    return (data && typeof data.message === 'string' && data.message) || fallback;
}

/**
 * Set an element's text only when it actually changed.
 *
 * A poll that rewrites identical text still counts as a mutation, which some
 * screen readers re-announce; comparing first keeps a steady panel quiet.
 *
 * Args:
 *     id: The element's id.
 *     value: The text it should carry.
 */
export function setElementText(id, value) {
    const el = document.getElementById(id);
    if (el && el.textContent !== value) el.textContent = value;
}

/**
 * Reconcile a table body's rows with a new set of rows, patching in place.
 *
 * Each new row is keyed by ``key``; a row whose key already exists is updated
 * cell-by-cell rather than replaced, and only cells whose text actually
 * changed are touched. Rows that no longer exist are removed, new ones are
 * appended. The point is that an unchanged poll reuses the existing ``<tr>``
 * nodes, so a screen reader does not re-announce a whole table that did not
 * change.
 *
 * Args:
 *     tbody: The ``<tbody>`` whose rows are to be reconciled.
 *     rows: The new rows, in display order. Each is ``{ key, cells }`` where
 *         ``cells`` is the array of cell values for that row. A cell is a
 *         string (set as ``textContent``) or a DOM node (reused in place when
 *         it already occupies that cell, appended otherwise).
 *
 * Returns:
 *     The ``tbody``, for chaining.
 */
export function patchTableRows(tbody, rows) {
    const existing = new Map();
    for (const tr of Array.from(tbody.rows)) {
        if (tr.dataset.rowKey !== undefined) {
            existing.set(tr.dataset.rowKey, tr);
        }
    }

    const seen = new Set();
    rows.forEach((row, index) => {
        seen.add(row.key);
        let tr = existing.get(row.key);
        if (!tr) {
            tr = document.createElement('tr');
            tr.dataset.rowKey = row.key;
            tbody.appendChild(tr);
        }
        row.cells.forEach((value, cellIndex) => {
            let td = tr.children[cellIndex];
            if (!td) {
                td = document.createElement('td');
                tr.appendChild(td);
            }
            if (value instanceof Node) {
                if (td.firstChild !== value) {
                    td.replaceChildren(value);
                }
            } else if (td.textContent !== value) {
                td.textContent = value;
            }
        });
        // Keep DOM order in step with display order without recreating nodes.
        const current = tbody.rows[index];
        if (current !== tr) {
            tbody.insertBefore(tr, current);
        }
    });

    for (const [key, tr] of existing) {
        if (!seen.has(key)) {
            tr.remove();
        }
    }
    return tbody;
}
