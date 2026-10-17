#include <assert.h>
#include <stdint.h>
#include <string.h>

#include <string>

#include <Preferences.h>
#include "power_policy.h"
#include "wifi_ble_flow.h"
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

class FakeNativeProvisioningManager : public wifi_ble_flow::ProvisioningManager {
 public:
  FakeNativeProvisioningManager() : failureLatched(false), resetSucceeds(true), resetCalls(0) {}

  bool resetWifiStateAfterFailure() override {
    ++resetCalls;
    if (!resetSucceeds) return false;
    failureLatched = false;
    return true;
  }

  bool acceptsCredentials() const { return !failureLatched; }

  bool failureLatched;
  bool resetSucceeds;
  size_t resetCalls;
};

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
  assert(Preferences::profileWrites() == 0);  // Missing namespace fallback does not write the Wi-Fi profile key.
  assert(wifi_config::save(profile));
  assert(Preferences::profileWrites() == 1);
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Valid);
  assert(strcmp(loaded.ssid, profile.ssid) == 0);
  assert(loaded.ssidLength == profile.ssidLength);
  assert(loaded.passwordLength == profile.passwordLength);
  assert(memcmp(loaded.password, profile.password, profile.passwordLength) == 0);
  assert(Preferences::profileWrites() == 1);  // Boot reads do not rewrite the Wi-Fi profile key.

  Preferences::corrupt("amu_wifi", "profile", 20);
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Invalid);
  assert(loaded.ssid[0] == '\0' && loaded.password[0] == '\0');
  assert(wifi_config::clear());
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Missing);
}

static void testBleProvisionFlow() {
  Preferences::reset();
  wifi_ble_flow::Session session;
  assert(!session.start(100, true, true));
  assert(!session.start(100, false, false));
  assert(session.start(100, false, true));

  wifi_config::Profile output = {};
  session.authenticationRejected();  // A wrong PoP yields no credential event to persist.
  assert(session.active());
  assert(!session.credentialsSucceeded(output));
  assert(wifi_config::load(output) == wifi_config::LoadResult::Missing);
  assert(Preferences::profileWrites() == 0);

  wifi_config::Profile candidate = makeProfile();
  assert(session.credentialsReceived(candidate));
  session.credentialsFailed();  // Bad AP credentials remain retryable and are not committed.
  assert(session.active());
  assert(!session.credentialsSucceeded(output));
  assert(wifi_config::load(output) == wifi_config::LoadResult::Missing);
  assert(Preferences::profileWrites() == 0);

  assert(session.credentialsReceived(candidate));
  assert(session.credentialsSucceeded(output));
  assert(session.state() == wifi_ble_flow::State::Succeeded);
  assert(wifi_config::save(output));
  assert(Preferences::profileWrites() == 1);
  assert(wifi_config::load(candidate) == wifi_config::LoadResult::Valid);
  wifi_config::wipeSensitive(output);
  wifi_config::wipeSensitive(candidate);

  session.stop();
  assert(session.restartRequiresReboot());
  assert(!session.start(0, false, true));  // FREE_BLE permits only one manager lifetime per boot.

  wifi_console::Parser usbRecovery;
  uint32_t usbTime = 0;
  wifi_console::Event usbEvent = feedLine(usbRecovery, "WIFI SET 6 8\n", usbTime);
  assert(usbEvent.type == wifi_console::EventType::SetReady);  // USB SET still overrides the reboot-only BLE retry.
  const char usbPayload[] = "lab-2gxxxxxxxx";
  bool usbPayloadAccepted = false;
  for (size_t i = 0; i < sizeof(usbPayload) - 1; ++i) {
    if (usbRecovery.feed(static_cast<uint8_t>(usbPayload[i]), usbTime++, usbEvent)) usbPayloadAccepted = true;
  }
  assert(usbPayloadAccepted && usbEvent.type == wifi_console::EventType::Set);
  assert(wifi_config::validProfile(usbEvent.profile));

  wifi_ble_flow::Session timeoutSession;
  assert(timeoutSession.start(0, false, true));
  assert(!timeoutSession.expire(wifi_ble_flow::kProvisionWindowMs - 1, true));
  assert(timeoutSession.expire(wifi_ble_flow::kProvisionWindowMs, true));
  assert(timeoutSession.state() == wifi_ble_flow::State::TimedOut);

  wifi_ble_flow::Session unplugSession;
  assert(unplugSession.start(10, false, true));
  assert(unplugSession.expire(11, false));  // Unplugging USB closes the setup window.
}

static void testNativeCredentialFailureRetry() {
  wifi_ble_flow::Session session;
  assert(session.start(100, false, true));

  FakeNativeProvisioningManager manager;
  manager.failureLatched = true;  // The native provisioner rejects another credential set until reset.
  assert(!manager.acceptsCredentials());
  wifi_config::Profile firstCandidate = makeProfile();
  assert(session.credentialsReceived(firstCandidate));
  assert(wifi_ble_flow::resetAfterCredentialsFailure(session, manager));
  assert(manager.resetCalls == 1);
  assert(manager.acceptsCredentials());
  assert(session.active());

  wifi_config::Profile correctedCandidate = makeProfile();
  correctedCandidate.ssid[0] = 'o';
  assert(session.credentialsReceived(correctedCandidate));
  wifi_config::Profile persisted = {};
  assert(session.credentialsSucceeded(persisted));
  assert(strcmp(persisted.ssid, "oab-2g") == 0);

  FakeNativeProvisioningManager failedReset;
  failedReset.failureLatched = true;
  failedReset.resetSucceeds = false;
  wifi_ble_flow::Session failedSession;
  assert(failedSession.start(100, false, true));
  assert(!wifi_ble_flow::resetAfterCredentialsFailure(failedSession, failedReset));
  assert(failedReset.failureLatched);  // Firmware treats this as terminal and stops the manager.
  wifi_config::wipeSensitive(firstCandidate);
  wifi_config::wipeSensitive(correctedCandidate);
  wifi_config::wipeSensitive(persisted);
}

static void testPopDisplayLayout() {
  uint8_t entropy[wifi_ble_flow::kPopEntropyBytes] = {};
  char pop[wifi_ble_flow::kPopLength + 1] = {};
  char first[wifi_ble_flow::kPopLength / 2 + 1] = {};
  char second[wifi_ble_flow::kPopLength / 2 + 1] = {};
  assert(wifi_ble_flow::formatPop(entropy, pop));
  memset(entropy, 0xFF, sizeof(entropy));
  assert(wifi_ble_flow::formatPop(entropy, pop));
  assert(wifi_ble_flow::splitPopForDisplay(pop, first, second));
  assert(strlen(first) == 13 && strlen(second) == 13);
  assert(strcmp(first, "ZZZZZZZZZZZZZ") == 0);
  assert(strcmp(second, "ZZZZZZZZZZZZW") == 0);
  assert(!wifi_ble_flow::splitPopForDisplay("too-short", first, second));
  assert(first[0] == '\0' && second[0] == '\0');
}

static void testPerSessionPopFormatting() {
  uint8_t entropy[wifi_ble_flow::kPopEntropyBytes] = {};
  char pop[wifi_ble_flow::kPopLength + 1] = {};
  assert(wifi_ble_flow::formatPop(entropy, pop));
  assert(strlen(pop) == wifi_ble_flow::kPopLength);
  for (size_t i = 0; i < strlen(pop); ++i) assert(pop[i] == '0');
  memset(entropy, 0xFF, sizeof(entropy));
  assert(wifi_ble_flow::formatPop(entropy, pop));
  assert(strlen(pop) == wifi_ble_flow::kPopLength);
  for (size_t i = 0; i < wifi_ble_flow::kPopLength - 1; ++i) assert(pop[i] == 'Z');
  assert(pop[wifi_ble_flow::kPopLength - 1] == 'W');  // The final 3 entropy bits are zero-padded.
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
  testBleProvisionFlow();
  testNativeCredentialFailureRetry();
  testPopDisplayLayout();
  testPerSessionPopFormatting();
  testProfileValidation();
  testConsoleProtocol();
  testStatusAndOfflineRecovery();
  testIdleRecoveryPolicy();
  return 0;
}

