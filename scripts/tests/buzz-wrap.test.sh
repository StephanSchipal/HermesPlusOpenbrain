#!/bin/sh
# Wrapper resolves the agent key from <keydir>/<profile>.key and injects it as
# BUZZ_PRIVATE_KEY into buzz.real ONLY; falls back default->default.key; never
# leaks the key into its own environment.
set -eu
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/keys"
# stub buzz.real: print the env vars + args it received
cat > "$TMP/bin/buzz.real" <<'EOF'
#!/bin/sh
echo "KEY=${BUZZ_PRIVATE_KEY:-NONE}"
echo "RELAY=${BUZZ_RELAY_URL:-NONE}"
echo "ARGS=$*"
EOF
chmod +x "$TMP/bin/buzz.real"
printf 'coderkey111\n'   > "$TMP/keys/coder.key"
printf 'defaultkey000\n' > "$TMP/keys/default.key"

WRAP="sh $(pwd)/scripts/buzz-wrap.sh"
export BUZZ_WRAP_KEYDIR="$TMP/keys" BUZZ_WRAP_REAL="$TMP/bin/buzz.real"
export BUZZ_WRAP_RELAY="wss://relay.example"

# profile from HERMES_PROFILE
out=$(HERMES_PROFILE=coder $WRAP messages send --content hi)
echo "$out" | grep -q 'KEY=coderkey111' || { echo "FAIL: coder key not injected: $out"; exit 1; }
echo "$out" | grep -q 'RELAY=wss://relay.example' || { echo "FAIL: relay not set: $out"; exit 1; }
echo "$out" | grep -q 'ARGS=messages send --content hi' || { echo "FAIL: args not forwarded: $out"; exit 1; }

# profile from HERMES_HOME basename
out=$(HERMES_HOME=/opt/data/profiles/coder $WRAP x)
echo "$out" | grep -q 'KEY=coderkey111' || { echo "FAIL: HERMES_HOME path: $out"; exit 1; }

# base profile: HERMES_HOME=/opt/data -> default.key
out=$(HERMES_HOME=/opt/data $WRAP x)
echo "$out" | grep -q 'KEY=defaultkey000' || { echo "FAIL: default fallback: $out"; exit 1; }

# caller-supplied BUZZ_RELAY_URL wins over the wrapper default
out=$(HERMES_PROFILE=coder BUZZ_RELAY_URL=wss://override $WRAP x)
echo "$out" | grep -q 'RELAY=wss://override' || { echo "FAIL: caller relay not respected: $out"; exit 1; }

# no key file -> wrapper still execs (buzz.real errors on its own), no crash
out=$(HERMES_PROFILE=ghost $WRAP x 2>&1 || true)
echo "$out" | grep -q 'KEY=NONE' || { echo "FAIL: missing-key case: $out"; exit 1; }

# wrapper's own env must not carry the key
HERMES_PROFILE=coder $WRAP x >/dev/null 2>&1
[ "$(env | grep -c '^BUZZ_PRIVATE_KEY=')" = "0" ] || { echo "FAIL: key leaked to caller env"; exit 1; }

echo "PASS"
