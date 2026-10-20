#include <assert.h>
#include <stdint.h>
#include <string.h>

#include <Preferences.h>
#include "wifi_ble_flow.h"
#include "wifi_config.h"
#include "wifi_setup_intent.h"

static wifi_config::Profile makeProfile(const char* ssid, char passwordByte) {
  wifi_config::Profile profile = {};
  const size_t ssidLength = strlen(ssid);
  assert(ssidLength > 0 && ssidLength <= 32);
  memcpy(profile.ssid, ssid, ssidLength);
  profile.ssidLength = static_cast<uint8_t>(ssidLength);
  memset(profile.password, passwordByte, 8);
  profile.password[8] = '\0';
  profile.passwordLength = 8;
  return profile;
}

static bool sameProfile(const wifi_config::Profile& left, const wifi_config::Profile& right) {
  return left.ssidLength == right.ssidLength && left.passwordLength == right.passwordLength &&
         memcmp(left.ssid, right.ssid, left.ssidLength) == 0 &&
         memcmp(left.password, right.password, left.passwordLength) == 0;
}

static void testOneShotBootIntent() {
  uint32_t intent = wifi_setup_intent::kBootIntentMagic;
  assert(wifi_setup_intent::consume(intent, true));
  assert(intent == 0);
  assert(!wifi_setup_intent::consume(intent, true));

  intent = wifi_setup_intent::kBootIntentMagic;
  assert(!wifi_setup_intent::consume(intent, false));
  assert(intent == 0);

  intent = 0x12345678UL;
  assert(!wifi_setup_intent::consume(intent, true));
  assert(intent == 0);
}

static void testFiveSecondGestureAndConflictGates() {
  assert(wifi_setup_intent::kPhysicalGestureHoldMs == 5000);
  assert(wifi_setup_intent::kManagerDrainTimeoutMs == 30000);
  assert(wifi_setup_intent::setupGestureEligible(true, false, false, false, true));
  assert(wifi_setup_intent::setupGestureEligible(false, true, false, false, true));
  assert(!wifi_setup_intent::setupGestureEligible(false, false, false, false, true));
  assert(!wifi_setup_intent::setupGestureEligible(true, false, true, false, true));
  assert(!wifi_setup_intent::setupGestureEligible(false, true, false, true, true));
  assert(!wifi_setup_intent::setupGestureEligible(true, false, false, false, false));
}

static void testBatteryEntryAndAbortPolicy() {
  assert(!wifi_setup_intent::batteryAllowsEntry(19, false));
  assert(wifi_setup_intent::batteryAllowsEntry(20, false));
  assert(wifi_setup_intent::batteryAllowsEntry(100, false));
  assert(!wifi_setup_intent::batteryAllowsEntry(101, false));
  assert(wifi_setup_intent::batteryAllowsEntry(0, true));

  assert(wifi_setup_intent::abortForBattery(14, false));
  assert(!wifi_setup_intent::abortForBattery(15, false));
  assert(!wifi_setup_intent::abortForBattery(100, false));
  assert(wifi_setup_intent::abortForBattery(101, false));
  assert(!wifi_setup_intent::abortForBattery(1, true));
}

static void assertSavedProfile(const wifi_config::Profile& expected) {
  wifi_config::Profile loaded = {};
  assert(wifi_config::load(loaded) == wifi_config::LoadResult::Valid);
  assert(sameProfile(loaded, expected));
  wifi_config::wipeSensitive(loaded);
}

static void testConfiguredSetupIsExplicitAndFailureKeepsOldProfile() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));
  assert(Preferences::profileWrites() == 1);

  wifi_ble_flow::Session normalSession;
  assert(!normalSession.start(100, true, true));  // A configured profile never advertises in normal mode.
  assert(!normalSession.start(100, false, false));

  wifi_ble_flow::Session setupSession;
  assert(setupSession.start(100, true, false, true, wifi_setup_intent::kPhysicalSetupWindowMs));
  assert(setupSession.active());  // An owner gesture permits configured, battery-powered setup.
  setupSession.authenticationRejected();  // A wrong PoP produces no credential or storage event.
  assert(setupSession.active());
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  assert(setupSession.credentialsReceived(candidate));
  assert(!wifi_setup_intent::networkReady(false, true, true));
  assert(!wifi_setup_intent::networkReady(true, false, true));
  assert(!wifi_setup_intent::networkReady(true, true, false));
  assert(Preferences::profileWrites() == 1);  // Candidate credentials stay in RAM until link + IP.

  for (int attempt = 0; attempt < 3; ++attempt) {
    if (attempt > 0) assert(setupSession.credentialsReceived(candidate));
    const bool retryAllowed = setupSession.credentialsFailed();
    assert(retryAllowed == (attempt < 2));
    assert(Preferences::profileWrites() == 1);
    assertSavedProfile(oldProfile);
  }
  assert(!setupSession.active());  // Bad credentials are capped at three attempts.
  assert(!setupSession.start(200, true, false, true, wifi_setup_intent::kPhysicalSetupWindowMs));

  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testSuccessfulAssociationCommitsNewSingleProfile() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  wifi_ble_flow::Session session;
  assert(session.start(500, true, true, true, wifi_setup_intent::kPhysicalSetupWindowMs));
  assert(session.credentialsReceived(candidate));

  wifi_config::Profile output = {};
  if (wifi_setup_intent::networkReady(true, true, true)) {
    assert(session.credentialsSucceeded(output));
    assert(wifi_config::save(output));
  }
  assert(Preferences::profileWrites() == 2);
  assertSavedProfile(candidate);
  wifi_config::wipeSensitive(output);
  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testNvsWriteFailureLeavesOldProfile() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  Preferences::failProfileWrites(true);
  assert(!wifi_config::save(candidate));
  Preferences::failProfileWrites(false);
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testBoundedWindowCancelAndUsbDisconnect() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  assert(wifi_config::save(oldProfile));

  wifi_ble_flow::Session ownerSession;
  assert(ownerSession.start(1000, true, true, true, wifi_setup_intent::kPhysicalSetupWindowMs));
  assert(!ownerSession.expire(1000 + wifi_setup_intent::kPhysicalSetupWindowMs - 1, false));
  ownerSession.stop();  // Physical cancel stops BLE but never clears the saved profile.
  assert(!ownerSession.active());
  assertSavedProfile(oldProfile);

  wifi_ble_flow::Session timedOutSession;
  assert(timedOutSession.start(2000, true, false, true, wifi_setup_intent::kPhysicalSetupWindowMs));
  assert(timedOutSession.expire(2000 + wifi_setup_intent::kPhysicalSetupWindowMs, false));
  assert(timedOutSession.state() == wifi_ble_flow::State::TimedOut);
  assertSavedProfile(oldProfile);

  wifi_ble_flow::Session firstBootSession;
  assert(firstBootSession.start(3000, false, true));
  assert(firstBootSession.expire(3001, false));  // USB-only first boot retains the W1.5 unplug behavior.
  assertSavedProfile(oldProfile);

  wifi_config::wipeSensitive(oldProfile);
}

int main() {
  testOneShotBootIntent();
  testFiveSecondGestureAndConflictGates();
  testBatteryEntryAndAbortPolicy();
  testConfiguredSetupIsExplicitAndFailureKeepsOldProfile();
  testSuccessfulAssociationCommitsNewSingleProfile();
  testNvsWriteFailureLeavesOldProfile();
  testBoundedWindowCancelAndUsbDisconnect();
  return 0;
}

