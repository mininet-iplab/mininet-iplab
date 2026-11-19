// This browser tab's open Terminals, per Lab, so a reload can reattach them.
// sessionStorage, not localStorage: a Terminal belongs to the tab that opened
// it, and another tab must never take it over.
// Exported so Reset browser preferences can leave it: it is session state,
// not a preference.
export const STORAGE_PREFIX = 'mniplab-terminals:';

/** Forget the Terminals remembered for every Lab, as logging out must. */
export function forgetRememberedTerminals() {
    try {
        Object.keys(sessionStorage)
            .filter(key => key.startsWith(STORAGE_PREFIX))
            .forEach(key => sessionStorage.removeItem(key));
    } catch (e) {
        // Storage may be unavailable (private mode); nothing was kept.
    }
}
