#!/usr/bin/env bash
# Compare independent reconstructions from a local clone containing the pinned commit.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
upstream=${1:?usage: verify-equivalence.sh /path/to/kai-clone}
rev=1681fb3c11acd28525d8987bba4b9bf4bc2a288f
if [[ -n "${VERIFY_WORK_DIR:-}" ]]; then
  tmp=$VERIFY_WORK_DIR
  mkdir -p "$tmp"
  [[ -z "$(ls -A "$tmp")" ]] || { echo "VERIFY_WORK_DIR must be empty" >&2; exit 1; }
else
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
fi
for name in layered consolidated; do
  git -c core.autocrlf=false clone --quiet --no-checkout --shared "$upstream" "$tmp/$name"
  git -C "$tmp/$name" -c core.autocrlf=false checkout --quiet --detach "$rev"
done
old=$tmp/layered
for p in kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch; do
  git -C "$old" apply --check "$repo/patches/$p"
  git -C "$old" apply "$repo/patches/$p"
done
python3 "$repo/patches/sticks3-w15-cjk/apply_integration.py" --kai "$old"
for p in kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch kai-v0.12.0-wifi-w2bqr/kai-v0.12.0-wifi-w2bqr.patch; do
  git -C "$old" apply --check "$repo/patches/$p"
  git -C "$old" apply "$repo/patches/$p"
done
patch=$repo/patches/amusant-sticks3-base-v0.12.0.patch
git -C "$tmp/consolidated" apply --check "$patch"
git -C "$tmp/consolidated" apply "$patch"
# Verify the only intended difference, then compare the remaining source trees.
python3 - "$old/firmware/src/main.cpp" "$tmp/consolidated/firmware/src/main.cpp" <<'PYCODE'
from pathlib import Path
import sys
old, new = map(Path, sys.argv[1:])
s = old.read_text()
assert s.count('#include "secrets.h"') == 1
s = s.replace('#include "secrets.h"', '#include "secrets.h"\n\n#if !defined(KAI_PSK_HEX) || !defined(KAI_PSK_IDENTITY) || !defined(KAI_DEVICE_TOKEN)\n#error "TLS-PSK identity, key and device token are required"\n#endif\nstatic_assert(sizeof(KAI_PSK_IDENTITY) > 1, "TLS-PSK identity must not be empty");\nstatic_assert(sizeof(KAI_PSK_HEX) == 65, "TLS-PSK key must contain 64 characters");\nstatic_assert(sizeof(KAI_DEVICE_TOKEN) > 1, "Device token must not be empty");', 1)
assert s.count('#ifdef KAI_PSK_HEX\n  // Encrypted') == 1
s = s.replace('#ifdef KAI_PSK_HEX\n  // Encrypted', '  // Encrypted', 1)
plain = '#else\n#warning "No KAI_PSK_HEX in secrets.h: the link to the relay is unencrypted"\n  ws.begin(host, RELAY_PORT, "/ws");\n#endif\n  ws.setExtraHeaders'
assert s.count(plain) == 1
s = s.replace(plain, '  ws.setExtraHeaders', 1)
assert s == new.read_text(), 'Unexpected firmware source changes'
PYCODE
cp "$tmp/consolidated/firmware/src/main.cpp" "$tmp/hardened-main.cpp"
cp "$old/firmware/src/main.cpp" "$tmp/consolidated/firmware/src/main.cpp"
for name in layered consolidated; do
  git -C "$tmp/$name" add -N .
  # Existing CJK header contains one blank EOF line; compare its exact bytes below.
  git -C "$tmp/$name" diff --check -- . ':(exclude)firmware/src/jellii_text_utf8.h'
  git -C "$tmp/$name" -c core.whitespace=-blank-at-eof diff --check -- firmware/src/jellii_text_utf8.h
  git -C "$tmp/$name" -c core.quotePath=true diff --binary --no-ext-diff > "$tmp/$name.diff"
done
sha256sum "$tmp/layered.diff" "$tmp/consolidated.diff"
cmp "$tmp/layered.diff" "$tmp/consolidated.diff"
printf '%s  %s\n' cfb9cd82bcc108fc2d80d1f57fe6b3e0295d4491ed591c0e75345a1af093b988 "$tmp/consolidated.diff" | sha256sum --check
cp "$tmp/hardened-main.cpp" "$tmp/consolidated/firmware/src/main.cpp"
echo 'PASS: source parity apart from the verified TLS-PSK hardening'
