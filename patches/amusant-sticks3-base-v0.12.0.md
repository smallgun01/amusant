# Amusant StickS3 consolidated baseline

The current patch preserves the six-layer baseline and makes TLS-PSK mandatory:
missing identity, key or bearer stops compilation, and there is no plaintext
WebSocket branch. Empty, malformed and placeholder credentials are not reliably
identified by compilation: run the preflash check before an owner build.

For Gate 3I, the owner privately sets `RELAY_HOST` to `jellii.k100.uk` and
`RELAY_TLS_PORT` to `443`, preserving the known-good PSK and Bearer. Run
`python patches/sticks3-baseline/preflash.py /private/path/kai/firmware/src/secrets.h`.
It accepts only this Gate 3I hostname and port, simple literal definitions, and
rejects placeholder or malformed values without printing them. A PASS checks
format, not whether credentials match the server or are genuinely secret.
A production build must stop immediately on preflash or build failure, and must
use a fresh isolated build directory: an earlier firmware.bin may remain after a
failed incremental build. Verify the successful build and image size before
separate Owner flash authorization. Placeholder CI images are test-only, are not
release artifacts, and must never be flashed.
Never commit secrets, build outputs, caches or flash backups. Build with pinned
PlatformIO and preserve the last owner-verified OCI-IP app for rollback.
Do not flash without separate owner approval. Physical DNS/WSS acceptance and
#67 LCD acceptance remain open.

The original source parity record below is historical; the current patch has
one explicitly verified TLS hardening difference from the layered reconstruction.

This source-only candidate combines the six production layers on Kai v0.12.0
`1681fb3c11acd28525d8987bba4b9bf4bc2a288f`: Jellii, W1, W1.5,
production CJK, W2b-1 and the owner-accepted W2b-QR. The opt-in CJK
diagnostic patch is excluded. It preserves Kai's licensing and the MIT notice
in the vendored Nayuki QR encoder. No device flashing or NVS reset is implied.

For placeholder-only CI testing, on a clean Linux checkout with this repository
as the working directory (the resulting image is not flashable for deployment):

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
bash patches/sticks3-baseline/verify-equivalence.sh /path/to/kai-clone
```

The expected SHA-256 for each canonical source diff is
`cfb9cd82bcc108fc2d80d1f57fe6b3e0295d4491ed591c0e75345a1af093b988`.
Source parity is a source packaging gate; it is neither a firmware binary hash
nor a fresh physical QR 5/5 acceptance. Do not flash or reset NVS in this PR.
The original parity hash above predates TLS hardening; the parity script verifies the exact intentional difference and compares the remaining source. The layered reconstruction remains the rollback source;
closing #136 and cutover require separate evidence and approval.
