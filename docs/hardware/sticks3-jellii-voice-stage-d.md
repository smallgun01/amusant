# H0.3 Stage D — Opt-in Jellii Persona + Zephyr Voice

**Scope:** Only Gemini Live session configuration for the existing Amusant Fast Voice path. This change does not authorize AWS changes, a device flash, public ingress, a paid Gemini request, or a PR merge. Stage C physical transport remains the known-good baseline.

## Why the Kai and Amusant Relay trees differ

The pinned upstream Kai v0.12.0 Relay uses `kai_relay/persona.py` and `kai_relay/session.py` to supply a system prompt and selected prebuilt voice. Amusant independently implements the device wire protocol and keeps its Fast Voice provider adapter in `fast_voice/`, with task admission and Inbox in `relay/`. Transport compatibility does not copy upstream Kai's persona or its weather, fishing, Google Search, notes, or reminders tools.

Stage D introduces an **Amusant-owned, generic Jellii Fast Voice identity** in `relay/companion_persona.py`, opt-in Gemini prebuilt voice, and opt-in Google Search Grounding in `fast_voice/factory.py`. This is **not** a byte-for-byte copy of any owner's locally customized Kai prompt. The selected Slow Backend (Hermes Native Runs by default) has separate agent instructions and future Companion Memory.

## Local opt-in and rollback

On the Ubuntu machine already proven by Stage C, keep the **same** private-LAN TLS-PSK listener, bearer/PSK credentials, and pinned Kai StickS3 firmware. The Relay now defaults to **Hermes Native Runs** as the Slow Backend (PR #45); this is independent of Fast Voice, but normal Relay startup **requires** securely configured `AMUSANT_HERMES_RUNS_URL` and `AMUSANT_HERMES_API_KEY`. Do not select the OpenAB legacy mode merely to bypass Hermes startup validation. Stop the current Relay before restarting it with the Fast Voice settings below:

```bash
export AMUSANT_PERSONA_PROFILE=jellii
export AMUSANT_GEMINI_VOICE=Zephyr
export AMUSANT_GEMINI_GOOGLE_SEARCH=1

# Select the product backend explicitly; this is already the default.
export AMUSANT_SLOW_EXECUTOR=hermes_runs

# This is a normal Relay startup and requires the existing, securely
# configured Hermes Runs endpoint/API key; do not paste those secrets.
# Use the private Stage C device listener and existing secure credentials.
# Example, from repository root (RELAY_LAN_IP is your existing private host):
uv run --locked --python 3.13 --extra hardware-opus python -m relay.live_bridge --device-mode sticks3 --host "$RELAY_LAN_IP"
```

The example assumes the Stage C device credentials, `GEMINI_API_KEY`, task-store settings, `RELAY_LAN_IP`, and valid Hermes Runs connection settings are **already securely available** in the Relay process environment. Do not paste or commit their values. Do not start a second listener on port 8443. If Hermes has not been configured locally, **do not run this physical launch command yet**; offline provider/unit tests below still work without a backend. A dedicated Fast-only/disabled Slow mode is a separate design task and is **not implemented** in this PR.

- `AMUSANT_PERSONA_PROFILE=jellii` adds the generic Jellii system instruction to both the normal tool-enabled Gemini session and the tool-free spoken Inbox session.
- `AMUSANT_GEMINI_VOICE=Zephyr` selects the Gemini Live prebuilt voice for either session. This is independent of the persona profile and does **not** read Kai's `KAI_VOICE`.
- `AMUSANT_GEMINI_GOOGLE_SEARCH=1` adds Gemini-managed Google Search Grounding to the **normal** Fast Voice session, alongside the existing `delegate_task` and `check_task_status` function tools. Default is `0`. Grounding is NEVER added to the tool-free Inbox result speech session. Search may incur extra Google API usage charges.
- Each Fast Voice setting is independent. With **all three unset**, the prior Stage C `LiveConnectConfig` remains unchanged. Slow Backend selection is separate, with Hermes as the product default.
- An unknown persona profile, empty/invalid voice name, or invalid injected system instruction fails closed. Actual Gemini model/voice support must still be confirmed with a separately approved live test.

To roll back only the Fast Voice changes, stop Amusant, `unset AMUSANT_PERSONA_PROFILE AMUSANT_GEMINI_VOICE AMUSANT_GEMINI_GOOGLE_SEARCH`, and restart the **current Hermes-configured Relay** with the same trusted listener and device credentials. This returns Gemini Live settings to Stage C behavior without undoing PR #45's Hermes backend selection. No firmware reflash or secret rotation is required.

## Offline regression (no API calls)

From the repository root:

```bash
uv run --locked --python 3.13 --extra hardware-opus --group dev python -m unittest discover -s fast_voice/tests -v
PYTHONPATH=.:relay uv run --locked --python 3.13 --extra hardware-opus --group dev python -m unittest discover -s relay/tests -v
PYTHONPATH=. uv run --locked --python 3.13 --extra hardware-opus --group dev python relay/tests/psk_wss_integration.py
```

Tests cover default-off configuration, invalid profile/voice/search flag, actual SDK config construction without network, coexistence of native Google Search Grounding with function declarations and manual PTT activity detection, isolation of the tool-free Inbox spoken session, and persona parity between sessions.

## Separately authorized physical Stage D check

After offline CI passes and the owner approves Gemini usage, perform 3–5 short battery-powered PTT turns. Confirm the selected voice sounds like Zephyr, natural Taiwan Traditional Chinese wording, Jellii's short conversational style, no invented long-term memory, and a working barge-in. With `AMUSANT_GEMINI_GOOGLE_SEARCH=1`, separately ask a simple time-sensitive question that cannot be answered reliably from model memory, confirm an actual grounded answer, and verify that the existing slow-task tool configuration still connects. Offline SDK construction alone does **not** prove that the selected Gemini Live model accepts both tool types in a real session; if a live connect rejects the configuration, disable only the search flag and capture sanitized provider error codes before further changes. Record any audible changes or errors. Do **not** claim Stage D live PASS from offline tests.

This PR does **not** modify Hermes selection/routing, implement backend persona synchronization, Companion Memory, proactive speech, scheduling, or any additional Relay Function Tools. It **does** add opt-in Gemini-native Google Search Grounding. The real Slow Task/Inbox path still requires its own E2E validation.
