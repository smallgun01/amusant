#!/usr/bin/env python3
"""Check the real M5GFX 0.2.30 efontTW_14 table and emit host-test metrics.

The lookup, metric bit reader, and bitmap run decoder mirror the pinned
U8g2font implementation in lgfx_fonts.inl. The script reads the font array
that PlatformIO just built against; it does not download or copy font data.
"""
import argparse
import json
from pathlib import Path
import re

VERSION = "0.2.30"
CORPUS_LINES = (
    "你們好，我想要一杯咖啡。",
    "請問現在幾點？謝謝，這個價格可以嗎？",
    "天氣很好，明天見。",
    "會議、時間、工作、臺灣，裡面？",
    "繁體中文，標點：「」、『』（）—…；？、！",
    "會您鬱體龍發裡臺與這為說請謝讓好你我中文",
)
EMOJI = "🙂🧪"
CONTROLS = "\t\r\n"

# A diagnostic matrix for mixed-script captions and renderer edge cases.
# The glyph labels are informational; lookup is always keyed by the numeric scalar.
DIAGNOSTIC_MATRIX = (
    (0x6703, "traditional-hui"),
    (0x4F1A, "simplified-hui"),
    (0x5F8C, "traditional-hou"),
    (0x540E, "simplified-hou"),
    (0x88E1, "traditional-li"),
    (0x91CC, "simplified-li"),
    (0x767C, "traditional-fa"),
    (0x53D1, "simplified-fa"),
    (0x60A8, "nin"),
    (0x653E, "fang"),
    (0x5FC3, "xin"),
    (0xFF0C, "comma"),
    (0x3002, "ideographic-full-stop"),
    (0xFF1F, "question-mark"),
    (0x3001, "enumeration-comma"),
    (0xFF01, "exclamation-mark"),
    (0xFFFD, "replacement"),
    (0xFE0F, "variation selector"),
    (0x200D, "zero-width joiner"),
)
EXPECTED_DIAGNOSTIC_COVERAGE = {
    0x6703: True,
    0x4F1A: False,
    0x5F8C: True,
    0x540E: True,
    0x88E1: True,
    0x91CC: True,
    0x767C: True,
    0x53D1: False,
    0x60A8: True,
    0x653E: True,
    0x5FC3: True,
    0xFF0C: True,
    0x3002: True,
    0xFF1F: True,
    0x3001: True,
    0xFF01: True,
    0xFFFD: False,
    0xFE0F: False,
    0x200D: False,
}


def c_string_bytes(token):
    """Decode one C string literal to bytes (the font uses octal escapes)."""
    result = bytearray()
    i = 1
    end = len(token) - 1
    simple = {
        "a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
        "\\": 92, '"': 34, "'": 39, "?": 63,
    }
    while i < end:
        ch = token[i]
        if ch != "\\":
            result.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= end:
            raise ValueError("trailing escape in C string literal")
        if token[i] in "01234567":
            j = i
            while j < min(i + 3, end) and token[j] in "01234567":
                j += 1
            result.append(int(token[i:j], 8))
            i = j
        elif token[i] == "x":
            j = i + 1
            while j < end and token[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 1:
                raise ValueError("empty hexadecimal escape in C string literal")
            result.append(int(token[i + 1:j], 16) & 0xFF)
            i = j
        else:
            result.append(simple[token[i]])
            i += 1
    return result


class BitReader:
    def __init__(self, data, offset):
        self.data = data
        self.offset = offset
        self.bit_pos = 0

    def unsigned(self, count):
        if count == 0:
            return 0
        bit_pos = self.bit_pos
        value = self.data[self.offset] >> bit_pos
        bit_pos_plus_count = bit_pos + count
        if bit_pos_plus_count >= 8:
            bit_pos_plus_count -= 8
            value |= self.data[self.offset + 1] << (8 - bit_pos)
            self.offset += 1
        self.bit_pos = bit_pos_plus_count
        return value & ((1 << count) - 1)

    def signed(self, count):
        if count == 0:
            raise ValueError("U8g2 signed metric has zero bits")
        return self.unsigned(count) - (1 << (count - 1))


def read_u16(data, offset):
    return (data[offset] << 8) | data[offset + 1]


def glyph_offset(font, codepoint):
    # U8g2font::getGlyph() from M5GFX 0.2.30.
    ptr = 23
    if codepoint <= 255:
        if codepoint >= ord("a"):
            ptr += read_u16(font, 19)
        elif codepoint >= ord("A"):
            ptr += read_u16(font, 17)
        while ptr + 1 < len(font) and font[ptr + 1] != 0:
            if font[ptr] == codepoint:
                return ptr + 2
            ptr += font[ptr + 1]
        return None

    ptr += read_u16(font, 21)
    lut = ptr
    for _ in range(65536):
        if lut + 3 >= len(font):
            return None
        ptr += read_u16(font, lut)
        group_end = read_u16(font, lut + 2)
        lut += 4
        if group_end >= codepoint:
            break
    else:
        return None

    for _ in range(65536):
        if ptr + 2 >= len(font):
            return None
        encoded = read_u16(font, ptr)
        if encoded == 0:
            return None
        if encoded == codepoint:
            return ptr + 3
        step = font[ptr + 2]
        if step == 0:
            return None
        ptr += step
    return None


def metrics_and_reader(font, glyph_ptr):
    reader = BitReader(font, glyph_ptr)
    width = reader.unsigned(font[4])
    height = reader.unsigned(font[5])
    x_offset = reader.signed(font[6])
    y_offset = reader.signed(font[7])
    x_advance = reader.signed(font[8])
    return {
        "width": width,
        "height": height,
        "x_offset": x_offset,
        "y_offset": y_offset,
        "x_advance": x_advance,
    }, reader


def bitmap_pixel_count(font, metrics, reader):
    # Mirrors U8g2font::drawChar()'s alternating foreground/background runs.
    width = metrics["width"]
    height = metrics["height"]
    if width == 0 or height == 0:
        return 0
    x = 0
    y = 0
    foreground_pixels = 0
    guard = 0
    while y < height:
        runs = (reader.unsigned(font[2]), reader.unsigned(font[3]))
        colour = 0
        while True:
            remaining = runs[colour]
            while remaining:
                count = min(remaining, width - x)
                if colour == 1:
                    foreground_pixels += count
                x += count
                remaining -= count
                if x == width:
                    x = 0
                    y += 1
                    if y >= height:
                        break
            if y >= height:
                break
            colour = 1 - colour
            if colour == 0 and reader.unsigned(1) == 0:
                break
            guard += 1
            if guard > width * height * 4:
                raise ValueError("invalid U8g2 bitmap run stream")
    return foreground_pixels


def verify_pinned_sources(root):
    metadata = json.loads((root / "library.json").read_text(encoding="utf-8"))
    if metadata.get("version") != VERSION:
        raise SystemExit(
            "expected M5GFX " + VERSION + ", found " + str(metadata.get("version"))
        )

    fonts_inl = (root / "src/lgfx/v1/lgfx_fonts.inl").read_text(encoding="utf-8")
    base_inl = (root / "src/lgfx/v1/LGFXBase.inl").read_text(encoding="utf-8")
    fonts_hpp = (root / "src/lgfx/v1/lgfx_fonts.hpp").read_text(encoding="utf-8")
    base_hpp = (root / "src/lgfx/v1/LGFXBase.hpp").read_text(encoding="utf-8")
    required = (
        (fonts_inl, "const uint8_t *font = &this->_font[23];"),
        (fonts_inl, "font += this->start_pos_unicode();"),
        (fonts_inl, "bool U8g2font::updateFontMetric"),
        (fonts_inl, "decode.get_unsigned_bits(this->bits_per_char_width())"),
        (fonts_inl, "decode.get_signed_bits  (this->bits_per_delta_x())"),
        (fonts_inl, "decode.get_unsigned_bits(bits_per_0())"),
        (fonts_inl, "decode.get_unsigned_bits(bits_per_1())"),
        (base_inl, "int32_t LGFXBase::text_width"),
        (base_inl, "font->updateFontMetric(metrics, uniCode)"),
        (base_inl, "std::max<int>(sxadvance, ((metrics->width * sx) >> 16) + sxoffset)"),
        (base_inl, "if (uniCode > 0xFFFF || (!font->updateFontMetric"),
        (base_inl, "uniCode = 0;"),
        (fonts_hpp, "bool utf8 = true;"),
        (fonts_hpp, "const uint8_t* getGlyph(uint16_t encoding) const;"),
        (base_hpp, "emoji_draw_cb_t _emoji_draw_cb = nullptr;"),
        (base_inl, "while (uniCode < 0x20 && *(++string));"),
        (base_inl, "if (utf8 == '\\r') return 1;"),
        (base_inl, "if (utf8 == '\\n') {"),
    )
    for source, marker in required:
        if marker not in source:
            raise SystemExit(
                "pinned M5GFX source no longer matches lookup/width assumptions: " + marker
            )


def extract_font(root):
    source = (root / "src/lgfx/Fonts/efont/lgfx_efont_tw.c").read_text(encoding="ascii")
    declaration = re.search(
        r"PROGMEM const uint8_t\s+lgfx_efont_tw_14\[(\d+)\]\s*=\s*",
        source,
    )
    if not declaration:
        raise SystemExit("could not find the pinned lgfx_efont_tw_14 array")
    array_size = int(declaration.group(1))
    literals = []
    cursor = declaration.end()
    while True:
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor < len(source) and source[cursor] == ";":
            break
        literal = re.compile(r'"(?:\\.|[^"\\])*"').match(source, cursor)
        if not literal:
            raise SystemExit("unexpected token in the efontTW_14 C string array")
        literals.append(literal.group(0))
        cursor = literal.end()
    if not literals:
        raise SystemExit("the pinned efontTW_14 array has no C string literals")
    payload = bytearray()
    for literal in literals:
        payload.extend(c_string_bytes(literal))
    # The C array is initialized from adjacent strings and has the C terminator.
    if len(payload) + 1 != array_size:
        raise SystemExit(
            "efontTW_14 payload length mismatch: " + str(len(payload)) +
            " bytes plus terminator, expected " + str(array_size)
        )
    payload.append(0)
    return payload

def print_diagnostic_matrix(font):
    print("Pinned M5GFX %s caption diagnostic matrix:" % VERSION)
    for codepoint, label in DIAGNOSTIC_MATRIX:
        offset = glyph_offset(font, codepoint)
        present = offset is not None
        expected = EXPECTED_DIAGNOSTIC_COVERAGE[codepoint]
        if present != expected:
            raise SystemExit(
                "unexpected efontTW_14 matrix for U+%04X: expected %s, found %s" %
                (codepoint, "PRESENT" if expected else "MISSING",
                 "PRESENT" if present else "MISSING")
            )
        if not present:
            print("U+%04X %s: MISSING" % (codepoint, label))
            continue
        metrics, _ = metrics_and_reader(font, offset)
        print("U+%04X %s: PRESENT width=%d advance=%d" %
              (codepoint, label, metrics["width"], metrics["x_advance"]))
    for character in EMOJI:
        codepoint = ord(character)
        print("U+%06X emoji: supplementary scalar; outside U8g2 16-bit glyph lookup, "
              "M5GFX emoji callback/fallback path" % codepoint)


def write_metrics_header(path, metrics_by_cp):
    lines = [
        "#pragma once",
        "#include <cstddef>",
        "#include <cstdint>",
        "struct CjkGlyphMetric { std::uint32_t codepoint; std::int16_t width; "
        "std::int16_t x_offset; std::int16_t x_advance; };",
        "inline constexpr CjkGlyphMetric kCjkGlyphMetrics[] = {",
    ]
    for cp in sorted(metrics_by_cp):
        m = metrics_by_cp[cp]
        lines.append(
            "  {0x%04X, %d, %d, %d}," %
            (cp, m["width"], m["x_offset"], m["x_advance"])
        )
    lines.extend((
        "};",
        "inline constexpr std::size_t kCjkGlyphMetricCount = "
        "sizeof(kCjkGlyphMetrics) / sizeof(kCjkGlyphMetrics[0]);",
        "",
    ))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m5gfx", type=Path, required=True)
    parser.add_argument("--emit-header", type=Path, required=True)
    args = parser.parse_args()

    root = args.m5gfx.resolve()
    verify_pinned_sources(root)
    font = extract_font(root)
    corpus = "\n".join(CORPUS_LINES)
    codepoints = sorted({ord(ch) for ch in corpus if ord(ch) >= 0x20})
    if any(cp > 0xFFFF for cp in codepoints):
        raise SystemExit("the Traditional Chinese coverage corpus must stay in the BMP")

    metrics_by_cp = {}
    missing = []
    for cp in codepoints:
        offset = glyph_offset(font, cp)
        if offset is None:
            missing.append(cp)
            continue
        metrics, _ = metrics_and_reader(font, offset)
        if metrics["width"] <= 0 or metrics["height"] <= 0 or metrics["x_advance"] <= 0:
            raise SystemExit("invalid glyph metrics for U+%04X: %r" % (cp, metrics))
        metrics_by_cp[cp] = metrics
    if missing:
        raise SystemExit(
            "efontTW_14 is missing representative corpus glyphs: " +
            ", ".join("U+%04X" % cp for cp in missing)
        )

    print_diagnostic_matrix(font)

    hui = metrics_by_cp[0x6703]
    hui_offset = glyph_offset(font, 0x6703)
    hui_metrics, hui_reader = metrics_and_reader(font, hui_offset)
    pixels = bitmap_pixel_count(font, hui_metrics, hui_reader)
    if pixels == 0:
        raise SystemExit("U+6703 exists in the table but has an empty bitmap")

    if not all(ord(ch) > 0xFFFF for ch in EMOJI):
        raise SystemExit("emoji fixture should use supplementary-plane code points")
    if any(ord(ch) >= 0x20 for ch in CONTROLS):
        raise SystemExit("control fixture must remain below U+0020")

    write_metrics_header(args.emit_header.resolve(), metrics_by_cp)
    print("M5GFX %s efontTW_14 exact glyph lookup: %d/%d corpus code points present" %
          (VERSION, len(metrics_by_cp), len(codepoints)))
    print("U+6703 會: width=%d height=%d xOffset=%d yOffset=%d advance=%d bitmapPixels=%d" %
          (hui["width"], hui["height"], hui["x_offset"], hui["y_offset"],
           hui["x_advance"], pixels))
    print("U+60A8 您 and U+9B31 鬱: present")
    print("Supplementary emoji U+1F642/U+1F9EA: outside the 16-bit U8g2 glyph lookup; "
          "separate M5GFX emoji callback/fallback path")
    print("U+0009/U+000D/U+000A controls: control handling, not font glyph coverage")
    print("Generated metrics for production jellii_text::wrap width test: %s" %
          args.emit_header.resolve())


if __name__ == "__main__":
    main()
