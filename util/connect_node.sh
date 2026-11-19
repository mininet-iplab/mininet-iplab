#!/bin/bash
set -euo pipefail

# Attaches to a Node with nsenter, restating in shell the recipe that
# mniplab.namespace.build_nsenter_argv owns: the flag set is a single fact
# used in two languages, and this file is the shell copy. No branch for the
# MNIPLAB_PRIVATE_NAMESPACES escape hatch — nsenter enters whatever
# namespaces the target has, so one recipe covers both modes.

MNIPLAB_TMP_DIR="${MNIPLAB_TMP_DIR:-/tmp/mniplab}"
LABS_DIR="${MNIPLAB_TMP_DIR}/labs"

usage() {
  cat <<'EOF'
Usage: connect_node.sh [--lab KEY] [-l|--list] HOST CMD [ARGS ...]

Run CMD inside the Mininet namespace for HOST.

Options:
  --lab KEY   Lab key to target (defaults to $MNIPLAB_LAB_KEY or the single alive Lab)
  -l, --list  Print the known host names and exit
  -h, --help  Show this help message

Example:
  connect_node.sh r1 vtysh -c 'show ip route'
  connect_node.sh --lab lab-abc r1 vtysh -c 'show ip route'
EOF
}

get_alive_labs() {
  local labs=()
  if [[ -d "$LABS_DIR" ]]; then
    for d in "$LABS_DIR"/*; do
      if [[ -d "$d" ]]; then
        labs+=("$(basename "$d")")
      fi
    done
  fi
  printf '%s\n' "${labs[@]}"
}

discover_hosts() {
  local key=$1
  if [[ -d "$LABS_DIR/$key/nodes" ]]; then
    find "$LABS_DIR/$key/nodes" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort -u
  fi
}

list_hosts() {
  local key=$1
  local found=0

  while IFS= read -r host; do
    printf '%s\n' "$host"
    found=1
  done < <(discover_hosts "$key")

  if (( found == 0 )); then
    echo "No Mininet hosts detected." >&2
  fi
}

host_known() {
  local target=$1
  local key=$2
  if discover_hosts "$key" | grep -Fxq "$target"; then
    return 0
  fi
  return 1
}

require_nsenter() {
  if ! command -v nsenter >/dev/null 2>&1; then
    echo "nsenter not found in PATH; install util-linux or ensure nsenter is available." >&2
    exit 3
  fi
}

get_host_pid() {
  local host=$1
  local key=$2

  # Anchored at ^bash so the pattern selects the Node's shell — the process
  # whose argv is `bash ... mininet:<host>` — and never a launcher or wrapper
  # whose command line merely *contains* the match. Under unshare both the
  # launcher and the shell match the unanchored pattern, and only the shell
  # (the Namespace Anchor) sits inside the PID namespace a Terminal targets.
  local candidate_pids
  candidate_pids=$(ps -eo pid=,args= |
    sed -n -E "/^ *[0-9]+ bash.* mininet:${host}\$/s/^ *([0-9]+) .*/\1/p")

  if [[ -z "$candidate_pids" ]]; then
    echo "Unable to locate PID for host '${host}'. Is it running?" >&2
    exit 2
  fi

  # Two Labs may each have a Node of this name. The one belonging to the Lab
  # is told by its mounts: every backing file a Node is bound to lives in
  # its Lab directory, so labs/<key>/ appears in that process's mountinfo.
  for p in $candidate_pids; do
    if grep -q "labs/${key}/" "/proc/${p}/mountinfo" 2>/dev/null; then
      printf '%s' "$p"
      return 0
    fi
  done

  echo "No process of host '${host}' belongs to Lab '${key}' (candidates: $(echo $candidate_pids))." >&2
  exit 2
}

main() {
  local lab_key="${MNIPLAB_LAB_KEY:-}"

  while [[ $# -gt 0 ]]; do
    case $1 in
      --lab)
        if [[ $# -lt 2 ]]; then
          echo "Error: --lab requires a key argument." >&2
          exit 1
        fi
        lab_key="$2"
        shift 2
        ;;
      --lab=*)
        lab_key="${1#*=}"
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        break
        ;;
    esac
  done

  local alive_labs=()
  while IFS= read -r l; do
    [[ -n "$l" ]] && alive_labs+=("$l")
  done < <(get_alive_labs)

  if [[ -z "$lab_key" ]]; then
    if [[ ${#alive_labs[@]} -eq 1 ]]; then
      lab_key="${alive_labs[0]}"
    elif [[ ${#alive_labs[@]} -gt 1 ]]; then
      echo "Multiple alive Labs detected. Please specify --lab <key> or set MNIPLAB_LAB_KEY:" >&2
      for k in "${alive_labs[@]}"; do
        echo "  $k" >&2
      done
      exit 1
    else
      echo "No alive Lab under ${LABS_DIR}. Is one running? If it runs under another runtime root, set MNIPLAB_TMP_DIR to match." >&2
      exit 1
    fi
  fi

  if [[ $# -eq 0 ]]; then
    usage
    exit 1
  fi

  case $1 in
    -l|--list)
      list_hosts "$lab_key"
      return 0
      ;;
  esac

  if [[ $# -lt 2 ]]; then
    echo "Missing HOST or CMD." >&2
    usage
    exit 1
  fi

  local host=$1
  shift

  if ! host_known "$host" "$lab_key"; then
    echo "Warning: host '${host}' is not in the known host list." >&2
  fi

  require_nsenter
  local host_pid
  host_pid=$(get_host_pid "$host" "$lab_key")

  local homeDir="${LABS_DIR}/${lab_key}/home/${host}"

  # For interactive bash, use exec without -c to preserve interactivity
  # For commands, set environment and run the command
  if [[ $1 == "bash" ]]; then
    # Interactive shell: set environment and exec bash
    nsenter -t "$host_pid" -m -u -n -p -- bash --norc -c "export HOME='${homeDir}' PS1='${host}# '; cd '${homeDir}'; exec bash --norc"
  else
    # Command execution: set environment and run the command.
    # Build a single, safely-escaped command string from the remaining args
    # so both forms work:
    #   ./util/connect_node.sh r1 vtysh -c "show ip route"
    # and
    #   ./util/connect_node.sh r1 "vtysh -c 'show ip route'"
    if [[ $# -eq 1 ]]; then
      cmd="$1"
    else
      cmd=$(printf '%q ' "$@")
      cmd=${cmd%}
    fi
    nsenter -t "$host_pid" -m -u -n -p -- bash --norc -c "export HOME='${homeDir}'; cd '${homeDir}'; $cmd"
  fi
}

main "$@"
