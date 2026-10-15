# H0.3 Stage D — Opt-in Jellii Persona + Zephyr Voice

**Scope:** Only Gemini Live session configuration for the existing Amusant Fast Voice path. This change does not authorize AWS changes, a device flash, public ingress, a paid Gemini request, or a PR merge. Stage C physical transport remains the known-good baseline.

## Why the Kai and Amusant Relay trees differ

The pinned upstream Kai v0.12.0 Relay uses `kai_relay/persona.py` and `kai_relay/session.py` to supply a system prompt and selected prebuilt voice. Amusant independently implements the device wire protocol and keeps its Fast Voice provider adapter in `fast_voice/`, with task admission and Inbox in `relay/`. Transport compatibility does not copy upstream Kai's persona or its weather, fishing, Google Search, notes, or reminders tools.

Stage D introduces an **Amusant-owned, generic Jellii Fast Voice identity** in `relay/companion_persona.py` and an opt-in Gemini prebuilt voice in `fast_voice/factory.py`. This is **not** a byte-for-byte copy of any owner's locally customized Kai prompt. OpenAB's agent persona and future long-term companion memory remain separate.

## Local opt-in and rollback

On the Ubuntu machine already proven by Stage C, keep the **same** private-LAN TLS-PSK listener, bearer/PSK credentials, pinned Kai StickS3 firmware, and other Amusant settings. Stop the current Amusant Relay cleanly before restarting it with these two additional environment variables:

```bash
export AMUSANT_PERSONA_PROFILE=jellii
export AMUSANT_GEMINI_VOICE=Zephyr

# Use the same existing Stage C launch command and private LAN host.
# Example, from repository root (RELAY_LAN_IP is your existing private host):
uv run --locked --python 3.13 --extra hardware-opus python -m relay.live_bridge --device-mode sticks3 --host "$RELAY_LAN_IP"
```

The example assumes the Stage C device credentials, `GEMINI_API_KEY`, task-store settings, and `RELAY_LAN_IP` are **already securely available** in the Relay process environment. Do not paste or commit their values. Do not start a second listener on port 8443.

- `AMUSANT_PERSONA_PROFILE=jellii` adds the generic Jellii system instruction to both the normal tool-enabled Gemini session and the tool-free spoken Inbox session.
- `AMUSANT_GEMINI_VOICE=Zephyr` selects the Gemini Live prebuilt voice for either session. This is independent of the persona profile and does **not** read Kai's `KAI_VOICE`.
- Either setting may be omitted. With **both unset**, the previous Stage C LiveConnectConfig remains unchanged.
- An unknown persona profile, empty/invalid voice name, or invalid injected system instruction fails closed. Actual Gemini model/voice support must still be confirmed with a separately approved live test.

To roll back, stop Amusant, `unset AMUSANT_PERSONA_PROFILE AMUSANT_GEMINI_VOICE`, and restart the exact Stage C command. No firmware reflash or secret rotation is required.

## Offline regression (no API calls)

From the repository root:

```bash
uv run --locked --python 3.13 --extra hardware-opus --group dev python -m unittest discover -s fast_voice/tests -v
PYTHONPATH=.:relay uv run --locked --python 3.13 --extra hardware-opus --group dev python -m unittest discover -s relay/tests -v
PYTHONPATH=. uv run --locked --python 3.13 --extra hardware-opus --group dev python relay/tests/psk_wss_integration.py
```

Tests cover default-off configuration, invalid profile/voice, actual SDK config construction without network, preservation of tool declarations and manual PTT activity detection, and persona parity between normal and tool-free spoken sessions.

## Separately authorized physical Stage D check

After offline CI passes and the owner approves Gemini usage, perform 3–5 short battery-powered PTT turns. Confirm the selected voice sounds like Zephyr, natural Taiwan Traditional Chinese wording, Jellii's short conversational style, no invented long-term memory, and a working barge-in. Record any audible changes or errors. Do **not** claim Stage D live PASS from offline tests.

This PR does **not** implement OpenAB persona synchronization, Companion Memory, proactive speech, scheduling, Google Search, or new tools. The real slow-task/Inbox path requires its own E2E validation.
