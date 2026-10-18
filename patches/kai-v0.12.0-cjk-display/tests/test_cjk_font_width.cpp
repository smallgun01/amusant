#include "jellii_text_utf8.h"
#include "jellii_cjk_font_metrics.generated.hpp"

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

struct WidthMetric {
  int width;
  int x_offset;
  int x_advance;
};

static WidthMetric metric_for(std::uint32_t codepoint) {
  const auto* begin = kCjkGlyphMetrics;
  const auto* end = kCjkGlyphMetrics + kCjkGlyphMetricCount;
  const auto* found = std::lower_bound(
      begin, end, codepoint,
      [](const CjkGlyphMetric& metric, std::uint32_t cp) {
        return metric.codepoint < cp;
      });
  assert(found != end && found->codepoint == codepoint);
  return {found->width, found->x_offset, found->x_advance};
}

static std::uint32_t decode_codepoint(const char* text, std::size_t start,
                                      std::size_t end) {
  const auto first = static_cast<std::uint8_t>(text[start]);
  const std::size_t count = end - start;
  if (count == 1) return first;
  std::uint32_t cp = count == 2 ? first & 0x1f :
                     count == 3 ? first & 0x0f : first & 0x07;
  for (std::size_t i = 1; i < count; ++i) {
    cp = (cp << 6) |
         (static_cast<std::uint8_t>(text[start + i]) & 0x3f);
  }
  return cp;
}

// Mirrors M5GFX 0.2.30 LGFXBase::text_width at size_x == 1 using metrics
// obtained from the real efontTW_14 U8g2 bitstream.
static int m5gfx_text_width(const char* text, std::size_t length) {
  int left = 0;
  int right = 0;
  for (std::size_t pos = 0; pos < length;) {
    const std::size_t next = jellii_text::next(text, length, pos);
    assert(next > pos && next <= length);
    const auto metric = metric_for(decode_codepoint(text, pos, next));
    if (left == 0 && right == 0 && metric.x_offset < 0) {
      left = right = -metric.x_offset;
    }
    right = left + std::max(metric.x_advance,
                            metric.width + metric.x_offset);
    left += metric.x_advance;
    pos = next;
  }
  return right;
}

static bool valid_utf8(const std::string& text) {
  for (std::size_t pos = 0; pos < text.size();) {
    const std::size_t next = jellii_text::next(text.data(), text.size(), pos);
    if (next <= pos || next > text.size()) return false;
    pos = next;
  }
  return true;
}

int main() {
  const std::string corpus =
      "你們好，我想要一杯咖啡。\n"
      "請問現在幾點？謝謝，這個價格可以嗎？\n"
      "天氣很好，明天見。\n"
      "會議、時間、工作、臺灣，裡面？\n"
      "繁體中文，標點：「」、『』（）—…；？、！\n"
      "會您鬱體龍發裡臺與這為說請謝讓好你我中文";

  constexpr int max_width = 70;
  std::vector<std::string> lines;
  const auto measure = [&](std::size_t start, std::size_t end) {
    return m5gfx_text_width(corpus.data() + start, end - start);
  };
  const auto emit = [&](std::size_t start, std::size_t end) {
    lines.emplace_back(corpus.data() + start, end - start);
  };
  jellii_text::wrap(corpus.data(), corpus.size(), max_width, measure, emit);

  assert(!lines.empty());
  bool saw_hui = false;
  for (const auto& line : lines) {
    assert(valid_utf8(line));
    const int measured = m5gfx_text_width(line.data(), line.size());
    assert(measured <= max_width);
    if (line.find("會") != std::string::npos) saw_hui = true;
  }
  assert(saw_hui);
  assert(m5gfx_text_width("會", std::string("會").size()) == 14);
  std::cout << "Jellii production wrapper with real efontTW_14 advances PASS: "
            << lines.size() << " lines <= " << max_width << " px; "
            << kCjkGlyphMetricCount << " actual glyph metrics\n";
}
