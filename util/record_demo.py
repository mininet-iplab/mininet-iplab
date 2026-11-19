#!/usr/bin/env python3
"""Record the README demo GIF from a real classroom server.

Starts ``python -m mniplab serve`` in Multi-Lab Mode from a temporary
folder, so it never reads or writes this checkout's ``.env``,
``accounts.json``, ``assignments.json``, ``settings.json``,
``group-config/`` or ``authored-labs/``. Four Groups each start a different
Lab Example, four Learner logins work in the background so the Class
Overview shows Sessions and Terminals, and the Instructor's browser is
recorded visiting each Lab. The video is then sped up and converted to a
GIF.

Usage (as root, in the dev container, from the repository root):
    python3 util/record_demo.py
    python3 util/record_demo.py --output /tmp/demo.gif --keep-video /tmp/demo.webm

By default the GIF goes to a new timestamped file in the system temp folder,
never over the README's copy: watch it first, then copy it to
``docs/assets/mininet-iplab-demo.gif`` with the command the script prints.

Needs Playwright with Chromium and ``ffmpeg``. ``gifsicle`` is optional: with
it the GIF is about a third of the size. Re-run after a visible Web UI
change; a step that can no longer find its control fails with the
Playwright error naming it, which is where the script needs updating.
"""

import argparse
import base64
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from playwright.sync_api import ViewportSize

_UTIL_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_UTIL_DIR)
README_DEMO = os.path.join(PROJECT_ROOT, "docs", "assets", "mininet-iplab-demo.gif")

# Group -> (Lab Example it runs, its Assignment, a Node a Learner works on,
# what the Learner types there).
GROUPS: Dict[str, Tuple[str, List[str], str, str]] = {
    "group-a": ("bgp-ispixp-lab", ["bgp-ispixp-lab", "bgp-isp-lab", "bgp-lab"],
                "host hp1", "ping -c 3 10.0.1.1"),
    "group-b": ("ospf-lab", ["ospf-lab", "static-lab"],
                "router r2", "vtysh -c 'show ip ospf neighbor'"),
    "group-c": ("dhcp-lab", ["dhcp-lab", "ospf-lab"], "host h1", "ip -br addr"),
    "group-d": ("dns-lab", ["dns-lab", "dnssec-lab"],
                "host client1", "dig example.com NS +short"),
}
INSTRUCTOR = "instructor"
VIEWPORT: "ViewportSize" = {"width": 1600, "height": 900}
# Waits sped up harder than the rest of the video, in its conversion.
FAST_SPEED = 5.0

# Playwright's video does not draw the mouse; this pointer and click ring do.
CURSOR_SCRIPT = """
(() => {
  const add = () => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'demo-cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 20 L9 15 L12.5 22 L15.5 20.5 L12 13.5 L19 13.5 Z" fill="#111" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647,
      pointerEvents: 'none', transition: 'transform 0.08s', transformOrigin: '4px 2px'});
    document.documentElement.appendChild(c);
    const ring = document.createElement('div');
    Object.assign(ring.style, {position: 'fixed', width: '34px', height: '34px', borderRadius: '50%',
      border: '3px solid rgba(255,140,0,0.9)', zIndex: 2147483646, pointerEvents: 'none',
      opacity: 0, transform: 'scale(0.4)'});
    document.documentElement.appendChild(ring);
    addEventListener('mousemove', e => { c.style.left = (e.clientX - 4) + 'px'; c.style.top = (e.clientY - 2) + 'px'; }, true);
    addEventListener('mousedown', e => {
      c.style.transform = 'scale(0.85)';
      ring.style.left = (e.clientX - 17) + 'px'; ring.style.top = (e.clientY - 17) + 'px';
      ring.style.transition = 'none'; ring.style.opacity = 1; ring.style.transform = 'scale(0.4)';
      requestAnimationFrame(() => requestAnimationFrame(() => {
        ring.style.transition = 'opacity 0.45s, transform 0.45s'; ring.style.opacity = 0; ring.style.transform = 'scale(1.3)';
      }));
    }, true);
    addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


def _free_port() -> int:
    """Return a TCP port free to bind on localhost."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _password() -> str:
    """Return a throwaway password that meets the server's rules."""
    return f"Demo{secrets.token_hex(6)}7"


class DemoServer:
    """A classroom server in Multi-Lab Mode, isolated in a temporary folder."""

    def __init__(self, port: int) -> None:
        """Prepare the folder and logins; nothing runs until ``start``.

        Args:
            port: The port the server listens on, on 127.0.0.1.
        """
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.passwords = {name: _password() for name in [INSTRUCTOR, *GROUPS]}
        self.folder = tempfile.mkdtemp(prefix="mniplab-demo-")
        self.process: Optional[subprocess.Popen] = None
        self.log_path = os.path.join(self.folder, "serve.log")
        # Lab Examples read their seed (frr_dir) and the server reads saved
        # layouts relative to the working directory.
        for name in ("frr-config", "examples"):
            os.symlink(os.path.join(PROJECT_ROOT, name), os.path.join(self.folder, name))
        users = ",".join(
            f"{name}:{password}:{'instructor' if name == INSTRUCTOR else 'learner'}"
            for name, password in self.passwords.items()
        )
        with open(os.path.join(self.folder, ".env"), "w", encoding="utf-8") as env:
            env.write(f"MNIPLAB_LAB_MODE=multi\nMNIPLAB_LAB_CAP={len(GROUPS) + 2}\n"
                      f"MNIPLAB_USERS={users}\nMNIPLAB_TERMINAL_IDLE_TIMEOUT=600\n")
        jar = http.cookiejar.CookieJar()
        self._http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def start(self) -> None:
        """Start the server and block until it answers.

        Raises:
            RuntimeError: The server exited or did not answer within 60 s.
        """
        env = {key: value for key, value in os.environ.items() if key != "MNIPLAB_LAB_KEY"}
        env.update({
            "MNIPLAB_ENV_FILE": os.path.join(self.folder, ".env"),
            "MNIPLAB_AUTHORED_LABS_DIR": os.path.join(self.folder, "authored-labs"),
            "PYTHONPATH": PROJECT_ROOT,
        })
        with open(self.log_path, "w", encoding="utf-8") as log:
            self.process = subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-m", "mniplab", "serve",
                 "--web-host", "127.0.0.1", "--web-port", str(self.port)],
                cwd=self.folder, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"the server exited; see {self.log_path}")
            try:
                urllib.request.urlopen(self.base_url + "/", timeout=2).close()
                return
            except (urllib.error.URLError, ConnectionError, socket.timeout):
                time.sleep(0.5)
        raise RuntimeError(f"the server did not answer within 60 s; see {self.log_path}")

    def api(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        """Call the server's API as the Instructor and return the decoded reply.

        Args:
            method: The HTTP method.
            path: The path under the server, starting with ``/api/``.
            body: A JSON body, if any.

        Returns:
            The decoded JSON reply.
        """
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        with self._http.open(request, timeout=180) as reply:
            return json.loads(reply.read() or b"null")

    def start_labs(self) -> Dict[str, str]:
        """Sign in as the Instructor, set each Group's Assignment and start its Lab.

        Returns:
            Each Group's running Lab key.
        """
        login = urllib.request.Request(self.base_url + "/api/login", method="POST")
        token = f"{INSTRUCTOR}:{self.passwords[INSTRUCTOR]}"
        login.add_header("Authorization", "Basic " + base64.b64encode(token.encode()).decode())
        self._http.open(login, timeout=30).close()
        keys = {}
        for group, (example, assignment, _, _) in GROUPS.items():
            self.api("PUT", f"/api/admin/assignments/{group}", {"example_ids": assignment})
            started = self.api("POST", "/api/labs", {"example_id": example, "group": group})
            keys[group] = started["key"]
            print(f"*** {group}: started {example} ({started['key']})")
        return keys

    def wait_for_bgp(self, key: str, router: str, timeout: float = 180) -> None:
        """Block until every BGP neighbor of a Router is up and its table is steady.

        Args:
            key: The Lab's key.
            router: The Router whose BGP summary is read.
            timeout: Seconds to wait before going on anyway.
        """
        deadline = time.monotonic() + timeout
        last, steady = None, 0
        while time.monotonic() < deadline:
            output = self.api("GET", f"/api/labs/{key}/routing/bgp/{router}").get("output", "")
            rows = [line.split() for line in output.splitlines()
                    if re.match(r"^\d+\.\d+\.\d+\.\d+\s", line)]
            entries = re.search(r"RIB entries (\d+)", output)
            up = rows and all(len(row) > 9 and row[9].isdigit() for row in rows)
            count = entries.group(1) if entries else None
            steady = steady + 1 if up and count == last else 0
            last = count
            if steady >= 3:
                print(f"*** BGP on {router} settled: {count} RIB entries")
                return
            time.sleep(3)
        print(f"*** BGP on {router} did not settle in {timeout:.0f} s; recording anyway")

    def reset_preview(self) -> None:
        """Stop any Lab the Instructor's Group runs, such as a leftover Preview."""
        for lab in self.api("GET", "/api/labs").get("labs", []):
            if lab.get("group") == INSTRUCTOR:
                self.api("POST", f"/api/labs/{lab['key']}/stop")

    def stop(self) -> None:
        """Stop the server, which stops its Labs, and remove its folder."""
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=30)
        # The folder holds only symlinks to this checkout, never its targets.
        shutil.rmtree(self.folder, ignore_errors=True)


class Recorder:
    """Drives one browser page with a visible, gliding pointer."""

    def __init__(self, page: Any) -> None:
        """Wrap a Playwright page.

        Args:
            page: The page to drive.
        """
        self.page = page

    def click(self, locator: Any, settle: float = 0.15) -> None:
        """Glide the pointer to an element's centre, then click it.

        Args:
            locator: The element to click.
            settle: Seconds to rest on the element before clicking.
        """
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=14)
        time.sleep(settle)
        self.page.mouse.click(x, y)

    def node(self, label: str) -> Any:
        """Return a Topology Node by the start of its label, e.g. ``"router r1"``.

        Args:
            label: The Node's role and name.

        Returns:
            The Node's locator.
        """
        topology = self.page.locator("svg#topology")
        return topology.locator(f"[aria-label^='{label} ']").first.or_(
            topology.locator(f"[aria-label^='{label},']").first)

    def wait_for_topology(self) -> None:
        """Block until the page shows a Topology."""
        self.page.locator("svg#topology [aria-label]").first.wait_for(state="attached")

    def type_command(self, text: str, wait: float) -> None:
        """Type a command into the focused Terminal and run it.

        Args:
            text: The command.
            wait: Seconds to let its output show.
        """
        self.page.keyboard.type(text, delay=32)
        self.page.keyboard.press("Enter")
        time.sleep(wait)

    def open_terminal(self, label: str) -> None:
        """Open a Node's Terminal from the Topology and focus it.

        Args:
            label: The Node's role and name.
        """
        self.click(self.node(label))
        terminal = self.page.locator(".xterm:visible").last
        terminal.wait_for()
        time.sleep(1.4)
        terminal.click()

    def link_menu(self, ends: str, item: str) -> None:
        """Open a Link's action menu and choose an item.

        A downed Link is drawn dashed and takes no pointer, so it, and any
        Link the layout moved under the pointer, is opened from the keyboard.

        Args:
            ends: The Link's ends as its label names them, e.g. ``"r1 and r2"``.
            item: The menu item to choose.
        """
        link = self.page.locator(f"svg#topology [aria-label^='Link between {ends}']").first
        target = self.page.get_by_role("menuitem", name=item)
        for attempt in range(3):
            time.sleep(0.6)
            if attempt == 0 and "down" not in (link.get_attribute("aria-label") or ""):
                self.click(link)
            else:
                box = link.bounding_box()
                self.page.mouse.move(box["x"] + box["width"] / 2,
                                     box["y"] + box["height"] / 2, steps=14)
                link.focus()
                self.page.keyboard.press("Enter")
            try:
                target.wait_for(state="visible", timeout=2500)
                break
            except Exception:  # pylint: disable=broad-except
                self.page.keyboard.press("Escape")
        time.sleep(0.5)
        self.click(target)

    def go_group(self, group: str) -> None:
        """Switch to a Group's Lab with the Select Group picker.

        Args:
            group: The Group's login name.
        """
        picker = self.page.locator("input[placeholder^='Find or select']").first
        self.click(picker)
        picker.fill("")
        self.page.keyboard.type(group, delay=70)
        time.sleep(0.4)
        self.click(self.page.get_by_role("option", name=group).first)
        self.wait_for_topology()
        time.sleep(2.2)

    def place(self, palette_item: str, x: float, y: float) -> None:
        """Drag a Lab Authoring palette item onto the canvas.

        Args:
            palette_item: The palette button's name, e.g. ``"Add Router"``.
            x: Where to drop it, in page pixels.
            y: Where to drop it, in page pixels.
        """
        box = self.page.get_by_role("button", name=palette_item).bounding_box()
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10)
        self.page.mouse.down()
        self.page.mouse.move(x, y, steps=18)
        self.page.mouse.up()
        time.sleep(0.4)

    def connect(self, a: Tuple[float, float], b: Tuple[float, float]) -> None:
        """Drag a placed Node's ``+`` handle onto another Node.

        Args:
            a: The first Node's centre, in page pixels.
            b: The second Node's centre, in page pixels.
        """
        self.page.mouse.move(a[0], a[1], steps=10)
        time.sleep(0.3)
        self.page.mouse.move(a[0] + 30, a[1], steps=4)
        self.page.mouse.down()
        self.page.mouse.move(b[0], b[1], steps=18)
        self.page.mouse.up()
        time.sleep(0.4)


def sign_in(page: Any, base_url: str, user: str, password: str,
            recorder: Optional[Recorder] = None) -> None:
    """Sign a page in through the login form.

    Args:
        page: The page.
        base_url: The server's address.
        user: The login name.
        password: Its password.
        recorder: When given, the form is filled visibly, key by key.
    """
    page.goto(base_url + "/")
    page.wait_for_selector("#username")
    if recorder is None:
        page.fill("#username", user)
        page.fill("#password", password)
        page.click("#login-btn")
    else:
        time.sleep(0.8)
        recorder.click(page.locator("#username"))
        page.keyboard.type(user, delay=55)
        recorder.click(page.locator("#password"))
        page.keyboard.type(password, delay=45)
        recorder.click(page.locator("#login-btn"))
    page.wait_for_selector("#app:not(.hidden)")


def play_story(rec: Recorder, base_url: str, password: str, mark: Callable[[str], None]) -> None:
    """Drive the Instructor's page through the demo's seven scenes.

    Args:
        rec: The recorder driving the Instructor's page.
        base_url: The server's address.
        password: The Instructor's password.
        mark: Records a moment, ``"fast_start"``/``"fast_end"`` around waits.
    """
    page = rec.page

    # 1-2. Sign in; the Class Overview shows four Groups on one host.
    sign_in(page, base_url, INSTRUCTOR, password, recorder=rec)
    time.sleep(3.0)
    rec.click(page.get_by_role("button", name="group-a").first)
    time.sleep(1.8)
    rec.click(page.get_by_role("button", name="Go to the Lab"))
    rec.wait_for_topology()
    time.sleep(2.5)

    # 3. group-a, BGP ISP / IXP Lab: the session summary and the table.
    rec.open_terminal("router up1")
    rec.type_command("vtysh", 1.2)
    rec.type_command("show ip bgp summary", 3.2)
    rec.type_command("show ip bgp", 3.5)

    # 4. group-b, OSPF Lab: capture, traceroute, fail a Link, traceroute again.
    rec.go_group("group-b")
    rec.link_menu("r1 and r3", "Packet Capture")
    time.sleep(0.8)
    rec.click(page.get_by_role("button", name="Capture on r3-eth1 (r3)"))
    time.sleep(2.5)
    rec.open_terminal("host c11")
    rec.type_command("traceroute -n 172.17.1.2", 3.0)
    rec.link_menu("r1 and r2", "Disconnect Link")
    mark("fast_start")
    time.sleep(4.5)  # this Lab's OSPF dead interval is 3 s
    mark("fast_end")
    page.locator(".xterm:visible").last.click()
    rec.type_command("traceroute -n 172.17.1.2", 3.2)
    rec.click(page.get_by_role("button", name="OSPF").first)
    time.sleep(2.5)
    rec.link_menu("r1 and r2", "Reconnect Link")
    time.sleep(1.5)

    # 5. group-c, DHCP Lab: a Host asks for a Lease; the live Lease table.
    rec.go_group("group-c")
    rec.open_terminal("host h3")
    page.keyboard.type("dhcpcd -1 -B -4 h3-eth0", delay=32)
    page.keyboard.press("Enter")
    mark("fast_start")
    time.sleep(7.0)  # dhcpcd probes the address before binding it
    mark("fast_end")
    rec.click(page.locator("svg#topology [aria-label^='DHCP · DHCPv6 Service']").first)
    time.sleep(1.5)
    page.get_by_text("Active Leases").first.scroll_into_view_if_needed()
    time.sleep(3.0)
    page.keyboard.press("Escape")
    time.sleep(0.6)

    # 6. group-d, DNS Lab: a lookup, then the Resolver's cache.
    rec.go_group("group-d")
    rec.open_terminal("host client1")
    rec.type_command("dig web.example.com A", 2.5)
    rec.click(page.locator("svg#topology [aria-label^='RESOLVER Service']").first)
    time.sleep(3.5)
    page.keyboard.press("Escape")
    time.sleep(0.6)

    # 7. Lab Authoring: draw a small Lab and start its Preview.
    rec.click(page.get_by_role("button", name="Lab Authoring").or_(
        page.get_by_role("link", name="Lab Authoring")).first)
    page.get_by_role("button", name="New draft").wait_for()
    time.sleep(1.2)
    rec.click(page.get_by_role("button", name="New draft"))
    time.sleep(1.5)
    rec.place("Add Router", 620, 360)
    rec.place("Add Router", 1020, 360)
    rec.place("Add Host", 620, 640)
    rec.place("Add Host", 1020, 640)
    rec.connect((620, 360), (1020, 360))
    rec.connect((620, 360), (620, 640))
    rec.connect((1020, 360), (1020, 640))
    time.sleep(0.8)
    rec.click(page.get_by_role("button", name="Preview"))
    mark("fast_start")
    page.get_by_role("button", name="Stop Preview").wait_for(timeout=60000)
    mark("fast_end")
    time.sleep(2.5)

    # 8. Back to the Class Overview: Sessions, Terminals, learner input.
    page.goto(base_url + "/")
    overview = page.get_by_role("tab", name="Overview", exact=True).first
    overview.wait_for()
    time.sleep(0.8)
    heading = page.get_by_role("heading", name="Class Overview", level=2)
    if not heading.is_visible():
        rec.click(overview)
    heading.wait_for()
    time.sleep(4.0)
    mark("end")


def record(server: DemoServer, video_path: str) -> List[Tuple[str, float]]:
    """Record the demo to a video file.

    Args:
        server: The running demo server, its Labs started.
        video_path: Where to write the ``.webm`` video.

    Returns:
        The marks ``play_story`` made, in seconds from the video's start.
    """
    from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel

    marks: List[Tuple[str, float]] = []
    video_dir = tempfile.mkdtemp(prefix="mniplab-demo-video-")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        learners = []
        for group, (_, _, label, command) in GROUPS.items():
            context = browser.new_context(viewport={"width": 1400, "height": 850})
            page = context.new_page()
            sign_in(page, server.base_url, group, server.passwords[group])
            learner = Recorder(page)
            learner.wait_for_topology()
            time.sleep(1.5)
            learner.open_terminal(label)
            learner.type_command(command, 0.5)
            learners.append(context)

        context = browser.new_context(viewport=VIEWPORT, color_scheme="light",
                                      record_video_dir=video_dir, record_video_size=VIEWPORT)
        context.add_init_script(CURSOR_SCRIPT)
        page = context.new_page()
        page.set_default_timeout(30000)
        started = time.monotonic()
        try:
            play_story(Recorder(page), server.base_url, server.passwords[INSTRUCTOR],
                       lambda kind: marks.append((kind, round(time.monotonic() - started, 2))))
        finally:
            video = page.video
            assert video is not None, "the context records video"
            context.close()
            shutil.move(video.path(), video_path)
            shutil.rmtree(video_dir, ignore_errors=True)
            for learner_context in learners:
                learner_context.close()
            browser.close()
    return marks


def to_gif(video_path: str, marks: List[Tuple[str, float]], output: str,
           speed: float, width: int, fps: int) -> None:
    """Speed a recording up and convert it to an optimised GIF.

    Args:
        video_path: The recorded ``.webm``.
        marks: The marks ``record`` returned.
        output: Where to write the GIF.
        speed: How much faster than real time the video plays.
        width: The GIF's width in pixels.
        fps: The GIF's frame rate.
    """
    cuts, start = [], 0.6  # the first moments are a blank page
    for kind, at in marks:
        if kind == "fast_start":
            cuts.append((start, at, speed))
        elif kind == "fast_end":
            cuts.append((start, at, FAST_SPEED))
        elif kind == "end":
            cuts.append((start, at, speed))
        start = at
    parts = [f"[0:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{s}[v{i}]"
             for i, (a, b, s) in enumerate(cuts)]
    joined = "".join(f"[v{i}]" for i in range(len(cuts))) + f"concat=n={len(cuts)}:v=1:a=0[c]"
    palette = (f"[c]fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
               "[a]palettegen=max_colors=64:stats_mode=diff[p];"
               "[b][p]paletteuse=dither=none:diff_mode=rectangle")
    raw = output + ".raw.gif"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video_path, "-filter_complex",
                    ";".join(parts + [joined, palette]), raw], check=True)
    if shutil.which("gifsicle"):
        subprocess.run(["gifsicle", "-O3", "--lossy=60", raw, "-o", output],
                       check=True, stderr=subprocess.DEVNULL)
        os.remove(raw)
    else:
        os.replace(raw, output)
        print("*** gifsicle not found: the GIF is unoptimised, about three times larger")
    seconds = sum((b - a) / s for a, b, s in cuts)
    size = os.path.getsize(output) / 1e6
    print(f"*** Wrote {output}: {seconds:.0f} s, {width} px wide, {size:.1f} MB")


def main() -> int:
    """Parse the arguments, record the demo and write the GIF.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", help="GIF to write, replacing any file there "
                        "(default: a new timestamped file in the system temp folder)")
    parser.add_argument("--keep-video", metavar="PATH", help="also keep the raw .webm recording here")
    parser.add_argument("--speed", type=float, default=1.75, help="playback speed (default: %(default)s)")
    parser.add_argument("--width", type=int, default=960, help="GIF width in pixels (default: %(default)s)")
    parser.add_argument("--fps", type=int, default=8, help="GIF frame rate (default: %(default)s)")
    parser.add_argument("--port", type=int, default=0, help="server port (default: a free one)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("record_demo.py builds Labs, so it runs as root (the dev container already does)")
        return 1
    if not shutil.which("ffmpeg"):
        print("record_demo.py needs ffmpeg to convert the recording")
        return 1

    output = os.path.abspath(args.output or os.path.join(
        tempfile.gettempdir(), time.strftime("mininet-iplab-demo-%Y%m%d-%H%M%S.gif")))
    server = DemoServer(args.port or _free_port())
    video_path = args.keep_video or os.path.join(server.folder, "demo.webm")
    try:
        print(f"*** Starting a demo server on {server.base_url} from {server.folder}")
        server.start()
        keys = server.start_labs()
        server.wait_for_bgp(keys["group-a"], "up1")
        server.reset_preview()
        print("*** Recording")
        marks = record(server, video_path)
        to_gif(video_path, marks, output, args.speed, args.width, args.fps)
        print(f"*** Watch it, then to use it in the README:\n"
              f"    cp {output} {os.path.relpath(README_DEMO)}")
    finally:
        print("*** Stopping the demo server and its Labs")
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
