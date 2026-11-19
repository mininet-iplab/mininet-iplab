#!/usr/bin/env python3
"""Generate examples/labs.json from lab script analysis.

Usage:
    python3 util/gen_labs_json.py                        # Interactive: scan all, prompt for missing labs
    python3 util/gen_labs_json.py examples/my-lab.py     # Add/update a single lab
    python3 util/gen_labs_json.py --all                  # Non-interactive: refresh existing, report missing
    python3 util/gen_labs_json.py --check                # Dry-run: report missing labs, exit 1 if any
"""

import argparse
import ast
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Path constants — resolved relative to this file's location (util/)
# ---------------------------------------------------------------------------
_UTIL_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTIL_DIR)
EXAMPLES_DIR = os.path.join(_PROJECT_ROOT, 'examples')
LABS_JSON_PATH = os.path.join(EXAMPLES_DIR, 'labs.json')

# Share the web server's choices; direct script execution starts in util/.
sys.path.insert(0, _PROJECT_ROOT)
from mniplab.webserver.catalog import AREAS


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def extract_metadata(filepath: str) -> dict:
    """Auto-detect lab metadata from a .py file using AST (no import/exec).

    Returns a dict with detected fields. Does NOT prompt the user.
    """
    with open(filepath, encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        print(f"  Warning: could not parse {os.path.basename(filepath)}: {e}", file=sys.stderr)
        tree = None

    # Module docstring — first non-empty line only
    docstring = None
    if tree is not None:
        raw = ast.get_docstring(tree)
        if raw:
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    docstring = line
                    break

    lab_id = os.path.splitext(os.path.basename(filepath))[0]
    lab_file = os.path.basename(filepath)

    # --- Feature detection (plain substring search, fast and reliable) ---
    has_exabgp = 'addExaBGP' in source
    has_ipv6 = any(tok in source for tok in ('ip6=', 'ip6:', 'defaultRoute6', 'make_v6_peer_routes'))
    # A Router declared with frr_dir has Persistent Config: its directory is
    # the seed each Group's copy starts from under the web UI's manager.
    persistent_config = 'frr_dir=' in source

    # --- Protocol inference ---
    protocols = []
    if "proto='OSPF" in source or 'proto="OSPF' in source:
        protocols.append('OSPF')
    if "proto='BGP" in source or 'proto="BGP' in source or has_exabgp:
        protocols.append('BGP')
    if has_exabgp:
        protocols.append('ExaBGP')
    if not protocols:
        protocols.append('static')
    protocols.sort()

    # --- Node count (count call-site occurrences) ---
    node_count = (
        source.count('addRouter(')
        + source.count('addHost(')
        + source.count('addExaBGP(')
        + source.count('addSwitch(')
        + source.count('addNAT(')
    )

    # --- Layout file detection ---
    has_layout = os.path.exists(os.path.join(EXAMPLES_DIR, 'layouts', f'{lab_id}.json'))

    return {
        'id': lab_id,
        'file': lab_file,
        'docstring': docstring,
        'protocols': protocols,
        'node_count': node_count,
        'has_layout': has_layout,
        'has_exabgp': has_exabgp,
        'has_ipv6': has_ipv6,
        'persistent_config': persistent_config,
    }


def load_labs_json() -> list:
    """Load and parse examples/labs.json. Returns [] on missing file or parse error."""
    if not os.path.exists(LABS_JSON_PATH):
        return []
    try:
        with open(LABS_JSON_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {LABS_JSON_PATH}: {e}", file=sys.stderr)
        return []


def save_labs_json(labs: list) -> None:
    """Write labs in catalogue order to examples/labs.json with compact string arrays."""
    text = json.dumps(labs, indent=2, ensure_ascii=False)
    # Collapse string-only arrays from multi-line to single-line.
    # json.dumps with indent=2 expands ["BGP", "OSPF"] into 4 lines;
    # this regex restores them to the original compact inline format.
    def _collapse(m: re.Match) -> str:
        items = re.findall(r'"([^"]*)"', m.group(0))
        return '[' + ', '.join(f'"{i}"' for i in items) + ']'
    text = re.sub(r'\[\s*\n(?:\s*"[^"]*",?\s*\n)+\s*\]', _collapse, text)
    with open(LABS_JSON_PATH, 'w', encoding='utf-8') as f:
        f.write(text)
        f.write('\n')


def find_lab_scripts() -> list:
    """Return sorted list of absolute .py paths in examples/, excluding _-prefixed files."""
    scripts = []
    for name in sorted(os.listdir(EXAMPLES_DIR)):
        if name.endswith('.py') and not name.startswith('_'):
            scripts.append(os.path.join(EXAMPLES_DIR, name))
    return scripts


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------

def _input_with_default(prompt: str, default: str) -> str:
    """Prompt user; return default if they press Enter."""
    if default:
        value = input(f'{prompt} [{default}]: ').strip()
    else:
        value = input(f'{prompt}: ').strip()
    return value if value else default


def prompt_for_lab(meta: dict, existing: dict | None = None) -> dict | None:
    """Interactively prompt for fields that cannot be auto-detected.

    `existing` is the current labs.json entry for this id (if updating).
    Returns a complete entry dict, or None if the user skipped.
    """
    print(f"\n--- {'Updating' if existing else 'Adding'} lab: {meta['id']} ---")
    print(f"  File     : {meta['file']}")
    print(f"  Docstring: {meta['docstring'] or '(none)'}")
    print(f"  Protocols: {', '.join(meta['protocols'])}")
    print(f"  Nodes    : {meta['node_count']}")
    print(f"  Layout   : {'yes' if meta['has_layout'] else 'no'}")
    print(f"  ExaBGP   : {'yes' if meta['has_exabgp'] else 'no'}")
    print(f"  IPv6     : {'yes' if meta['has_ipv6'] else 'no'}")
    print(f"  Persistent Config: {'yes' if meta['persistent_config'] else 'no'}")
    print()

    # name — required; no safe auto-default
    name_default = existing['name'] if existing else ''
    name = _input_with_default('Lab name (required)', name_default)
    if not name:
        print(f"  Skipping {meta['id']} (name is required).")
        return None

    # description — default to existing value, then docstring
    desc_default = existing['description'] if existing else (meta['docstring'] or '')
    description = _input_with_default('Description', desc_default)

    # protocols — default to existing value, then auto-detected
    existing_protocols = ', '.join(existing['protocols']) if existing else ''
    auto_protocols = ', '.join(meta['protocols'])
    proto_default = existing_protocols or auto_protocols
    proto_input = _input_with_default('Protocols (comma-separated)', proto_default)
    protocols = [p.strip() for p in proto_input.split(',') if p.strip()]

    print('Areas: ' + ', '.join(AREAS))
    area = _input_with_default('Area (required)', existing.get('area', '') if existing else '')
    while area not in AREAS:
        print('  Choose an Area from the list.')
        area = _input_with_default('Area (required)', '')

    # node_count — default to existing value, then auto-detected
    nc_default = str(existing['node_count']) if existing else str(meta['node_count'])
    nc_input = _input_with_default('Node count', nc_default)
    try:
        node_count = int(nc_input) if nc_input else meta['node_count']
    except ValueError:
        print(f"  Invalid node count '{nc_input}', using auto-detected value {meta['node_count']}.")
        node_count = meta['node_count']

    return {
        'id': meta['id'],
        'name': name,
        'area': area,
        'description': description,
        'file': meta['file'],
        'protocols': protocols,
        'node_count': node_count,
        'persistent_config': meta['persistent_config'],
    }


# ---------------------------------------------------------------------------
# Invocation modes
# ---------------------------------------------------------------------------

def mode_check() -> int:
    """Dry-run: report which scripts are missing from labs.json. Exit 1 if any."""
    labs = load_labs_json()
    existing_ids = {lab['id'] for lab in labs}

    scripts = find_lab_scripts()
    script_ids = {os.path.splitext(os.path.basename(s))[0] for s in scripts}

    missing = sorted(script_ids - existing_ids)
    extra = sorted(existing_ids - script_ids)

    print(f"Registered labs  : {len(existing_ids)}")
    print(f"Lab scripts found: {len(script_ids)}")
    print(f"Missing from labs.json : {', '.join(missing) if missing else 'none'}")
    print(f"In labs.json but no script: {', '.join(extra) if extra else 'none'}")

    return 1 if missing else 0


def mode_all() -> int:
    """Non-interactive: refresh existing labs.json, print status for each script."""
    labs = load_labs_json()
    existing_by_id = {lab['id']: lab for lab in labs}

    scripts = find_lab_scripts()
    missing = []

    print(f"Scanning {len(scripts)} lab scripts in examples/...\n")
    for script in scripts:
        lab_id = os.path.splitext(os.path.basename(script))[0]
        if lab_id in existing_by_id:
            # Detected, never prompted for: refreshed from the script on every pass.
            existing_by_id[lab_id]['persistent_config'] = extract_metadata(script)['persistent_config']
            print(f"  ✓ {lab_id} (already registered)")
        else:
            print(f"  ? {lab_id} (not registered — run: python3 util/gen_labs_json.py {os.path.relpath(script, _PROJECT_ROOT)})")
            missing.append(lab_id)

    save_labs_json(labs)
    print(f"\nexamples/labs.json refreshed ({len(labs)} labs).")
    if missing:
        print(f"\n{len(missing)} unregistered lab(s): {', '.join(missing)}")
    return 0


def mode_single(filepath: str) -> int:
    """Add or update a single lab interactively."""
    filepath = os.path.abspath(filepath)
    if not os.path.isfile(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        return 1
    if not filepath.endswith('.py'):
        print(f"Error: expected a .py file, got: {filepath}", file=sys.stderr)
        return 1

    meta = extract_metadata(filepath)
    labs = load_labs_json()
    existing_by_id = {lab['id']: lab for lab in labs}
    existing = existing_by_id.get(meta['id'])

    if existing:
        print(f"Lab '{meta['id']}' is already in labs.json.")

    entry = prompt_for_lab(meta, existing=existing)
    if entry is None:
        return 0

    # Upsert: replace existing or append
    if existing is not None:
        labs[labs.index(existing)] = entry
    else:
        labs.append(entry)
    save_labs_json(labs)

    action = 'Updated' if existing else 'Added'
    print(f"\n✓ {action} '{entry['id']}' in examples/labs.json ({len(labs)} labs total).")
    return 0


def mode_interactive() -> int:
    """Default mode: scan all scripts, prompt for each one missing from labs.json."""
    labs = load_labs_json()
    existing_by_id = {lab['id']: lab for lab in labs}

    scripts = find_lab_scripts()
    missing_scripts = [s for s in scripts
                       if os.path.splitext(os.path.basename(s))[0] not in existing_by_id]

    if not missing_scripts:
        print("All lab scripts are already registered in examples/labs.json.")
        return 0

    print(f"Found {len(missing_scripts)} unregistered lab script(s).")
    print("Press Enter to accept defaults, or type a new value.\n")

    added = 0
    for script in missing_scripts:
        meta = extract_metadata(script)
        entry = prompt_for_lab(meta)
        if entry is not None:
            labs.append(entry)
            added += 1

    if added:
        save_labs_json(labs)
        print(f"\n✓ Added {added} lab(s). examples/labs.json now has {len(labs)} entries.")
    else:
        print("\nNo labs added.")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate or update examples/labs.json from lab scripts.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        'file',
        nargs='?',
        help='Path to a single lab .py file to add/update.',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Non-interactive: refresh existing labs.json and report unregistered scripts.',
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Dry-run: report missing labs and exit 1 if any are unregistered.',
    )
    args = parser.parse_args()

    if args.check:
        sys.exit(mode_check())
    elif args.all:
        sys.exit(mode_all())
    elif args.file:
        sys.exit(mode_single(args.file))
    else:
        sys.exit(mode_interactive())


if __name__ == '__main__':
    main()
