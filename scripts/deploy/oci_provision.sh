#!/usr/bin/env bash
# goldbot demo infrastructure on Oracle Cloud Always Free, with the OCI CLI (docs/deploy/first-deploy.md P2).
#
#   scripts/deploy/oci_provision.sh [--dry-run] [--status] [--micro-brain] [--tries N] [--interval S]
#                                   [--region R] [--profile P]
#
# Creates or reuses (found by display name, so re-running is safe):
#   goldbot-vcn (10.0.0.0/16), goldbot-igw, goldbot-rt (0.0.0.0/0 -> igw), goldbot-sl, goldbot-public (10.0.0.0/24)
#   goldbot-mt5    VM.Standard.E2.1.Micro, Ubuntu 24.04 x86_64, 50 GB boot
#   goldbot-brain  VM.Standard.A1.Flex 2 OCPU / 12 GB, Ubuntu 24.04 aarch64, 50 GB boot
#                  (--micro-brain: a second VM.Standard.E2.1.Micro instead)
#
# Security list goldbot-sl (the subnet uses only this list, not the VCN's default one, which opens 22 to the world):
#   ingress TCP 22   from the owner's current public IPv4 /32 (curl -4 https://ifconfig.me; re-run to refresh it)
#   ingress TCP 22   from the subnet CIDR: the brain's bridge tunnel is `ssh -L 8765:127.0.0.1:8765 mt5@<mt5-private-ip>`
#                    (goldbot-bridge-tunnel.service), so the private network needs SSH, not the bridge port
#   the bridge port 8765 is NOT opened, not even to the VCN: the bridge binds 127.0.0.1 and is reached only
#   through that tunnel
#   ingress ICMP 3/4 from anywhere and ICMP 3 from the subnet (path-MTU discovery, as in OCI's default list)
#   egress  all
#
# Nothing secret goes to the terminal: no IP addresses (OCI errors included: IPv4 -> <ip>), OCIDs shortened.
# Public and private IPs are written to ~/.goldbot-deploy/hosts.env (mode 600), and ssh Host blocks goldbot-brain /
# goldbot-mt5 to ~/.ssh/goldbot.conf (mode 600; ~/.ssh/config is never edited: add `Include ~/.ssh/goldbot.conf`
# at its top yourself, and check each host key on the first connect). Refuses any shape or size outside Always Free.
# Needs: oci (configured with `oci setup config`), jq, curl, ~/.ssh/id_ed25519.pub.
set -euo pipefail

# ---- fixed plan (Always Free) ------------------------------------------------------------------------------------
VCN_NAME=goldbot-vcn;   VCN_CIDR=10.0.0.0/16;  VCN_DNS=goldbot
IGW_NAME=goldbot-igw;   RT_NAME=goldbot-rt;    SL_NAME=goldbot-sl
SUBNET_NAME=goldbot-public; SUBNET_CIDR=10.0.0.0/24; SUBNET_DNS=pub
BRAIN_NAME=goldbot-brain; MT5_NAME=goldbot-mt5
A1_SHAPE=VM.Standard.A1.Flex; MICRO_SHAPE=VM.Standard.E2.1.Micro
BRAIN_OCPUS=${BRAIN_OCPUS:-2}; BRAIN_MEMORY_GB=${BRAIN_MEMORY_GB:-12}; BOOT_GB=${BOOT_GB:-50}
# Always Free ceilings (first-deploy.md P2: 2 OCPU / 12 GB is the whole A1 allowance now; 2 micros; 200 GB storage)
FREE_A1_OCPUS=2; FREE_A1_MEM_GB=12; FREE_MICROS=2; FREE_STORAGE_GB=200; MIN_BOOT_GB=47
SSH_PUB=${SSH_PUB:-$HOME/.ssh/id_ed25519.pub}
OUT_DIR=$HOME/.goldbot-deploy; HOSTS_ENV=$OUT_DIR/hosts.env; SSH_CONF=$HOME/.ssh/goldbot.conf
MIN_INTERVAL=30
OCI_CONFIG=${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}

REGION=uk-london-1; PROFILE=${OCI_CLI_PROFILE:-DEFAULT}
DRY_RUN=0; STATUS=0; MICRO_BRAIN=0; TRIES=60; INTERVAL=60

usage() { sed -n '4,5p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --status) STATUS=1 ;;
    --micro-brain) MICRO_BRAIN=1 ;;
    --tries) TRIES=${2:?--tries N}; shift ;;
    --interval) INTERVAL=${2:?--interval seconds}; shift ;;
    --region) REGION=${2:?--region R}; shift ;;
    --profile) PROFILE=${2:?--profile P}; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
  shift
done
[[ "$TRIES" =~ ^[1-9][0-9]*$ ]] || { echo "--tries must be a positive integer" >&2; exit 2; }
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] && [ "$INTERVAL" -ge "$MIN_INTERVAL" ] \
  || { echo "--interval must be a whole number of seconds >= $MIN_INTERVAL" >&2; exit 2; }

exec 3>&1                      # fd 3 = the terminal; command substitutions capture only fd 1
log()  { printf '%s\n' "$*" >&3; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
short() { sed -E 's/(ocid1\.[a-z0-9]+)\.[A-Za-z0-9._-]*([A-Za-z0-9]{6})/\1...\2/g'; }   # OCIDs never printed whole
redact() { short | sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g'; }                      # nor IPv4 addresses
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/oci_provision.XXXXXX")                               # all temp files live here
trap 'rm -rf "$TMPD"' EXIT

# ---- Always Free guard (before any call) --------------------------------------------------------------------------
refuse() { die "refused, outside Always Free: $*"; }
[[ "$BRAIN_OCPUS" =~ ^[0-9]+$ && "$BRAIN_MEMORY_GB" =~ ^[0-9]+$ && "$BOOT_GB" =~ ^[0-9]+$ ]] \
  || die "BRAIN_OCPUS, BRAIN_MEMORY_GB and BOOT_GB must be whole numbers"
[ "$BRAIN_OCPUS" -ge 1 ] && [ "$BRAIN_OCPUS" -le "$FREE_A1_OCPUS" ] || refuse "A1 with $BRAIN_OCPUS OCPU (max $FREE_A1_OCPUS)"
[ "$BRAIN_MEMORY_GB" -ge 1 ] && [ "$BRAIN_MEMORY_GB" -le "$FREE_A1_MEM_GB" ] \
  || refuse "A1 with $BRAIN_MEMORY_GB GB (max $FREE_A1_MEM_GB)"
[ "$BOOT_GB" -ge "$MIN_BOOT_GB" ] || die "boot volume $BOOT_GB GB is below OCI's minimum $MIN_BOOT_GB GB"
[ $((BOOT_GB * 2)) -le "$FREE_STORAGE_GB" ] || refuse "boot volume $BOOT_GB GB x 2 instances > $FREE_STORAGE_GB GB"
check_shape() { case "$1" in "$A1_SHAPE"|"$MICRO_SHAPE") ;; *) refuse "shape $1" ;; esac; }

# ---- config ---------------------------------------------------------------------------------------------------
cfg() {   # cfg <key>: value of key in the selected profile of ~/.oci/config
  awk -v p="[$PROFILE]" -v k="$1" '
    /^[[:space:]]*\[/ { gsub(/[[:space:]]/, ""); s = ($0 == p); next }
    s { line = $0; sub(/^[[:space:]]+/, "", line)
        if (index(line, k) == 1) { rest = substr(line, length(k) + 1); sub(/^[[:space:]]*=[[:space:]]*/, "", rest)
          if (rest != substr(line, length(k) + 1)) { sub(/[[:space:]]+$/, "", rest); print rest; exit } } }' "$OCI_CONFIG"
}
if [ -f "$OCI_CONFIG" ]; then
  TENANCY=$(cfg tenancy); CFG_REGION=$(cfg region)
  [ -n "$TENANCY" ] || die "no tenancy= in profile [$PROFILE] of $OCI_CONFIG"
elif [ "$DRY_RUN" = 1 ]; then
  TENANCY='<tenancy-ocid>'; CFG_REGION=''
  warn "$OCI_CONFIG not found (run: oci setup config); dry run uses placeholders"
else
  die "$OCI_CONFIG not found. Run: oci setup config"
fi
if [ -n "${CFG_REGION:-}" ] && [ "$CFG_REGION" != "$REGION" ]; then
  warn "config region is $CFG_REGION but this run targets $REGION (Always Free compute exists only in the home region)"
fi
COMPARTMENT=$TENANCY            # root compartment
for t in jq curl; do command -v "$t" >/dev/null || die "$t is required"; done
if [ "$DRY_RUN" = 0 ]; then command -v oci >/dev/null || die "oci CLI not found (brew install oci-cli)"; fi
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
export PYTHONWARNINGS=${PYTHONWARNINGS:-ignore::SyntaxWarning}   # oci-cli 3.95 on Python 3.15 warns on every call
if [ -e "$HOME/.oci/oci_cli_rc" ]; then
  warn "$HOME/.oci/oci_cli_rc exists: its defaults (profile, query, output, aliases) apply to every call here; move it aside if unsure"
fi

# ---- oci wrappers ---------------------------------------------------------------------------------------------
# ro: read-only call (skipped in dry run, returns nothing so every lookup reads "not found")
# rw: changing call (printed, not run, in dry run; prints a placeholder id)
OCI_BASE=(--region "$REGION" --profile "$PROFILE" --config-file "$OCI_CONFIG")
qarg() { case "$1" in *[!A-Za-z0-9_./:=@,+-]*|"") printf "'%s'" "${1//'/'\\''}" ;; *) printf "%s" "$1" ;; esac; }
show() { local a line="+ oci"; for a in "${OCI_BASE[@]}" "$@"; do line="$line $(qarg "$a")"; done; printf "%s\n" "$line" | short >&3; }
oci_run() {   # oci with stderr captured, then printed with OCIDs shortened and IPv4 addresses redacted
  local err rc=0
  err=$(mktemp "$TMPD/err.XXXXXX")
  oci "${OCI_BASE[@]}" "$@" 2>"$err" || rc=$?
  if [ -s "$err" ]; then redact <"$err" >&2; fi
  rm -f "$err"
  return "$rc"
}
ro() { if [ "$DRY_RUN" = 1 ]; then show "$@"; return 0; fi; oci_run "$@"; }
rw() { if [ "$DRY_RUN" = 1 ]; then show "$@"; return 0; fi; oci_run "$@"; }

# first id in .data whose display-name matches and whose state is not gone
by_name() {   # by_name <name> ; reads oci list JSON on stdin
  jq -r --arg n "$1" 'first((.data // [])[] | select(."display-name" == $n)
    | select((."lifecycle-state" // "") | test("TERMINAT|DELET") | not) | .id)'
}
created_id() { if [ "$DRY_RUN" = 1 ]; then printf '%s' "$1"; else jq -r '.data.id'; fi; }

# ---- lookups --------------------------------------------------------------------------------------------------
find_vcn()    { ro network vcn list --compartment-id "$COMPARTMENT" --all | by_name "$VCN_NAME"; }
find_igw()    { ro network internet-gateway list --compartment-id "$COMPARTMENT" --vcn-id "$1" --all | by_name "$IGW_NAME"; }
find_rt()     { ro network route-table list --compartment-id "$COMPARTMENT" --vcn-id "$1" --all | by_name "$RT_NAME"; }
find_sl()     { ro network security-list list --compartment-id "$COMPARTMENT" --vcn-id "$1" --all | by_name "$SL_NAME"; }
find_subnet() { ro network subnet list --compartment-id "$COMPARTMENT" --vcn-id "$1" --all | by_name "$SUBNET_NAME"; }
find_inst()   { ro compute instance list --compartment-id "$COMPARTMENT" --all | by_name "$1"; }
inst_field()  { ro compute instance get --instance-id "$1" | jq -r ".data.\"$2\""; }
list_ads()    { ro iam availability-domain list --compartment-id "$COMPARTMENT" | jq -r '(.data // [])[].name'; }

# ---- status ---------------------------------------------------------------------------------------------------
pm() { if [ -n "$1" ]; then echo present; else echo missing; fi; }
status() {
  [ "$DRY_RUN" = 0 ] || { log "(--status makes read-only calls; nothing to show in a dry run)"; return 0; }
  local vcn sub
  vcn=$(find_vcn)
  log "region $REGION"
  log "vcn     $VCN_NAME: $(pm "$vcn")"
  if [ -n "$vcn" ]; then
    for pair in "igw:$IGW_NAME" "rt:$RT_NAME" "sl:$SL_NAME" "subnet:$SUBNET_NAME"; do
      kind=${pair%%:*}; name=${pair#*:}
      case $kind in igw) sub=$(find_igw "$vcn");; rt) sub=$(find_rt "$vcn");; sl) sub=$(find_sl "$vcn");;
                    subnet) sub=$(find_subnet "$vcn");; esac
      log "$(printf '%-7s %s: %s' "$kind" "$name" "$(pm "$sub")")"
    done
  fi
  local name id
  for name in "$MT5_NAME" "$BRAIN_NAME"; do
    id=$(find_inst "$name")
    if [ -n "$id" ]; then
      log "$(printf '%-14s %s %s' "$name" "$(inst_field "$id" shape)" "$(inst_field "$id" lifecycle-state)")"
    else
      log "$(printf '%-14s missing' "$name")"
    fi
  done
  if [ -f "$HOSTS_ENV" ]; then log "IPs: $HOSTS_ENV ($(stat -f '%Lp' "$HOSTS_ENV" 2>/dev/null || stat -c '%a' "$HOSTS_ENV"))"
  else log "IPs: $HOSTS_ENV not written yet"; fi
}

# ---- Always Free usage check against what the tenancy already has ----------------------------------------------
free_tier_check() {   # free_tier_check <new-brain-shape|-> <new-mt5 0|1>
  [ "$DRY_RUN" = 0 ] || return 0
  local brain_shape=$1 new_mt5=$2 inst a1_ocpu a1_mem micros new_boot=0 used_gb ad
  inst=$(ro compute instance list --compartment-id "$COMPARTMENT" --all)
  a1_ocpu=$(jq -rs --arg s "$A1_SHAPE" '[.[] | (.data // [])[] | select(.shape == $s and (."lifecycle-state" | test("TERMINAT") | not))
            | ."shape-config".ocpus] | add // 0 | floor' <<<"$inst")
  a1_mem=$(jq -rs --arg s "$A1_SHAPE" '[.[] | (.data // [])[] | select(.shape == $s and (."lifecycle-state" | test("TERMINAT") | not))
           | ."shape-config"."memory-in-gbs"] | add // 0 | floor' <<<"$inst")
  micros=$(jq -rs --arg s "$MICRO_SHAPE" '[.[] | (.data // [])[] | select(.shape == $s and (."lifecycle-state" | test("TERMINAT") | not))]
           | length' <<<"$inst")
  local add_micro=$new_mt5
  if [ "$brain_shape" = "$A1_SHAPE" ]; then
    [ $((a1_ocpu + BRAIN_OCPUS)) -le "$FREE_A1_OCPUS" ] || refuse "A1 OCPUs would total $((a1_ocpu + BRAIN_OCPUS))"
    [ $((a1_mem + BRAIN_MEMORY_GB)) -le "$FREE_A1_MEM_GB" ] || refuse "A1 memory would total $((a1_mem + BRAIN_MEMORY_GB)) GB"
  elif [ "$brain_shape" = "$MICRO_SHAPE" ]; then
    add_micro=$((add_micro + 1))
  fi
  [ $((micros + add_micro)) -le "$FREE_MICROS" ] || refuse "E2.1.Micro instances would total $((micros + add_micro))"
  [ "$brain_shape" = - ] || new_boot=$((new_boot + BOOT_GB))
  [ "$new_mt5" = 0 ] || new_boot=$((new_boot + BOOT_GB))
  used_gb=0
  for ad in $(list_ads); do
    used_gb=$((used_gb + $(ro bv boot-volume list --compartment-id "$COMPARTMENT" --availability-domain "$ad" --all \
      | jq -rs '[.[] | (.data // [])[] | select(."lifecycle-state" | test("TERMINAT") | not) | ."size-in-gbs"] | add // 0 | floor')))
  done
  used_gb=$((used_gb + $(ro bv volume list --compartment-id "$COMPARTMENT" --all \
    | jq -rs '[.[] | (.data // [])[] | select(."lifecycle-state" | test("TERMINAT") | not) | ."size-in-gbs"] | add // 0 | floor')))
  [ $((used_gb + new_boot)) -le "$FREE_STORAGE_GB" ] \
    || refuse "block + boot storage would total $((used_gb + new_boot)) GB (free total $FREE_STORAGE_GB GB)"
  log "Always Free check ok: A1 ${a1_ocpu} OCPU in use, micros ${micros}, storage ${used_gb} GB in use + ${new_boot} GB new"
}

# ---- network --------------------------------------------------------------------------------------------------
owner_ip() {
  local ip
  local o1 o2 o3 o4 o
  ip=$(curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null) \
    || die "could not look up your public IPv4 address (curl -4 https://ifconfig.me)"
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "ifconfig.me did not return an IPv4 address"
  IFS=. read -r o1 o2 o3 o4 <<<"$ip"
  for o in "$o1" "$o2" "$o3" "$o4"; do
    [ "$((10#$o))" -le 255 ] || die "ifconfig.me returned an invalid IPv4 address (octet above 255)"
  done
  printf '%s' "$ip"
}
tcp_rule() {   # tcp_rule <source-cidr> <port> <description>
  jq -nc --arg s "$1" --argjson p "$2" --arg d "$3" \
    '{protocol:"6", source:$s, sourceType:"CIDR_BLOCK", isStateless:false, description:$d,
      tcpOptions:{destinationPortRange:{min:$p, max:$p}}}'
}
icmp_rule() {  # icmp_rule <source-cidr> <type> [code]
  jq -nc --arg s "$1" --argjson t "$2" --arg c "${3:-}" \
    '{protocol:"1", source:$s, sourceType:"CIDR_BLOCK", isStateless:false,
      icmpOptions:({type:$t} + (if $c == "" then {} else {code:($c|tonumber)} end))}'
}

network() {
  local vcn igw rt sl subnet ip ingress egress routes
  vcn=$(find_vcn)
  if [ -z "$vcn" ]; then
    log "create $VCN_NAME"
    vcn=$(rw network vcn create --compartment-id "$COMPARTMENT" --display-name "$VCN_NAME" \
      --cidr-blocks "[\"$VCN_CIDR\"]" --dns-label "$VCN_DNS" --wait-for-state AVAILABLE | created_id '<vcn-id>')
  else log "reuse $VCN_NAME"; fi

  igw=$(find_igw "$vcn")
  if [ -z "$igw" ]; then
    log "create $IGW_NAME"
    igw=$(rw network internet-gateway create --compartment-id "$COMPARTMENT" --vcn-id "$vcn" --display-name "$IGW_NAME" \
      --is-enabled true --wait-for-state AVAILABLE | created_id '<igw-id>')
  else log "reuse $IGW_NAME"; fi

  routes=$(jq -nc --arg g "$igw" '[{destination:"0.0.0.0/0", destinationType:"CIDR_BLOCK", networkEntityId:$g}]')
  rt=$(find_rt "$vcn")
  if [ -z "$rt" ]; then
    log "create $RT_NAME"
    rt=$(rw network route-table create --compartment-id "$COMPARTMENT" --vcn-id "$vcn" --display-name "$RT_NAME" \
      --route-rules "$routes" --wait-for-state AVAILABLE | created_id '<rt-id>')
  else
    log "reuse $RT_NAME (route 0.0.0.0/0 -> $IGW_NAME re-applied)"
    rw network route-table update --rt-id "$rt" --route-rules "$routes" --force --wait-for-state AVAILABLE >/dev/null
  fi

  if [ "$DRY_RUN" = 1 ]; then ip='<owner-ip>'; log "+ curl -4 -fsS --max-time 10 https://ifconfig.me"; else ip=$(owner_ip); fi
  ingress=$(jq -sc . <<EOF
$(tcp_rule "$ip/32" 22 "SSH from the owner")
$(tcp_rule "$SUBNET_CIDR" 22 "SSH inside the subnet: brain to mt5 bridge tunnel")
$(icmp_rule 0.0.0.0/0 3 4)
$(icmp_rule "$SUBNET_CIDR" 3)
EOF
)
  [ "$(jq length <<<"$ingress")" = 4 ] || die "security list: expected 4 ingress rules, built $(jq length <<<"$ingress")"
  egress='[{"protocol":"all","destination":"0.0.0.0/0","destinationType":"CIDR_BLOCK","isStateless":false}]'
  sl=$(find_sl "$vcn")
  if [ -z "$sl" ]; then
    log "create $SL_NAME (SSH from your IP /32 only)"
    sl=$(rw network security-list create --compartment-id "$COMPARTMENT" --vcn-id "$vcn" --display-name "$SL_NAME" \
      --ingress-security-rules "$ingress" --egress-security-rules "$egress" --wait-for-state AVAILABLE \
      | created_id '<sl-id>')
  else
    log "reuse $SL_NAME (rules re-applied with your current IP /32)"
    rw network security-list update --security-list-id "$sl" --ingress-security-rules "$ingress" \
      --egress-security-rules "$egress" --force --wait-for-state AVAILABLE >/dev/null
  fi

  subnet=$(find_subnet "$vcn")
  if [ -z "$subnet" ]; then
    log "create $SUBNET_NAME (public)"
    subnet=$(rw network subnet create --compartment-id "$COMPARTMENT" --vcn-id "$vcn" --display-name "$SUBNET_NAME" \
      --cidr-block "$SUBNET_CIDR" --dns-label "$SUBNET_DNS" --route-table-id "$rt" \
      --security-list-ids "[\"$sl\"]" --prohibit-public-ip-on-vnic false --wait-for-state AVAILABLE \
      | created_id '<subnet-id>')
  else
    log "reuse $SUBNET_NAME (route table and security list re-attached)"
    rw network subnet update --subnet-id "$subnet" --route-table-id "$rt" --security-list-ids "[\"$sl\"]" \
      --force --wait-for-state AVAILABLE >/dev/null
  fi
  SUBNET_ID=$subnet
}

# ---- compute --------------------------------------------------------------------------------------------------
latest_image() {   # latest_image <shape> <aarch64|x86_64>
  local id
  id=$(ro compute image list --compartment-id "$COMPARTMENT" --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" --shape "$1" --lifecycle-state AVAILABLE \
    --sort-by TIMECREATED --sort-order DESC --all \
    | jq -r --arg a "$2" 'first((.data // [])[] | select(."display-name" | test("Minimal") | not)
        | select(if $a == "aarch64" then (."display-name" | test("aarch64")) else (."display-name" | test("aarch64") | not) end)
        | .id)')
  if [ -z "$id" ]; then
    [ "$DRY_RUN" = 1 ] && { printf '<ubuntu-24.04-%s-image-id>' "$2"; return 0; }
    die "no Canonical Ubuntu 24.04 $2 image for $1 in $REGION"
  fi
  log "image for $1: $(ro compute image get --image-id "$id" | jq -r '.data."display-name"')"
  printf '%s' "$id"
}

ads_for_shape() {   # ADs in the region that offer <shape>
  local ad
  for ad in $(list_ads); do
    if ro compute shape list --compartment-id "$COMPARTMENT" --availability-domain "$ad" --all \
        | jq -e --arg s "$1" '(.data // []) | any(.shape == $s)' >/dev/null; then printf '%s\n' "$ad"; fi
  done
}

wait_running() {   # wait_running <name> <id>; polls every 15 s for up to 20 min (`instance get` has no --wait-for-state)
  [ "$DRY_RUN" = 0 ] || { show compute instance get --instance-id "$2"; log "  (polled every 15 s until RUNNING, max 20 min)"; return 0; }
  local state i
  log "wait for $1 RUNNING"
  for ((i = 0; i < 80; i++)); do
    state=$(inst_field "$2" lifecycle-state)
    case "$state" in
      RUNNING) log "$1 RUNNING"; return 0 ;;
      TERMINATING|TERMINATED) die "$1 went $state while starting" ;;
    esac
    sleep 15
  done
  die "$1 did not reach RUNNING in 20 min (state: $state)"
}

launch() {   # launch <name> <shape> <arch>; sets LAUNCHED_ID
  local name=$1 shape=$2 arch=$3 image ads ad try err out id cfg_args=()
  check_shape "$shape"
  if [ "$shape" = "$A1_SHAPE" ]; then
    cfg_args=(--shape-config "{\"ocpus\":$BRAIN_OCPUS,\"memoryInGBs\":$BRAIN_MEMORY_GB}")
  fi
  image=$(latest_image "$shape" "$arch")
  if [ "$DRY_RUN" = 1 ]; then
    ads='<each-availability-domain>'; ro iam availability-domain list --compartment-id "$COMPARTMENT"
  else
    ads=$(ads_for_shape "$shape"); [ -n "$ads" ] || die "no availability domain in $REGION offers $shape"
  fi
  for ((try = 1; try <= TRIES; try++)); do
    for ad in $ads; do
      # a launch can create the instance and still return an error: never launch a second (billed) copy
      id=$(find_inst "$name")
      if [ -n "$id" ]; then log "  $name already exists (an earlier attempt went through)"; LAUNCHED_ID=$id; return 0; fi
      log "launch $name ($shape) in AD ${ad##*:}, try $try/$TRIES"
      err=$(mktemp "$TMPD/launch.XXXXXX")
      if out=$(rw compute instance launch --compartment-id "$COMPARTMENT" --availability-domain "$ad" \
          --display-name "$name" --shape "$shape" "${cfg_args[@]+"${cfg_args[@]}"}" --image-id "$image" \
          --subnet-id "$SUBNET_ID" --assign-public-ip true --boot-volume-size-in-gbs "$BOOT_GB" \
          --ssh-authorized-keys-file "$SSH_PUB" 2>"$err"); then
        rm -f "$err"
        id=$(created_id "<$name-id>" <<<"$out")
        LAUNCHED_ID=$id; return 0
      fi
      # $err is already redacted by oci_run
      if grep -qiE 'out of host capacity|TooManyRequests' "$err"; then
        log "  no capacity: $(grep -oiE '"message": *"[^"]*"' "$err" | head -n 1 || echo 'see oci error')"
        rm -f "$err"
      else
        cat "$err" >&2; rm -f "$err"; die "launch of $name failed (not a capacity error)"
      fi
    done
    [ "$try" -lt "$TRIES" ] && { log "  waiting ${INTERVAL}s"; sleep "$INTERVAL"; }
  done
  id=$(find_inst "$name")
  if [ -n "$id" ]; then log "  $name already exists (an earlier attempt went through)"; LAUNCHED_ID=$id; return 0; fi
  die "$name: no capacity after $TRIES tries across all ADs (try later, PAYG, or --micro-brain)"
}

ensure_instance() {   # ensure_instance <name> <shape> <arch>
  local id state
  id=$(find_inst "$1")
  if [ -n "$id" ]; then
    state=$(inst_field "$id" lifecycle-state)
    log "reuse $1 ($(inst_field "$id" shape), $state)"
    if [ "$state" = STOPPED ]; then
      log "start $1"; rw compute instance action --instance-id "$id" --action START >/dev/null
    fi
  else
    launch "$1" "$2" "$3"; id=$LAUNCHED_ID
  fi
  wait_running "$1" "$id"
  eval "${4}=\$id"
}

vnic_ips() {   # vnic_ips <instance-id> -> "public|private" (either may be empty)
  ro compute instance list-vnics --instance-id "$1" \
    | jq -r '(.data // [])[0] | "\(."public-ip" // "")|\(."private-ip" // "")"'
}

ssh_host_block() {   # ssh_host_block <alias> <public-ip>
  printf 'Host %s\n  HostName %s\n  User ubuntu\n  IdentityFile ~/.ssh/id_ed25519\n  IdentitiesOnly yes\n' "$1" "$2"
  printf '  StrictHostKeyChecking accept-new\n\n'
}

write_hosts() {   # write_hosts <brain-id> <mt5-id>
  if [ "$DRY_RUN" = 1 ]; then
    show compute instance list-vnics --instance-id '<id>'
    log "would write $HOSTS_ENV and $SSH_CONF (mode 600)"; return 0
  fi
  local b m
  IFS='|' read -r BRAIN_PUB BRAIN_PRIV <<<"$(vnic_ips "$1")"
  IFS='|' read -r MT5_PUB MT5_PRIV <<<"$(vnic_ips "$2")"
  b=$(inst_field "$1" shape); m=$(inst_field "$2" shape)
  ( umask 077
    mkdir -p "$OUT_DIR" "$HOME/.ssh"; chmod 700 "$OUT_DIR" "$HOME/.ssh"
    tmp=$(mktemp "$OUT_DIR/hosts.env.XXXXXX")
    {
      echo "# goldbot demo hosts ($REGION), written by oci_provision.sh $(date -u +%FT%TZ). Keep off chats and the repo."
      echo "BRAIN_SHAPE=$b"; echo "BRAIN_PUBLIC_IP=$BRAIN_PUB"; echo "BRAIN_PRIVATE_IP=$BRAIN_PRIV"
      echo "MT5_SHAPE=$m";   echo "MT5_PUBLIC_IP=$MT5_PUB";     echo "MT5_PRIVATE_IP=$MT5_PRIV"
    } >"$tmp"
    chmod 600 "$tmp"; mv -f "$tmp" "$HOSTS_ENV"
    tmp=$(mktemp "$HOME/.ssh/goldbot.conf.XXXXXX")
    {
      echo "# goldbot demo hosts ($REGION), written by oci_provision.sh $(date -u +%FT%TZ). Keep off chats and the repo."
      echo "# Check each host key fingerprint on the first connect (accept-new trusts only the first key it sees)."
      echo "# MT5_PRIVATE_IP=$MT5_PRIV"
      echo
      if [ -n "$BRAIN_PUB" ]; then ssh_host_block goldbot-brain "$BRAIN_PUB"; fi
      if [ -n "$MT5_PUB" ]; then ssh_host_block goldbot-mt5 "$MT5_PUB"; fi
    } >"$tmp"
    chmod 600 "$tmp"; mv -f "$tmp" "$SSH_CONF" )
  if [ -z "$BRAIN_PUB" ] || [ -z "$MT5_PUB" ]; then
    warn "public IP missing for$([ -n "$BRAIN_PUB" ] || echo " $BRAIN_NAME")$([ -n "$MT5_PUB" ] || echo " $MT5_NAME"); see $HOSTS_ENV"
  fi
  log "IPs written to $HOSTS_ENV and ssh hosts to $SSH_CONF (mode 600)"
  if ! grep -qsE '^[[:space:]]*Include[[:space:]]+(~|"?'"$HOME"')/\.ssh/goldbot\.conf"?[[:space:]]*$' "$HOME/.ssh/config"; then
    log "add this line at the top of ~/.ssh/config (this script never edits it): Include ~/.ssh/goldbot.conf"
  fi
}

# ---- main -----------------------------------------------------------------------------------------------------
if [ "$STATUS" = 1 ]; then status; exit 0; fi

[ -f "$SSH_PUB" ] || die "$SSH_PUB not found (ssh-keygen -t ed25519)"
grep -qE '^ssh-ed25519 ' "$SSH_PUB" || die "$SSH_PUB is not an ssh-ed25519 public key"
[ "$DRY_RUN" = 1 ] && log "DRY RUN: nothing is created; commands are shown with OCIDs shortened"

if [ "$DRY_RUN" = 0 ]; then
  home=$(ro iam region-subscription list --tenancy-id "$TENANCY" | jq -r '(.data // [])[] | select(."is-home-region") | ."region-name"')
  [ "$home" = "$REGION" ] || refuse "region $REGION is not the tenancy's home region ($home): Always Free compute lives there"
else
  ro iam region-subscription list --tenancy-id "$TENANCY"
fi

if [ "$MICRO_BRAIN" = 1 ]; then BRAIN_SHAPE=$MICRO_SHAPE; BRAIN_ARCH=x86_64; else BRAIN_SHAPE=$A1_SHAPE; BRAIN_ARCH=aarch64; fi
check_shape "$BRAIN_SHAPE"; check_shape "$MICRO_SHAPE"
if [ "$DRY_RUN" = 0 ]; then
  new_brain=-; new_mt5=0
  [ -n "$(find_inst "$BRAIN_NAME")" ] || new_brain=$BRAIN_SHAPE
  [ -n "$(find_inst "$MT5_NAME")" ] || new_mt5=1
  free_tier_check "$new_brain" "$new_mt5"
fi

network
ensure_instance "$MT5_NAME" "$MICRO_SHAPE" x86_64 MT5_ID         # the micro first: the A1 may wait for capacity
ensure_instance "$BRAIN_NAME" "$BRAIN_SHAPE" "$BRAIN_ARCH" BRAIN_ID
write_hosts "$BRAIN_ID" "$MT5_ID"
log "done. Next: docs/deploy/first-deploy.md part 2 (ssh goldbot-brain / ssh goldbot-mt5; IPs in $HOSTS_ENV)"
