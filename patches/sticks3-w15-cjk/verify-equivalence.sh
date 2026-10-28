#!/usr/bin/env bash
# Compare independent reconstructions from a local clone containing the pinned commit.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
upstream=${1:?usage: verify-equivalence.sh /path/to/kai-clone}
rev=1681fb3c11acd28525d8987bba4b9bf4bc2a288f
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
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
for name in layered consolidated; do
  git -C "$tmp/$name" add -N .
  git -C "$tmp/$name" -c core.quotePath=true diff --binary --no-ext-diff > "$tmp/$name.diff"
done
sha256sum "$tmp/layered.diff" "$tmp/consolidated.diff"
cmp "$tmp/layered.diff" "$tmp/consolidated.diff"
echo 'PASS: canonical source diffs are identical'
