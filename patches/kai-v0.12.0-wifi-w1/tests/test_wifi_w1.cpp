#include <assert.h>
#include <stdint.h>
#include <string.h>

#include <string>

#include <Preferences.h>
#include "power_policy.h"
#include "wifi_config.h"
#include "wifi_console.h"

static wifi_config::Profile makeProfile() {
  wifi_config::Profile profile = {};
  const char ssid[] = "lab-2g";
  memcpy(profile.ssid, ssid, sizeof(ssid) - 1);
  profile.ssidLength = sizeof(ssid) - 1;
  memset(profile.password, 'x', 8);
  profile.password[8] = '\0';
  profile.passwordLength = 8;
  return profile;
}

static wifi_console::Event feedLine(wifi_console::Parser& parser, const char* line, uint32_t& time) {
  wifi_console::Event event = {};
  bool received = false;
  for (const char* cursor = line; *cursor; ++cursor) {
    if (parser.feed(static_cast<uint8_t>(*cursor), time++, event)) received = true;
  }
  assert(received);
  return event;
}

static void testNvsProfile() {
  Preferences::reset();
  wifi_config::Profile profile = makeProfile();
  wifi_config::Profile loaded = {};
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Missing);
  assert(wifi_config::save(profile));
  assert(Preferences::writes() == 1);
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Valid);
  assert(strcmp(loaded.ssid, profile.ssid) == 0);
  assert(loaded.ssidLength == profile.ssidLength);
  assert(loaded.passwordLength == profile.passwordLength);
  assert(memcmp(loaded.password, profile.password, profile.passwordLength) == 0);
  assert(Preferences::writes() == 1);  // Boot reads do not rewrite NVS.

  Preferences::corrupt("amu_wifi", "profile", 20);
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Invalid);
  assert(loaded.ssid[0] == '\0' && loaded.password[0] == '\0');
  assert(wifi_config::clear());
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Missing);
}

static void testProfileValidation() {
  wifi_config::Profile profile = makeProfile();
  assert(wifi_config::validProfile(profile));
  profile.ssidLength = 0;
  assert(!wifi_config::validProfile(profile));
  profile = makeProfile();
  profile.passwordLength = 7;
  profile.password[7] = '\0';
  assert(!wifi_config::validProfile(profile));
  profile = makeProfile();
  profile.ssid[0] = '\n';
  assert(!wifi_config::validProfile(profile));
  profile = makeProfile();
  profile.password[0] = '\n';
  assert(!wifi_config::validProfile(profile));
}

static void testConsoleProtocol() {
  wifi_console::Parser parser;
  wifi_console::Event event = {};
  uint32_t time = 100;
  event = feedLine(parser, "WIFI STATUS\n", time);
  assert(event.type == wifi_console::EventType::Status);

  event = feedLine(parser, "WIFI SET 6 8\n", time);
  assert(event.type == wifi_console::EventType::SetReady);
  const char payload[] = "lab-2gxxxxxxxx";
  bool complete = false;
  for (size_t i = 0; i < sizeof(payload) - 1; ++i) {
    if (parser.feed(static_cast<uint8_t>(payload[i]), time++, event)) complete = true;
  }
  assert(complete && event.type == wifi_console::EventType::Set);
  assert(strcmp(event.profile.ssid, "lab-2g") == 0);
  assert(event.profile.passwordLength == 8);
  assert(wifi_config::validProfile(event.profile));

  event = feedLine(parser, "WIFI SET 6 8\n", time);
  assert(event.type == wifi_console::EventType::SetReady);
  const char invalidPayload[] = {'l', 'a', 'b', '-', '2', 'g', '\0', 'x', 'x', 'x', 'x', 'x', 'x', 'x'};
  complete = false;
  for (size_t i = 0; i < sizeof(invalidPayload); ++i) {
    if (parser.feed(static_cast<uint8_t>(invalidPayload[i]), time++, event)) complete = true;
  }
  assert(complete && event.type == wifi_console::EventType::Error);

  std::string oversized(48, 'A');
  oversized += '\n';
  event = feedLine(parser, oversized.c_str(), time);
  assert(event.type == wifi_console::EventType::Error);

  parser.feed('W', time++, event);
  parser.feed('I', time++, event);
  assert(parser.poll(time + 15000, event));
  assert(event.type == wifi_console::EventType::Timeout);

  time += 15000;
  event = feedLine(parser, "WIFI STATUS\n", time);
  assert(event.type == wifi_console::EventType::Status);

  parser.feed('W', time++, event);
  parser.feed('I', time++, event);
  const uint32_t lateByteAt = time + 15000;
  assert(parser.feed('W', lateByteAt, event));
  assert(event.type == wifi_console::EventType::Timeout);
  time = lateByteAt + 1;
  event = feedLine(parser, "IFI STATUS\n", time);
  assert(event.type == wifi_console::EventType::Status);

  event = feedLine(parser, "WIFI SET 6 8\n", time);
  assert(event.type == wifi_console::EventType::SetReady);
  const char partialPayload[] = "lab-";
  for (size_t i = 0; i < sizeof(partialPayload) - 1; ++i)
    parser.feed(static_cast<uint8_t>(partialPayload[i]), time++, event);
  assert(parser.poll(time + 15000, event));
  assert(event.type == wifi_console::EventType::Timeout);
  time += 15000;
  event = feedLine(parser, "WIFI CLEAR\n", time);
  assert(event.type == wifi_console::EventType::Clear);
}

static void testStatusAndOfflineRecovery() {
  assert(strcmp(wifi_console::statusResponse(false, false, false), "WIFI OK UNCONFIGURED OFFLINE") == 0);
  assert(strcmp(wifi_console::statusResponse(false, true, false), "WIFI OK INVALID OFFLINE") == 0);
  assert(strcmp(wifi_console::statusResponse(true, false, false), "WIFI OK CONFIGURED OFFLINE") == 0);
  assert(strcmp(wifi_console::statusResponse(true, false, true), "WIFI OK CONFIGURED CONNECTED") == 0);

  Preferences::reset();
  wifi_config::Profile unavailableNetwork = makeProfile();  // host test cannot associate with an AP
  assert(wifi_config::save(unavailableNetwork));
  assert(strcmp(wifi_console::statusResponse(true, false, false), "WIFI OK CONFIGURED OFFLINE") == 0);

  wifi_console::Parser parser;
  uint32_t time = 100;
  wifi_console::Event event = feedLine(parser, "WIFI CLEAR\n", time);
  assert(event.type == wifi_console::EventType::Clear);
  assert(wifi_config::clear());
  wifi_config::Profile loaded = {};
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Missing);
  assert(strcmp(wifi_console::statusResponse(false, false, false), "WIFI OK UNCONFIGURED OFFLINE") == 0);
}

static void testIdleRecoveryPolicy() {
  const uint32_t batteryIdleTimeoutMs = 45000;
  assert(!power_policy::shouldEnterDeepSleep(false, 45000, batteryIdleTimeoutMs));
  assert(power_policy::shouldEnterDeepSleep(false, 45001, batteryIdleTimeoutMs));
  assert(!power_policy::shouldEnterDeepSleep(true, 60000, batteryIdleTimeoutMs));

  // A deliberate wake starts a fresh configuration window with the USB parser live.
  wifi_console::Parser parser;
  uint32_t time = 100;
  wifi_console::Event event = feedLine(parser, "WIFI STATUS\n", time);
  assert(event.type == wifi_console::EventType::Status);
}

int main() {
  testNvsProfile();
  testProfileValidation();
  testConsoleProtocol();
  testStatusAndOfflineRecovery();
  testIdleRecoveryPolicy();
  return 0;
}
