#include "wifi_console.h"

#include <assert.h>
#include <stdint.h>

static wifi_console::Event parseLine(wifi_console::Parser& parser, const char* line) {
  wifi_console::Event event = {};
  uint32_t time = 0;
  bool received = false;
  for (const char* cursor = line; *cursor; ++cursor) {
    if (parser.feed(static_cast<uint8_t>(*cursor), time++, event)) received = true;
  }
  assert(received);
  return event;
}

int main() {
  wifi_console::Parser parser;
  const wifi_console::Event event = parseLine(parser, "CJK SHOW\n");
#ifdef JELLII_CJK_DIAGNOSTICS
  assert(event.type == wifi_console::EventType::CjkDiagnostic);
#else
  assert(event.type == wifi_console::EventType::Error);
#endif
}
