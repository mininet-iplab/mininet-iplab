"""Draw a Lab Example script's Nodes and Links without building its network.

A Python draft's script, and a Canvas draft's Code section, are known to the
canvas only by running them. The script runs in a process of its own against
a stand-in for the network class: the stand-in records each Node with its
kind and each Link with its ports, and absorbs every other call, so no
namespace, interface or daemon is ever created. The stand-in belongs to the
authoring server and is not library API.

Run as ``python -m mniplab.webserver.authoring_record --file lab.py --result
out.json``, the module is that process; :func:`record_script` starts it.
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import traceback
from typing import Any, Dict, List, Optional

#: Seconds a script may take to record before it is stopped.
RECORD_TIMEOUT = 10.0


class RecordError(Exception):
    """The script could not be recorded; the message says why."""


async def record_script(package: str, script: str, keep: bool = False) -> Dict[str, Any]:
    """Record the Nodes and Links a Lab Example script builds.

    Args:
        package: The script's folder, its working directory while it runs.
        script: The script's path.
        keep: Also record Router Seed destinations for Keep.

    Returns:
        ``nodes`` (``name``, ``kind``, ``line``) and ``links`` (``ends`` of
        ``node`` and ``port``, and ``line``), in the order the script made
        them; ``line`` is the script's own line that made each one.

    Raises:
        RecordError: The script failed, or did not finish in time.
    """
    result = os.path.join(package, ".drawing.json")
    environment = {key: value for key, value in os.environ.items()
                   if key != "MNIPLAB_LAB_KEY"}
    # The script runs from its own folder, so the library stays importable
    # from this checkout, as it does for a Lab process.
    checkout = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    environment["PYTHONPATH"] = os.pathsep.join(
        [checkout] + [path for path in environment.get("PYTHONPATH", "").split(os.pathsep) if path])
    # Output goes to a file, not a pipe: a process the script left running
    # must not keep the run open.
    errors = os.path.join(package, ".drawing.err")
    with open(errors, "wb") as stderr:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "mniplab.webserver.authoring_record",
            "--file", script, "--result", result, *(["--keep"] if keep else []),
            cwd=package, env=environment, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=stderr,
            start_new_session=True,
        )
    try:
        await asyncio.wait_for(process.wait(), RECORD_TIMEOUT)
    except asyncio.TimeoutError:
        seconds = f"{RECORD_TIMEOUT:g} second{'' if RECORD_TIMEOUT == 1 else 's'}"
        raise RecordError(f"it did not finish within {seconds}") from None
    finally:
        # The run's session holds everything the script started; none of it stays.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    try:
        with open(result, encoding="utf-8") as recorded:
            outcome = json.load(recorded)
    except (OSError, ValueError):
        with open(errors, encoding="utf-8", errors="replace") as stderr_text:
            lines = stderr_text.read().strip().splitlines()
        raise RecordError(lines[-1] if lines else "the script stopped unexpectedly") from None
    if "error" in outcome:
        raise RecordError(outcome["error"])
    return outcome


def code_section_drawing(recorded: Dict[str, Any], first_line: int) -> Dict[str, Any]:
    """Keep what a Canvas draft's Code section adds, numbered by its own lines.

    Args:
        recorded: What :func:`record_script` drew from the generated script.
        first_line: The script's line holding the Code section's first line.

    Returns:
        The Code section's ``nodes`` and ``links``, each ``line`` counted
        from 1 at the Code section's first line.
    """
    def own(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{**item, "line": item["line"] - first_line + 1} for item in items
                if item["line"] is not None and item["line"] >= first_line]

    return {"nodes": own(recorded["nodes"]), "links": own(recorded["links"])}


#: How the running Topology's Node types draw on the canvas.
_RUNNING_KINDS = {"router": "router", "host": "host", "switch": "switch",
                  "container": "container", "bgp-speaker": "speaker"}


def running_drawing(topology: Dict[str, Any], document: Dict[str, Any]) -> Dict[str, Any]:
    """Draw from a running Preview's Topology what only the draft's script knows.

    Args:
        topology: The running Lab's Topology payload.
        document: The draft the Preview was started from.

    Returns:
        ``nodes`` and ``links`` as :func:`record_script` gives them, with no
        lines; for a Canvas draft, only those its canvas does not draw.
    """
    def port(interface: str) -> Optional[int]:
        number = re.search(r"eth(\d+)$", interface or "")
        return int(number.group(1)) if number else None

    nodes: List[Dict[str, Any]] = [
        {"name": node["id"], "kind": _RUNNING_KINDS.get(node["type"], "other"), "line": None}
        for node in topology["nodes"]]
    links: List[Dict[str, Any]] = [{"ends": [{"node": link["source"], "port": port(link["source_intf"])},
                       {"node": link["target"], "port": port(link["target_intf"])}],
              "line": None} for link in topology["links"]]
    if document.get("kind") != "python":
        # The canvas draws its own Nodes and Links; the rest came from code.
        names = {node["id"]: node["name"] for node in document["topology"]["nodes"]}
        drawn = {frozenset((names[end["node_id"]], end["port"]) for end in link["ends"])
                 for link in document["topology"]["links"]}
        nodes = [node for node in nodes if node["name"] not in names.values()]
        links = [link for link in links if frozenset(
            (end["node"], end["port"]) for end in link["ends"]) not in drawn]
    return {"nodes": nodes, "links": links}


# ---------------------------------------------------------------------------
# The record-only process
# ---------------------------------------------------------------------------

class _Absorber:
    """Takes any call, attribute or item and gives itself back."""

    def __getattr__(self, name: str) -> "_Absorber":
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> "_Absorber":
        return self

    def __getitem__(self, key: Any) -> "_Absorber":
        return self

    def __iter__(self) -> Any:
        return iter(())


class _Node(_Absorber):
    """A recorded Node: its name and kind, and nothing it can run."""

    def __init__(self, name: str, kind: str, port_base: int, line: Optional[int]) -> None:
        self.name = name
        self.kind = kind
        self.line = line
        self._next_port = port_base
        self.seed: Optional[str] = None
        self.not_keepable: Optional[str] = None
        self.nat: List[Dict[str, Any]] = []

    def add_frr_config(self, config: str) -> None:
        """Mark inline Router text as something that must change in the script."""
        self.seed = None
        self.not_keepable = "FRR text is inline; move it into a Seed folder named by frr_dir in your script."

    def new_port(self, wanted: Optional[int]) -> int:
        """Number a Link end as Mininet does: the next port, or the one asked for."""
        port = self._next_port if wanted is None else wanted
        self._next_port = max(self._next_port, port + 1)
        return port

    def __str__(self) -> str:
        return self.name

    __repr__ = __str__


class _RecordingNetwork(_Absorber):
    """Stands in for the network class: records Nodes and Links, absorbs the rest."""

    script = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.nameToNode: Dict[str, _Node] = {}
        self.links: List[Dict[str, Any]] = []
        self.order: List[_Node] = []

    def _line(self) -> Optional[int]:
        frames = [frame for frame in traceback.extract_stack()
                  if os.path.realpath(frame.filename) == self.script]
        return frames[-1].lineno if frames else None

    def _add(self, name: Any, kind: str) -> _Node:
        node = _Node(str(name), kind, 1 if kind == "switch" else 0, self._line())
        self.nameToNode[node.name] = node
        self.order.append(node)
        return node

    def addHost(self, name: str, cls: Optional[type] = None, **params: Any) -> _Node:
        return self._add(name, _kind_of(cls, "host"))

    def addNode(self, name: str, cls: Optional[type] = None, **params: Any) -> _Node:
        return self._add(name, _kind_of(cls, "host"))

    def addRouter(self, name: str, *args: Any, **params: Any) -> _Node:
        node = self._add(name, "router")
        node.nat = [row for row in map(_nat_row, params.get("nat") or []) if row is not None]
        folder = params.get("frr_dir", args[1] if len(args) > 1 else None)
        if "frr_config" in params:
            node.not_keepable = "FRR text is inline; move it into a Seed folder named by frr_dir in your script."
        elif not folder:
            node.not_keepable = "No Seed folder; name one with frr_dir in your script."
        else:
            from mniplab.webserver.authoring import AuthoringRepository
            destination = os.path.realpath(os.path.join(
                str(folder).replace("%(name)s", node.name), "frr.conf"))
            relative = os.path.relpath(destination, os.path.dirname(self.script))
            if AuthoringRepository._file_pattern.fullmatch(relative):
                node.seed = relative
            else:
                node.not_keepable = "Name a Seed folder under frr-config in your script."
        return node

    def addSwitch(self, name: str, cls: Optional[type] = None, **params: Any) -> _Node:
        return self._add(name, "switch")

    def addExaBGP(self, name: str, *args: Any, **params: Any) -> _Node:
        return self._add(name, "speaker")

    def addContainer(self, name: str, *args: Any, **params: Any) -> _Node:
        return self._add(name, "container")

    def addNAT(self, name: str, *args: Any, **params: Any) -> _Node:
        return self._add(name, "nat")

    def addTransit(self, name: str, *args: Any, **params: Any) -> _Node:
        return self._add(name, "transit")

    def addLink(self, node1: Any, node2: Any, port1: Optional[int] = None,
                port2: Optional[int] = None, *args: Any, **params: Any) -> _Absorber:
        first, second = (node if isinstance(node, _Node) else self[node]
                         for node in (node1, node2))
        ends = [{"node": first.name, "port": first.new_port(port1)},
                {"node": second.name, "port": second.new_port(port2)}]
        for end, key in zip(ends, ("params1", "params2")):
            if (params.get(key) or {}).get("nat") == "outside":
                end["outside"] = True
        self.links.append({"ends": ends, "line": self._line()})
        return _Absorber()

    def __getitem__(self, name: Any) -> _Node:
        return self.nameToNode[str(name)]

    def get(self, *names: Any) -> Any:
        nodes = [self[name] for name in names]
        return nodes[0] if len(nodes) == 1 else nodes

    def __contains__(self, name: Any) -> bool:
        return str(name) in self.nameToNode

    def __iter__(self) -> Any:
        return iter(self.nameToNode)

    def __len__(self) -> int:
        return len(self.nameToNode)

    @property
    def hosts(self) -> List[_Node]:
        return [node for node in self.order if node.kind != "switch"]

    @property
    def switches(self) -> List[_Node]:
        return [node for node in self.order if node.kind == "switch"]


def _nat_row(entry: Any) -> Optional[Dict[str, Any]]:
    """A port forward or static mapping as a canvas NAT row, with only the fields it sets.

    Returns None for anything else: the Lab refuses such an entry when it is
    built, so the canvas has no row to draw for it.
    """
    from mniplab.routernat import PortForward, StaticNAT

    if isinstance(entry, PortForward):
        row: Dict[str, Any] = {"kind": "port_forward", "protocol": entry.protocol,
                               "outside_port": entry.outside_port, "inside": entry.inside,
                               "inside_port": entry.inside_port, "hairpin": entry.hairpin}
    elif isinstance(entry, StaticNAT):
        row = {"kind": "static", "inside": entry.inside, "outside": entry.outside}
    else:
        return None
    if entry.interface is not None:
        row["interface"] = entry.interface
    return row


def _kind_of(cls: Optional[type], default: str) -> str:
    """The kind a Node class draws as; a class the canvas does not know is ``other``."""
    if cls is None:
        return default
    from mniplab.container import ContainerHost
    from mniplab.exabgp.node import ExaBGPNode
    from mniplab.host import mnIPLabHost
    from mniplab.nodelib import MasqueradeNAT, TransitNAT
    from mniplab.router import FRRouter
    known = ((TransitNAT, "transit"), (MasqueradeNAT, "nat"), (FRRouter, "router"),
             (ExaBGPNode, "speaker"), (ContainerHost, "container"), (mnIPLabHost, "host"))
    return next((kind for known_cls, kind in known
                 if isinstance(cls, type) and issubclass(cls, known_cls)), "other")


def _record(script: str, keep: bool = False) -> Dict[str, Any]:
    """Run the script's ``build_network()`` against the stand-in."""
    import mniplab
    import mniplab.net
    from mniplab.runner import isolate_package_imports, load_lab_example

    isolate_package_imports(os.path.dirname(os.path.abspath(script)))
    _RecordingNetwork.script = os.path.realpath(script)
    mniplab.mnIPLab = _RecordingNetwork  # type: ignore[misc,assignment]
    mniplab.net.mnIPLab = _RecordingNetwork  # type: ignore[misc,assignment]
    try:
        net = load_lab_example(script).build_network()
    except Exception as exc:  # whatever the script raised is the reason
        frames = [frame for frame in traceback.extract_tb(exc.__traceback__)
                  if os.path.realpath(frame.filename) == _RecordingNetwork.script]
        where = ""
        if frames:
            code = f": {frames[-1].line.strip()}" if frames[-1].line else ""
            where = f" (line {frames[-1].lineno} of {os.path.basename(script)}{code})"
        return {"error": f"{exc}{where}"}
    if not isinstance(net, _RecordingNetwork):
        return {"error": "build_network() did not return the network it built"}
    return {
        **({"keep": {node.name: {"seed": node.seed, "not_keepable": node.not_keepable}
                    for node in net.order if node.kind == "router"}} if keep else {}),
        "nodes": [{"name": node.name, "kind": node.kind, "line": node.line,
                   **({"nat": node.nat} if node.nat else {})} for node in net.order],
        "links": net.links,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """Record one script; the entry point of ``python -m mniplab.webserver.authoring_record``.

    Args:
        argv: ``--file`` (the script) and ``--result`` (where the drawing,
            or the reason there is none, is written as JSON).

    Returns:
        0; the outcome is in the result file.
    """
    parser = argparse.ArgumentParser(prog="python -m mniplab.webserver.authoring_record")
    parser.add_argument("--file", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    outcome = _record(args.file, args.keep)
    with open(args.result, "w", encoding="utf-8") as result:
        json.dump(outcome, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
