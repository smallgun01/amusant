# Amusant StickS3 consolidated baseline (candidate)

This source-only candidate combines the six production layers on Kai v0.12.0
`1681fb3c11acd28525d8987bba4b9bf4bc2a288f`: Jellii, W1, W1.5,
production CJK, W2b-1 and the owner-accepted W2b-QR. The opt-in CJK
diagnostic patch is excluded. It preserves Kai's licensing and the MIT notice
in the vendored Nayuki QR encoder. No device flashing or NVS reset is implied.

On a clean Linux checkout, with this repository as the working directory:

```sh
git clone --no-tags https://github.com/zbruceli/kai.git kai
git -C kai checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C kai apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C kai apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
cp kai/firmware/src/secrets.example.h kai/firmware/src/secrets.h
pio run -d kai/firmware -e sticks3
pio run -d kai/firmware -e sticks3_ble_diag
rm kai/firmware/src/secrets.h
```

Use PlatformIO Core 6.2.0 and the versions pinned by Kai's `platformio.ini`.
The example secrets are placeholders, never deployment credentials. Build hashes
are not assumed reproducible: toolchain or timestamps can affect binary output.

## Provenance and rollback

The original patches and `patches/sticks3-w15-cjk/apply_integration.py` remain
unchanged. Their ordered reconstruction is documented in
`patches/kai-v0.12.0-wifi-w2bqr/README.md` and the existing integration workflow.
Keep the owner-approved known-good firmware backup outside this repository and
retain it until separately approved physical acceptance. See issues #47 and #67;
source equivalence does not establish full physical acceptance.

For the initial offline comparison, the old chain and the single patch were
applied independently to clean copies of the pinned revision. With LF checkout,
`git add -N . && git diff --binary --no-ext-diff` produced SHA-256
`cfb9cd82bcc108fc2d80d1f57fe6b3e0295d4491ed591c0e75345a1af093b988`
for each; no source deviations were observed. The Windows run forced LF output
for the integration script's text writes to match the Linux workflow. The
existing CJK header contains an extra blank line at EOF, retained for equivalence.
Repeat the comparison on Linux with:

```sh
bash patches/sticks3-w15-cjk/verify-equivalence.sh /path/to/kai-clone
```

The expected SHA-256 for each canonical source diff is
`cfb9cd82bcc108fc2d80d1f57fe6b3e0295d4491ed591c0e75345a1af093b988`.
Source parity is a source packaging gate; it is neither a firmware binary hash
nor a fresh physical QR 5/5 acceptance. Do not flash or reset NVS in this PR.
The production workflow remains unchanged and this candidate is not the default;
closing #136 and cutover require separate evidence and approval.
