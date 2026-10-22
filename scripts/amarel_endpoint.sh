#!/usr/bin/env bash
# Run the Orbit endpoint on an Amarel login node (plans/AMAREL_ENDPOINT.md §4).
#
#   ./scripts/amarel_endpoint.sh install     build ~/orbit-venv from the radical.orbit checkout
#   ./scripts/amarel_endpoint.sh check       verify venv, credentials, URL, broker reachability
#   ./scripts/amarel_endpoint.sh start       check, then run the endpoint in a tmux session
#   ./scripts/amarel_endpoint.sh run         the same, in the foreground (what `start` runs)
#   ./scripts/amarel_endpoint.sh stop        stop the tmux session
#   ./scripts/amarel_endpoint.sh status      session, process, last registration line
#   ./scripts/amarel_endpoint.sh logs [-f]   tail the endpoint log
#   ./scripts/amarel_endpoint.sh selftest    startup test against a throwaway loopback broker
#
# Environment (defaults in brackets):
#
#   ORBIT_BROKER_URL       https://<linode-ip>:8443 — required for check/start/run;
#                          $RADICAL_ORBIT_BROKER_URL is accepted too
#   ORBIT_ENDPOINT_NAME    [amarel3] — the agent's DESIGNAGENT_ORBIT_ENDPOINT must match
#   ORBIT_VENV             [~/orbit-venv]
#   ORBIT_SRC              [~/radical.orbit] — used by `install` only
#   ORBIT_PLUGINS          [psij,sysinfo] — narrower than the login-node default, which
#                          already has psij; naming them drops staging, which nothing uses (§1)
#   RADICAL_ORBIT_PSIJ_DIR [/scratch/$USER/orbit-psij]
#
# Cert and token resolve the way Orbit resolves them: $RADICAL_ORBIT_BROKER_CERT /
# $RADICAL_ORBIT_BROKER_TOKEN, else ~/.radical/orbit/{broker_cert.pem,broker.token}.
#
# Traps this encodes:
#
#   * The endpoint dials out; nothing here listens. A broker that is down or firewalled shows up
#     as a reconnect loop in the log, not as a startup error — so `check` probes the TCP port
#     first and `start` refuses when it does not answer.
#   * A tmux session lives on the login node it was started on. Amarel has several, so
#     `status` names the node the session belongs to, and `stop`/`status` from another node
#     say so instead of reporting "not running".
#   * `selftest` runs a real broker on 127.0.0.1 of a shared login node. Loopback here is shared
#     with every logged-in user (backlog A9), so it keeps auth on with a one-off token, uses a
#     random port, and tears everything down on exit. It never touches ~/.radical/orbit.
set -euo pipefail

cd "$(dirname "$0")/.."

VENV="${ORBIT_VENV:-$HOME/orbit-venv}"
SRC="${ORBIT_SRC:-$HOME/radical.orbit}"
NAME="${ORBIT_ENDPOINT_NAME:-amarel3}"
PLUGINS="${ORBIT_PLUGINS:-psij,sysinfo}"
URL="${ORBIT_BROKER_URL:-${RADICAL_ORBIT_BROKER_URL:-}}"
CERT="${RADICAL_ORBIT_BROKER_CERT:-$HOME/.radical/orbit/broker_cert.pem}"
TOKEN_FILE="$HOME/.radical/orbit/broker.token"
export RADICAL_ORBIT_PSIJ_DIR="${RADICAL_ORBIT_PSIJ_DIR:-/scratch/$USER/orbit-psij}"
SESSION="orbit-endpoint"
STATE="$HOME/.radical/orbit/endpoint-session"   # which node holds the tmux session
LOG="${RADICAL_ORBIT_LOG_FILE:-$HOME/.radical/orbit/logs/$NAME.log}"
ENDPOINT="$VENV/bin/radical-orbit-endpoint.py"
NODE="$(hostname -s)"

die()  { echo "amarel_endpoint: $*" >&2; exit 1; }
ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; FAILED=1; }
warn() { echo "  warn  $*"; }

# host and port out of https://host:port
url_hostport() {
    local rest="${1#*://}"
    rest="${rest%%/*}"
    case "$rest" in
        *:*) echo "${rest%:*} ${rest##*:}" ;;
        *)   echo "$rest 443" ;;
    esac
}

tcp_probe() {   # host port -> exit 0 if something accepts within 5 s
    timeout 5 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null
}

cmd_install() {
    [[ -d "$SRC" ]] || die "no radical.orbit checkout at $SRC (set ORBIT_SRC)"
    command -v uv >/dev/null || die "uv is not on PATH"
    [[ -x "$VENV/bin/python" ]] || uv venv --python 3.12 "$VENV"
    # Full dependency set: there is no refcodes/rhapsody here to displace (§4).
    uv pip install --python "$VENV" -e "$SRC"
    echo "radical.orbit $(git -C "$SRC" rev-parse --short HEAD) in $VENV"
}

cmd_check() {
    FAILED=0
    echo "endpoint '$NAME' on $NODE"

    if [[ -x "$ENDPOINT" ]]; then
        ok "venv $VENV ($("$VENV/bin/python" -c 'import radical.orbit as o; print(o.__version__)' \
            2>/dev/null || echo '?'))"
    else
        bad "no $ENDPOINT — run '$0 install'"
    fi

    if [[ -r "$CERT" ]]; then
        ok "cert $CERT (expires $(openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2))"
    else
        bad "no broker cert at $CERT — scp it from the broker host (§4)"
    fi

    if [[ -n "${RADICAL_ORBIT_BROKER_TOKEN:-}" ]]; then
        ok "token from \$RADICAL_ORBIT_BROKER_TOKEN"
    elif [[ -r "$TOKEN_FILE" ]]; then
        local mode
        mode="$(stat -c %a "$TOKEN_FILE")"
        if [[ "$mode" == 600 || "$mode" == 400 ]]; then
            ok "token $TOKEN_FILE"
        else
            bad "token $TOKEN_FILE is mode $mode; chmod 600 it"
        fi
    else
        bad "no token at $TOKEN_FILE — scp it from the broker host (§4)"
    fi

    if mkdir -p "$RADICAL_ORBIT_PSIJ_DIR" 2>/dev/null && [[ -w "$RADICAL_ORBIT_PSIJ_DIR" ]]; then
        ok "PSI/J dir $RADICAL_ORBIT_PSIJ_DIR"
    else
        bad "PSI/J dir $RADICAL_ORBIT_PSIJ_DIR is not writable"
    fi

    command -v sbatch >/dev/null && ok "sbatch on PATH" || bad "sbatch not on PATH"

    [[ "$NODE" == "$NAME" ]] || warn "running on $NODE, endpoint named '$NAME'"

    if [[ -z "$URL" ]]; then
        bad "ORBIT_BROKER_URL is not set (e.g. https://<linode-ip>:8443)"
    elif [[ "$URL" != https://* && "$URL" != wss://* ]]; then
        bad "ORBIT_BROKER_URL must be https:// or wss://, got $URL"
    else
        local host port
        read -r host port < <(url_hostport "$URL")
        if tcp_probe "$host" "$port"; then
            ok "broker port $host:$port answers"
        else
            bad "nothing answers at $host:$port (broker down, or the firewall drops this node)"
        fi
    fi

    return "$FAILED"
}

cmd_run() {
    [[ -n "$URL" ]] || die "ORBIT_BROKER_URL is not set"
    [[ -x "$ENDPOINT" ]] || die "no $ENDPOINT — run '$0 install'"
    mkdir -p "$RADICAL_ORBIT_PSIJ_DIR"
    exec "$ENDPOINT" --name "$NAME" -p "$PLUGINS" --url "$URL" --cert "$CERT"
}

session_node() { [[ -r "$STATE" ]] && cat "$STATE" || true; }

cmd_start() {
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        die "tmux session '$SESSION' already exists on $NODE; '$0 stop' first"
    fi
    local held
    held="$(session_node)"
    if [[ -n "$held" && "$held" != "$NODE" ]]; then
        die "the endpoint session was last started on $held; stop it there first, or rm $STATE"
    fi
    cmd_check || die "check failed; not starting"

    local start_line=0
    [[ -f "$LOG" ]] && start_line="$(wc -l < "$LOG")"

    # Pass the resolved settings explicitly: a session inherits the tmux server's environment,
    # not this shell's, whenever a server is already running.
    local -a env=(-e "ORBIT_BROKER_URL=$URL" -e "ORBIT_ENDPOINT_NAME=$NAME"
                  -e "ORBIT_VENV=$VENV" -e "ORBIT_PLUGINS=$PLUGINS"
                  -e "RADICAL_ORBIT_BROKER_CERT=$CERT" -e "RADICAL_ORBIT_LOG_FILE=$LOG"
                  -e "RADICAL_ORBIT_PSIJ_DIR=$RADICAL_ORBIT_PSIJ_DIR")
    [[ -n "${RADICAL_ORBIT_BROKER_TOKEN:-}" ]] \
        && env+=(-e "RADICAL_ORBIT_BROKER_TOKEN=$RADICAL_ORBIT_BROKER_TOKEN")
    # The trailing sleep keeps the pane open after an exit, so the reason stays readable.
    tmux new-session -d -s "$SESSION" "${env[@]}" \
        "'$PWD/scripts/amarel_endpoint.sh' run; echo 'endpoint exited'; sleep 3600"
    mkdir -p "$(dirname "$STATE")"
    echo "$NODE" > "$STATE"

    echo "waiting for registration..."
    for _ in $(seq 60); do
        if [[ -f "$LOG" ]] && tail -n +"$((start_line + 1))" "$LOG" \
                | grep -q "registered as '$NAME'"; then
            tail -n +"$((start_line + 1))" "$LOG" | grep "registered as '$NAME'" | tail -1
            echo "endpoint '$NAME' is up in tmux session '$SESSION' on $NODE"
            return 0
        fi
        sleep 1
    done
    echo "no registration within 60 s; last lines of $LOG:" >&2
    tail -n 20 "$LOG" >&2 || true
    return 1
}

cmd_stop() {
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        # SIGINT first: the endpoint handles it and closes its broker session cleanly.
        tmux send-keys -t "$SESSION" C-c
        sleep 3
        tmux kill-session -t "$SESSION" 2>/dev/null || true
        rm -f "$STATE"
        echo "stopped '$SESSION' on $NODE"
    else
        local held
        held="$(session_node)"
        if [[ -n "$held" && "$held" != "$NODE" ]]; then
            die "no session here; it was started on $held — ssh $held '$0 stop'"
        fi
        rm -f "$STATE"
        echo "not running on $NODE"
    fi
}

cmd_status() {
    local held
    held="$(session_node)"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "tmux session '$SESSION' on $NODE"
        pgrep -u "$USER" -af "radical-orbit-endpoint.py --name $NAME " || echo "  (no endpoint process)"
    elif [[ -n "$held" && "$held" != "$NODE" ]]; then
        echo "no session on $NODE; last started on $held"
    else
        echo "not running"
    fi
    [[ -f "$LOG" ]] && grep "registered as '$NAME'" "$LOG" | tail -1 || true
}

cmd_logs() {
    [[ -f "$LOG" ]] || die "no log at $LOG"
    if [[ "${1:-}" == "-f" ]]; then tail -f "$LOG"; else tail -n 50 "$LOG"; fi
}

selftest_cleanup() {
    # shellcheck disable=SC2086
    kill $epid $bpid 2>/dev/null || true
    wait 2>/dev/null || true
    # Anything still holding the throwaway cert was started by this run.
    pkill -u "$USER" -f -- "--cert $ST_DIR/cert.pem" 2>/dev/null || true
    rm -rf "$ST_DIR"
}

cmd_selftest() {
    [[ -x "$ENDPOINT" ]] || die "no $ENDPOINT — run '$0 install'"
    local broker="$VENV/bin/radical-orbit-broker.py"
    local dir name port token
    mkdir -p "/scratch/$USER"
    dir="$(mktemp -d "/scratch/$USER/orbit-selftest.XXXXXX")"
    name="selftest-$NODE-$$"
    # Globals, not locals: the EXIT trap runs after this function's scope is gone.
    ST_DIR="$dir"; bpid=""; epid=""
    trap selftest_cleanup EXIT

    port="$("$VENV/bin/python" -c \
        'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
    openssl req -x509 -newkey rsa:2048 -nodes -keyout "$dir/key.pem" -out "$dir/cert.pem" \
        -days 1 -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1" 2>/dev/null
    chmod 600 "$dir/key.pem"
    token="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"

    echo "broker   127.0.0.1:$port (throwaway, auth on, files in $dir)"
    RADICAL_ORBIT_LOG_FILE="$dir/broker.log" RADICAL_ORBIT_BROKER_TOKEN="$token" \
        "$broker" --host 127.0.0.1 --port "$port" -p sysinfo \
        --cert "$dir/cert.pem" --key "$dir/key.pem" > "$dir/broker.out" 2>&1 &
    bpid=$!
    for _ in $(seq 30); do tcp_probe 127.0.0.1 "$port" && break; sleep 1; done
    tcp_probe 127.0.0.1 "$port" || { tail -20 "$dir/broker.out"; die "broker did not start"; }

    echo "endpoint '$name' -p $PLUGINS"
    mkdir -p "$RADICAL_ORBIT_PSIJ_DIR"
    RADICAL_ORBIT_LOG_FILE="$dir/endpoint.log" RADICAL_ORBIT_BROKER_TOKEN="$token" \
        "$ENDPOINT" --name "$name" -p "$PLUGINS" --url "https://127.0.0.1:$port" \
        --cert "$dir/cert.pem" > "$dir/endpoint.out" 2>&1 &
    epid=$!

    local t0=$SECONDS line=""
    for _ in $(seq 60); do
        kill -0 "$epid" 2>/dev/null || { tail -30 "$dir/endpoint.out"; die "endpoint exited"; }
        line="$(grep "registered as '$name'" "$dir/endpoint.log" 2>/dev/null | tail -1 || true)"
        [[ -n "$line" ]] && break
        sleep 1
    done
    [[ -n "$line" ]] || { tail -30 "$dir/endpoint.out"; die "no registration within 60 s"; }
    echo "registered after $((SECONDS - t0)) s:"
    echo "  ${line#*] }"

    local body
    body="$(curl -s --cacert "$dir/cert.pem" -H "Authorization: Bearer $token" \
        "https://127.0.0.1:$port/endpoints")"
    echo "GET /endpoints: $body"
    NAME_="$name" BODY_="$body" WANT_="$PLUGINS" "$VENV/bin/python" - <<'EOF'
import json, os, sys
eps = {e["name"]: e for e in json.loads(os.environ["BODY_"])["endpoints"]}
e = eps.get(os.environ["NAME_"])
if not e:
    sys.exit("FAIL: endpoint not listed by the broker")
missing = [p for p in os.environ["WANT_"].split(",") if p not in e["plugins"]]
if not e["connected"] or missing:
    sys.exit(f"FAIL: connected={e['connected']} missing plugins={missing}")
print(f"PASS: connected, plugins={e['plugins']}")
EOF

    # Auth is actually on: a wrong token must be refused.
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$dir/cert.pem" \
        -H "Authorization: Bearer wrong" "https://127.0.0.1:$port/endpoints")"
    [[ "$code" == 401 || "$code" == 403 ]] \
        && echo "PASS: wrong token refused ($code)" \
        || die "wrong token got HTTP $code"
}

case "${1:-}" in
    install)  cmd_install ;;
    check)    cmd_check ;;
    start)    cmd_start ;;
    run)      cmd_run ;;
    stop)     cmd_stop ;;
    status)   cmd_status ;;
    logs)     shift; cmd_logs "$@" ;;
    selftest) cmd_selftest ;;
    *)        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
