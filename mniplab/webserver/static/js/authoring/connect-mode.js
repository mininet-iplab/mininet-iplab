// Connect mode: the keyboard and single-pointer way to link two Nodes
// (WCAG 2.5.7). Connect… names the first Node; the next one chosen on the
// canvas becomes the other end of a new Link.

let context = null;
let source = null;
let returnFocus = null;

/** The Node being connected, or null outside connect mode. */
export function connectSource() {
    return source;
}

function leave() {
    source = null;
    document.getElementById('connect-bar').hidden = true;
    document.getElementById('canvas-region').classList.remove('connecting');
    context.redraw();
}

/**
 * Enter connect mode from a Node, and move focus to the nearest other Node.
 *
 * @param {string} nodeId The Node the new Link starts from.
 * @param {Element} trigger Where focus returns if connecting is cancelled.
 */
export function startConnect(nodeId, trigger) {
    source = nodeId;
    returnFocus = trigger;
    const name = context.nodeName(nodeId);
    const bar = document.getElementById('connect-bar');
    bar.setAttribute('aria-label', `Connect ${name}`);
    document.getElementById('connect-message').textContent = `Connect ${name} to… choose a Node · Esc cancels`;
    bar.hidden = false;
    document.getElementById('canvas-region').classList.add('connecting');
    context.redraw();
    context.announce(`Connect ${name} to… choose a Node, or press Escape to cancel.`);
    context.focusNearest(nodeId);
}

/**
 * Link the connecting Node to this one, then focus the new Link.
 *
 * @param {string} nodeId The chosen Node.
 */
export function pickConnectTarget(nodeId) {
    if (nodeId === source) {
        context.announce(`Choose another Node to connect ${context.nodeName(source)} to.`);
        return;
    }
    const from = source;
    leave();
    context.focusLink(context.link(from, nodeId).id);
}

// Leave connect mode and return focus to what entered it.
function cancelConnect() {
    const from = source;
    leave();
    context.announce(`Stopped connecting ${context.nodeName(from)}.`);
    (returnFocus?.isConnected && !returnFocus.closest('[hidden]') ? returnFocus : context.nodeElement(from))?.focus();
}

/** Leave connect mode without a word, as when another draft opens or the Selection acts. */
export function endConnect() {
    if (source) leave();
}

/**
 * Bind the connect bar and Escape.
 *
 * @param {object} options
 * @param {(nodeId: string) => string} options.nodeName
 * @param {(nodeId: string) => ?Element} options.nodeElement The Node on the canvas.
 * @param {(nodeId: string) => void} options.focusNearest Focus the nearest other Node.
 * @param {(firstId: string, secondId: string) => object} options.link Add a Link; returns it.
 * @param {(linkId: string) => void} options.focusLink Focus a Link on the canvas.
 * @param {() => void} options.redraw Redraw the canvas for the mode.
 * @param {(message: string) => void} options.announce Say what happened.
 */
export function initConnectMode(options) {
    context = options;
    document.getElementById('connect-cancel').addEventListener('click', cancelConnect);
    // Escape leaves connect mode before anything else hears it, such as
    // undoing a placement.
    window.addEventListener('keydown', event => {
        // A dialog opened meanwhile hears its own Escape.
        if (event.key !== 'Escape' || !source || document.querySelector('dialog[open]')) return;
        event.preventDefault();
        event.stopPropagation();
        cancelConnect();
    }, true);
}
