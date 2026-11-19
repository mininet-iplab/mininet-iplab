"""Clipboard input through xterm, WebSockets and a real running Lab shell.

Chromium drives both application input paths by varying its user agent. Native
macOS Safari shortcuts still require a Safari host; no WebKit install is needed
to preserve this event-routing regression in the integration suite.
"""

import json
from typing import Any

import pytest

from mniplab.tests.authoring_editors import ClassroomFixture


class TerminalClipboardLabTests(ClassroomFixture):
    """Pasting and typing reach a real Host exactly once in both input paths."""

    pytestmark = [pytest.mark.integration, pytest.mark.requires_root,
                  pytest.mark.requires_browser]
    real_labs = True

    def test_paste_and_typing_reach_the_shell_once_in_both_browser_paths(self) -> None:
        """Safari paste uses the same normalized xterm data path as Chromium."""
        assert self.browser is not None
        control = self.session_page("instructor1", "instructor")
        response = control.request.post(
            self.base_url + "/api/labs",
            data={"group": "instructor1", "example_id": "static-lab"},
        )
        self.assertEqual(response.status, 200, response.text())
        key = response.json()["key"]
        safari_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"
        )
        for safari in (False, True):
            with self.subTest(safari=safari):
                page = self.session_page("instructor1", "instructor")
                if safari:
                    page.add_init_script(
                        "Object.defineProperty(navigator, 'userAgent', "
                        f"{{get: () => {json.dumps(safari_agent)}}});"
                    )
                inputs: list[str] = []

                def record_socket(socket: Any) -> None:
                    def record_frame(frame: str) -> None:
                        message = json.loads(frame)
                        if message.get("type") == "input":
                            inputs.append(message["data"])
                    socket.on("framesent", record_frame)

                page.on("websocket", record_socket)
                page.goto(self.base_url + f"/labs/{key}")
                page.wait_for_selector("#app:not(.hidden)")
                page.evaluate("""async () => {
                    const {appState} = await import('/static/js/state/app-state.js');
                    appState.tabManager.openTerminal('h1');
                    window.clipboardTestTerminal = appState.tabManager.terminals.get('h1');
                }""")
                textarea = page.locator("#terminal-h1 .xterm-helper-textarea")
                textarea.wait_for(state="attached")
                page.wait_for_function("""() => {
                    const d = window.clipboardTestTerminal;
                    return d.ws?.readyState === 1 &&
                        d.term.buffer.active.getLine(d.term.buffer.active.baseY + d.term.buffer.active.cursorY)
                            ?.translateToString().includes('h1#');
                }""")
                sources = ("typing", "paste", "keypress", "input", "late-keypress", "late-input") if safari else ("typing", "paste")
                for source in sources:
                    marker = f"CLIPBOARD_{safari}_{source}_café"
                    command = f"printf '%s\\n' '{marker}'"
                    inputs.clear()
                    textarea.focus()
                    if source == "paste":
                        # Keep this exact-byte assertion independent of the
                        # launching shell's bracketed-paste preference.
                        page.evaluate("""() => new Promise(done =>
                            window.clipboardTestTerminal.term.write('\u001b[?2004l', done))
                        """)
                        textarea.evaluate("""(element, text) => {
                            const data = new DataTransfer();
                            data.setData('text/plain', text);
                            element.dispatchEvent(new ClipboardEvent('paste', {
                                clipboardData: data, bubbles: true, cancelable: true,
                            }));
                        }""", command)
                    elif source == "typing":
                        for key in command:
                            if safari and not key.isascii():
                                # Playwright inserts non-US text without a
                                # keydown; exercise the Safari keyboard proxy
                                # with the corresponding physical key events.
                                textarea.dispatch_event("keydown", {"key": key})
                                textarea.dispatch_event("keyup", {"key": key})
                            else:
                                page.keyboard.type(key)
                    else:
                        # Safari can deliver a text event after a keydown
                        # already forwarded by its input proxy. Replay both
                        # text-event forms without replacing xterm or the PTY.
                        textarea.evaluate("""(element, {command, source}) => {
                            const keys = [...command];
                            const late = source.startsWith('late-');
                            for (const [index, key] of keys.entries()) {
                                const code = key.charCodeAt(0);
                                element.dispatchEvent(new KeyboardEvent('keydown', {
                                    key, repeat: index > 0 && keys[index - 1] === key,
                                    bubbles: true, cancelable: true,
                                }));
                                if (late) {
                                    element.dispatchEvent(new KeyboardEvent('keyup', {
                                        key, bubbles: true,
                                    }));
                                }
                                if (source.endsWith('keypress')) {
                                    element.dispatchEvent(new KeyboardEvent('keypress', {
                                        key, charCode: code, keyCode: code, which: code,
                                        bubbles: true, cancelable: true,
                                    }));
                                } else {
                                    element.dispatchEvent(new InputEvent('input', {
                                        data: key, inputType: 'insertText',
                                        bubbles: true, cancelable: true,
                                    }));
                                }
                                if (!late && keys[index + 1] !== key) {
                                    element.dispatchEvent(new KeyboardEvent('keyup', {
                                        key, bubbles: true,
                                    }));
                                }
                            }
                        }""", {"command": command, "source": source})
                    page.keyboard.press("Enter")
                    self.assertEqual("".join(inputs), command + "\r", source)
                    page.wait_for_function("""marker => {
                        const b = window.clipboardTestTerminal.term.buffer.active;
                        return Array.from({length: b.length}, (_, i) =>
                            b.getLine(i).translateToString(true)).includes(marker);
                    }""", arg=marker, timeout=3000)
                # Clipboard processing must retain xterm's CRLF normalization
                # and bracketed-paste framing in both browser paths.
                page.evaluate("""() => new Promise(done =>
                    window.clipboardTestTerminal.term.write('\u001b[?2004h', done))
                """)
                inputs.clear()
                textarea.evaluate(r"""element => {
                    const data = new DataTransfer();
                    data.setData('text/plain', "printf 'one'\r\nprintf 'two'\n");
                    element.dispatchEvent(new ClipboardEvent('paste', {
                        clipboardData: data, bubbles: true, cancelable: true,
                    }));
                }""")
                self.assertEqual(inputs, ["\x1b[200~printf 'one'\rprintf 'two'\r\x1b[201~"])
                textarea.press("Control+c")
                page.close()
