# shellcheck shell=bash
# mt5-ini.sh: sourced by mt5-gold-profile.sh and mt5-experts-enable.sh (never run on its own). MT5 writes its ini,
# profile and template files as UTF-16LE with a BOM and CRLF line ends; these helpers edit them as UTF-8/LF and write
# them back in MT5's encoding, atomically (a temp file beside the target, then mv). They never print file contents
# (common.ini holds the login).

mt5_read() {  # mt5_read <mt5-file> <utf8-dst>: the file as UTF-8 with LF line ends
  iconv -f UTF-16 -t UTF-8 "$1" | tr -d '\r' >"$2"
}

to_mt5() {  # to_mt5 <utf8-src> <dst>: UTF-16LE + BOM + CRLF, written atomically
  local tmp
  tmp=$(mktemp "$2.XXXXXX")
  { printf '\xff\xfe'; sed 's/$/\r/' "$1" | iconv -f UTF-8 -t UTF-16LE; } >"$tmp"
  mv -f "$tmp" "$2"
}

ini_set() {  # ini_set <utf8-file> <section> <key> <value>: in place; a missing key goes to its section's end, a
             # missing section to the file's end
  local f=$1 sec=$2 key=$3 val=$4 tmp
  tmp=$(mktemp "$f.XXXXXX")          # a fresh file beside the target: never follows a planted "$f.new" symlink
  awk -v sec="[$sec]" -v key="$key" -v val="$val" '
    function flush() { if (insec && !done) { print key "=" val; done = 1 } }
    /^\[/ { flush(); insec = ($0 == sec) }
    insec && index($0, key "=") == 1 { if (!done) print key "=" val; done = 1; next }
    { print }
    END { flush(); if (!done) { print sec; print key "=" val } }' "$f" >"$tmp"
  mv -f "$tmp" "$f"
}

set_ini() {  # set_ini <mt5-file> <section> <key> <value>: ini_set on an MT5-encoded file
  local u
  u=$(mktemp)
  mt5_read "$1" "$u"
  ini_set "$u" "$2" "$3" "$4"
  to_mt5 "$u" "$1"
  rm -f "$u"
}
