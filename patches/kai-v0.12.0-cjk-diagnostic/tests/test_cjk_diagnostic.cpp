#include "jellii_cjk_diagnostic.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <vector>

struct ReportedScalar {
  jellii_cjk_diagnostic::Classification classification;
  uint32_t codepoint;
};

static bool hasTraditionalHuiAndAscii(uint32_t codepoint) {
  return codepoint == 0x6703 || (codepoint >= 0x20 && codepoint <= 0x7E);
}

static void collect(std::vector<ReportedScalar>& reported,
                    jellii_cjk_diagnostic::Classification classification,
                    uint32_t codepoint) {
  reported.push_back({classification, codepoint});
}

static void testCategoriesAndDeduplication() {
  using jellii_cjk_diagnostic::Classification;
  const char caption[] =
      "\xE6\x9C\x83"  // U+6703 會 (supported)
      "\xE4\xBC\x9A"  // U+4F1A 会 (missing)
      "\xE4\xBC\x9A"  // duplicate missing scalar
      "\xF0\x9F\x99\x82"  // U+1F642 🙂
      "\xF0\x9F\x99\x82"  // duplicate supplementary scalar
      "\xEF\xBF\xBD"  // U+FFFD
      "\xEF\xB8\x8F"  // U+FE0F variation selector
      "\xE2\x80\x8D";  // U+200D zero-width joiner

  uint32_t seen[jellii_cjk_diagnostic::kMaxReportedCaptionScalars] = {};
  size_t seenCount = 0;
  std::vector<ReportedScalar> reported;
  const size_t emitted = jellii_cjk_diagnostic::reportUnsupported(
      caption, sizeof(caption) - 1, hasTraditionalHuiAndAscii,
      [&reported](Classification classification, uint32_t codepoint) {
        collect(reported, classification, codepoint);
      },
      seen, seenCount);

  assert(emitted == 5);
  assert(seenCount == 5);
  assert(reported.size() == 5);
  assert(reported[0].classification == Classification::Missing);
  assert(reported[0].codepoint == 0x4F1A);
  assert(reported[1].classification == Classification::NonBmp);
  assert(reported[1].codepoint == 0x1F642);
  assert(reported[2].classification == Classification::Invalid);
  assert(reported[2].codepoint == 0xFFFD);
  assert(reported[3].classification == Classification::Format);
  assert(reported[3].codepoint == 0xFE0F);
  assert(reported[4].classification == Classification::Format);
  assert(reported[4].codepoint == 0x200D);

  const size_t repeated = jellii_cjk_diagnostic::reportUnsupported(
      caption, sizeof(caption) - 1, hasTraditionalHuiAndAscii,
      [&reported](Classification classification, uint32_t codepoint) {
        collect(reported, classification, codepoint);
      },
      seen, seenCount);
  assert(repeated == 0);
  assert(reported.size() == 5);
}

static void testMalformedUtf8ReportsInvalidOnlyOnce() {
  using jellii_cjk_diagnostic::Classification;
  const char malformed[] = "\xF0\x28\x8C\x28";
  uint32_t seen[jellii_cjk_diagnostic::kMaxReportedCaptionScalars] = {};
  size_t seenCount = 0;
  std::vector<ReportedScalar> reported;
  const size_t emitted = jellii_cjk_diagnostic::reportUnsupported(
      malformed, sizeof(malformed) - 1,
      [](uint32_t codepoint) { return codepoint == '('; },
      [&reported](Classification classification, uint32_t codepoint) {
        collect(reported, classification, codepoint);
      },
      seen, seenCount);
  assert(emitted == 1);
  assert(reported.size() == 1);
  assert(reported[0].classification == Classification::Invalid);
  assert(reported[0].codepoint == 0xFFFD);
}

static void testPerBootReportCap() {
  char caption[jellii_cjk_diagnostic::kMaxReportedCaptionScalars + 2];
  for (size_t i = 0; i < sizeof(caption); ++i) {
    caption[i] = static_cast<char>(0x20 + i);
  }

  uint32_t seen[jellii_cjk_diagnostic::kMaxReportedCaptionScalars] = {};
  size_t seenCount = 0;
  std::vector<uint32_t> reported;
  const size_t emitted = jellii_cjk_diagnostic::reportUnsupported(
      caption, sizeof(caption), [](uint32_t) { return false; },
      [&reported](jellii_cjk_diagnostic::Classification, uint32_t codepoint) {
        reported.push_back(codepoint);
      },
      seen, seenCount);
  assert(emitted == jellii_cjk_diagnostic::kMaxReportedCaptionScalars);
  assert(seenCount == jellii_cjk_diagnostic::kMaxReportedCaptionScalars);
  assert(reported.size() == jellii_cjk_diagnostic::kMaxReportedCaptionScalars);

  const char extra = static_cast<char>(0x62);
  const size_t afterCap = jellii_cjk_diagnostic::reportUnsupported(
      &extra, 1, [](uint32_t) { return false; },
      [&reported](jellii_cjk_diagnostic::Classification, uint32_t codepoint) {
        reported.push_back(codepoint);
      },
      seen, seenCount);
  assert(afterCap == 0);
  assert(reported.size() == jellii_cjk_diagnostic::kMaxReportedCaptionScalars);
}

static void testSupplementaryScalarDecode() {
  const char text[] = "\xF0\x9F\xA7\xAA";
  uint32_t codepoint = 0;
  size_t next = 0;
  assert(jellii_cjk_diagnostic::decodeNext(text, sizeof(text) - 1, 0, codepoint, next));
  assert(codepoint == 0x1F9EA && next == sizeof(text) - 1);
}

static void testDisplayCommandBusyGate() {
  using jellii_cjk_diagnostic::displayCommandBusy;
  assert(!displayCommandBusy(false, false, false, true, true, true, false, false));
  assert(displayCommandBusy(false, false, false, true, true, false, false, false));
  assert(displayCommandBusy(false, false, false, true, true, true, true, false));
  assert(displayCommandBusy(false, false, false, true, false, true, false, false));
  assert(displayCommandBusy(true, false, false, false, false, true, false, false));
  assert(displayCommandBusy(false, true, false, false, false, true, false, false));
  assert(displayCommandBusy(false, false, true, false, false, true, false, false));
  assert(displayCommandBusy(false, false, false, false, false, true, false, true));
}

static void testCategoryLabels() {
  using jellii_cjk_diagnostic::Classification;
  using jellii_cjk_diagnostic::classificationName;
  assert(strcmp(classificationName(Classification::Missing), "MISSING") == 0);
  assert(strcmp(classificationName(Classification::Format), "FORMAT") == 0);
  assert(strcmp(classificationName(Classification::NonBmp), "NONBMP") == 0);
  assert(strcmp(classificationName(Classification::Invalid), "INVALID") == 0);
}

int main() {
  assert(jellii_cjk_diagnostic::kMaxReportedCaptionScalars == 64);
  testCategoriesAndDeduplication();
  testMalformedUtf8ReportsInvalidOnlyOnce();
  testPerBootReportCap();
  testSupplementaryScalarDecode();
  testDisplayCommandBusyGate();
  testCategoryLabels();
}
