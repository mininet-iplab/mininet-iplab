import { appState } from '../state/app-state.js';

// Two feeds. The Labs feed (/ws/labs) says which Lab is alive and is held by
// every signed-in browser, so a Lab started or stopped elsewhere reaches
// this one; the header indicator and the polling fallback belong to it. A
// Lab's feed (/ws/labs/{key}/topology) carries that Lab's Topology and
// Service updates and lives only while the page is on that Lab.

// Reflect the Labs feed's state in the visible, aria-live header indicator
// so a dropped connection is not silent. States: connected,
// disconnected/reconnecting, and fell-back-to-polling.
function updateConnectionStatus(state, text) {
    const indicator = document.getElementById('ws-status');
    if (!indicator) return;
    const dot = indicator.querySelector('.ws-status-dot');
    const label = indicator.querySelector('.ws-status-text');
    if (dot) dot.dataset.state = state;
    if (label) label.textContent = text;
}

function wsBase() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}`;
}

function isLive(ws) {
    return !!ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
}

function dispatch(event) {
    try {
        const message = JSON.parse(event.data);
        if (appState.wsMessageHandler) {
            appState.wsMessageHandler(message);
        }
    } catch (error) {
        console.error('Failed to parse WebSocket message:', error);
    }
}

export function connectLabsWebSocket() {
    if (isLive(appState.labsWs)) return;

    console.log('Connecting to Labs WebSocket...');
    appState.labsWs = new WebSocket(`${wsBase()}/ws/labs`);
    updateConnectionStatus('connecting', 'Connecting…');

    appState.labsWs.onopen = () => {
        console.log('Labs WebSocket connected');
        appState.wsReconnectAttempts = 0;
        updateConnectionStatus('connected', 'Connected');
    };

    appState.labsWs.onmessage = dispatch;

    appState.labsWs.onerror = (error) => {
        console.error('Labs WebSocket error:', error);
    };

    appState.labsWs.onclose = (event) => {
        console.log('Labs WebSocket closed');
        appState.labsWs = null;
        // 1008 is the server refusing this page's Session, which no retry
        // brings back; anything else is a dropped connection worth retrying.
        if (event.code === 1008) {
            handleSessionLost();
            return;
        }
        attemptWebSocketReconnect();
    };
}

// The page's Session is gone — the server restarted and forgot it, or it was
// expired or ended. Every request it makes would now be refused, so stop them
// all at once: drop both feeds and reload, which lands on the login page with
// no poller left running. Only a signed-in page acts, and only once, however
// many refusals arrive together; a page still checking its Session on load
// leaves the refusal to checkAuthentication.
export function handleSessionLost() {
    if (!appState.isAuthenticated) return;
    appState.isAuthenticated = false;

    closeLabWebSocket();
    const labsWs = appState.labsWs;
    appState.labsWs = null;
    if (labsWs) {
        labsWs.onclose = null;
        labsWs.close();
    }

    window.location.reload();
}

// Subscribe to the current Lab's feed, if not already on it. Called whenever
// status says a Lab is running; a feed for a different key is dropped first.
export function connectLabWebSocket() {
    const key = appState.labKey;
    if (!key) return;
    if (isLive(appState.labWs) && appState.labWs.labKey === key) return;
    closeLabWebSocket();

    const ws = new WebSocket(`${wsBase()}/ws/labs/${encodeURIComponent(key)}/topology`);
    ws.labKey = key;
    appState.labWs = ws;

    ws.onmessage = dispatch;

    ws.onerror = (error) => {
        console.error('Lab WebSocket error:', error);
    };

    ws.onclose = (event) => {
        if (appState.labWs === ws) appState.labWs = null;
        // The server closes the feed cleanly when the Lab ends, or refuses a
        // key that names no Lab; only a dropped connection to a Lab this page
        // is still on is worth reconnecting.
        const dropped = event.code !== 1000 && event.code !== 1008;
        if (dropped && appState.labKey === key) {
            setTimeout(connectLabWebSocket, appState.wsReconnectDelay);
        }
    };
}

export function closeLabWebSocket() {
    const ws = appState.labWs;
    appState.labWs = null;
    if (ws) {
        ws.onclose = null;
        ws.close();
    }
}

export function sendPong() {
    if (appState.labsWs && appState.labsWs.readyState === WebSocket.OPEN) {
        appState.labsWs.send(JSON.stringify({ type: 'pong' }));
    }
}

export function attemptWebSocketReconnect() {
    if (appState.wsReconnectAttempts >= appState.wsMaxReconnectAttempts) {
        console.error('Max WebSocket reconnection attempts reached. Falling back to polling.');
        updateConnectionStatus('polling', 'Polling');
        // Fallback to polling if WebSocket fails
        setInterval(() => {
            if (appState.wsFallbackHandler) {
                appState.wsFallbackHandler();
            }
        }, 5000);
        return;
    }

    appState.wsReconnectAttempts++;
    const delay = appState.wsReconnectDelay * Math.pow(2, appState.wsReconnectAttempts - 1);

    console.log(`Reconnecting WebSocket in ${delay}ms (attempt ${appState.wsReconnectAttempts}/${appState.wsMaxReconnectAttempts})`);
    updateConnectionStatus('reconnecting', 'Reconnecting…');

    setTimeout(() => {
        connectLabsWebSocket();
    }, delay);
}
