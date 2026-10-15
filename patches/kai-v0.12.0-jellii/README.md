# Jellii avatar downstream patch for Kai v0.12.0

This package applies the reviewed Jellii UI to the Kai v0.12.0 firmware baseline without copying or modifying the Kai upstream repository. It is an ownership-neutral downstream patch while Amusant firmware ownership is tracked in [Issue #11](https://github.com/smallgun01/amusant/issues/11).

## Pinned baseline

- Upstream: <https://github.com/zbruceli/kai>
- Commit: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`
- Local firmware branch: `downstream/jellii-avatar-v0.12.0`
- Patch modifies only `firmware/src/face.cpp` and the visible status label in `firmware/src/main.cpp`.

## Apply

From a clean checkout of the pinned Kai commit, with this patch file available locally:

```powershell
git clone https://github.com/zbruceli/kai.git
cd kai
git checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git switch -c downstream/jellii-avatar-v0.12.0
git apply --check C:/path/to/kai-v0.12.0-jellii.patch
git apply C:/path/to/kai-v0.12.0-jellii.patch
```

Configure private firmware values locally using Kai's `firmware/src/secrets.example.h` as a template. Keep `secrets.h` local and do not add it to a commit. Build from `kai/firmware` with:

```powershell
pio run -e sticks3
```

## Roll back

From the Kai checkout root, reverse the same patch:

```powershell
git apply -R C:/path/to/kai-v0.12.0-jellii.patch
```

Alternatively, restore only the two changed paths to the pinned commit:

```powershell
git restore --source=1681fb3c11acd28525d8987bba4b9bf4bc2a288f -- firmware/src/face.cpp firmware/src/main.cpp
```

## Implementation summary

- Replaces Kai's 20×18 mirrored mask with a fixed 20×18 Jellii sprite: four tentacles, cyan flower mark, pale blue/lavender shell, highlights, cheeks, six expressions, and red offline body with X eyes.
- Keeps the 240×135 RGB565 canvas, main view at 5×, reply header at 2×, six existing expression states, blink/floating, listening waves, thinking bubbles, sleeping Z marks, and current `setSpeaking()` / `setLevel()` volume threshold.
- Speaking mouth uses a 2×2 logical-cell open glyph; sleeping eyes are flat.
- Changes the default reply title and status card title from Kai to Jellii.
- Does not touch PTT, Opus, TLS-PSK, Wi-Fi, relay protocol, Gemini Live, power management, or any device credentials.

## Build and footprint

**Build not completed in the current environment.** PlatformIO Core 6.2.0 entered `sticks3` dependency resolution but stalled while fetching `m5stack/M5GFX@0.2.30` at `Downloading 0%`. A direct fetch of the exact upstream tag also stalled. The compiler was not reached, so this package makes no claim of a passing firmware build or binary-size measurement. Rerun the command above in a networked PlatformIO environment before flashing.

Static source impact: `Theme` gains nine `uint16_t` values (+18 bytes of global state); the existing 20×18 character buffer stays the same size (378 bytes), the new geometry is fixed `const` data, and no heap allocation was added. `buildSprite()` runs once during face initialization. Rendering keeps the existing 360-cell scan at 5×/2×; exact Flash, final RAM, and cycle deltas remain unmeasured until a successful build.

## Preview and verification

All enlarged images use nearest-neighbor scaling, with no anti-aliasing or post effects. The native LCD contact sheet retains Kai's icon bar, battery, inbox, and status text in the actual 240×135 main layout. Thinking bubbles, sleeping Z marks, and listening waves are drawn at the original effect positions; the preview audit reports no non-text drawing outside the LCD bounds.

- [Six states, exact 20×18 cells](previews/sprite-6-states-20x18-1x.png)
- [Six states, 8× nearest-neighbor](previews/sprite-6-states-20x18-8x.png)
- [Native 240×135 LCD contact sheet](previews/lcd-contact-1x.png)
- [4× nearest-neighbor LCD review sheet](previews/lcd-contact-4x.png)
- [Reply Header closed mouth, exact 40×36](previews/reply-header-40x36-closed.png)
- [Reply Header open mouth, exact 40×36](previews/reply-header-40x36-open.png)
- [Reply Header open / closed side by side](previews/reply-header-open-closed-80x36.png)
- [Reply Header 8× nearest-neighbor comparisons](previews/reply-header-open-closed-8x.png)
- [Reply header, 8× nearest-neighbor](previews/reply-header-open-closed-8x.png)
- [Thinking, listening, sleeping, and speaking at native 240×135](previews/animation-1x.gif)
- [Offline frame-by-frame viewer](previews/Preview.html)
- [Preview validation record](previews/Validation.json)

The previews are source-layout host rasters based on the pinned Kai drawing coordinates and existing font/icon data, not an M5Canvas hardware capture. Physical flashing is outside this patch review and requires separate device-owner confirmation.

## License

The patch is an adaptation of Kai firmware distributed under the MIT License; see [LICENSE-Kai.txt](LICENSE-Kai.txt).
