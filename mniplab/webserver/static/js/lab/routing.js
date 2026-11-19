// Keeps the page URL in sync with the caller's own running Lab, so a link
// an Instructor hands a Learner opens that Lab directly, a reload stays on
// it, and another tab lands in the same place. The server's status answer
// is always the source of truth (a Group has one Lab); this module never
// picks a Lab, it only mirrors the URL to whichever key `rememberLab`
// settles on, and reads a URL a page was loaded with back into that first
// settling.

const LAB_PATH = /^\/labs\/([^/]+)\/?$/;

// The Lab key the page was loaded with, or null off `/` or an unrecognized
// path. Read once; `rememberLab` corrects the URL to match reality
// regardless of whether this named the caller's actual Lab.
export function labKeyFromLocation() {
    const match = LAB_PATH.exec(window.location.pathname);
    return match ? decodeURIComponent(match[1]) : null;
}

// Mirror `key` into the URL: `/labs/{key}` when a Lab is bound, `/`
// otherwise. A no-op when the URL already matches, so this can be called on
// every status update without littering `history`.
export function syncUrlToLabKey(key) {
    if (typeof window.history === 'undefined' || typeof window.history.replaceState !== 'function') return;
    const target = key ? `/labs/${encodeURIComponent(key)}` : '/';
    if (window.location.pathname === target) return;
    window.history.replaceState(window.history.state, '', target + window.location.search);
}
