#include <assert.h>
#include <stdint.h>
#include <string.h>

#include <Preferences.h>
#include "wifi_ble_flow.h"
#include "wifi_config.h"
#include "wifi_provisioning_commit_gate.h"
#include "wifi_provisioning_event_queue.h"
#include "wifi_provisioning_lifecycle.h"
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
  assert(wifi_setup_intent::managerDrainTimeoutMs(30000) == 45000);
  assert(wifi_setup_intent::managerDrainTimeoutMs(90000) == 105000);
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

static void testCredentialSuccessThenDeinitThenIpCommitsOnce() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  wifi_provisioning_commit_gate::Gate gate;
  assert(gate.acceptCredentialsSucceeded(100));
  assert(gate.onProviderDeinitialized());
  assert(gate.providerEnded());
  assert(gate.poll(101, false, false) == wifi_provisioning_commit_gate::PollResult::Waiting);
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  const wifi_provisioning_commit_gate::PollResult ready = gate.poll(102, true, true);
  assert(ready == wifi_provisioning_commit_gate::PollResult::Commit);
  if (ready == wifi_provisioning_commit_gate::PollResult::Commit) {
    assert(wifi_config::save(candidate));
  }
  assert(gate.poll(103, true, true) == wifi_provisioning_commit_gate::PollResult::None);
  assert(Preferences::profileWrites() == 2);
  assertSavedProfile(candidate);

  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testCredentialSuccessThenDeinitThenIpTimeoutKeepsOldProfile() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  assert(wifi_config::save(oldProfile));

  wifi_provisioning_commit_gate::Gate gate;
  assert(gate.acceptCredentialsSucceeded(1000));
  assert(gate.onProviderDeinitialized());
  assert(gate.poll(1000 + wifi_provisioning_commit_gate::kAssociationTimeoutMs - 1,
                   false, false) == wifi_provisioning_commit_gate::PollResult::Waiting);
  assertSavedProfile(oldProfile);
  assert(gate.poll(1000 + wifi_provisioning_commit_gate::kAssociationTimeoutMs,
                   false, false) == wifi_provisioning_commit_gate::PollResult::TimedOut);
  assert(!gate.pending());
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  wifi_config::wipeSensitive(oldProfile);
}

static void testSdkAutoStopEndThenArduinoCoreDeinit() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  wifi_provisioning_lifecycle::ManagerLifecycle manager;
  assert(manager.onInitialized());
  assert(manager.active());
  wifi_provisioning_commit_gate::Gate commit;
  assert(commit.acceptCredentialsSucceeded(100));
  assert(commit.poll(100, false, false) == wifi_provisioning_commit_gate::PollResult::Waiting);
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  assert(commit.poll(101, true, true) == wifi_provisioning_commit_gate::PollResult::Commit);
  assert(wifi_config::save(candidate));
  assert(Preferences::profileWrites() == 2);
  assertSavedProfile(candidate);

  // SDK auto-stop leaves a window for the phone app. The application observes
  // END but leaves deinit to Arduino-ESP32's default-event-loop handler.
  assert(!manager.stopRequested());
  manager.onServiceEnded();
  assert(manager.serviceEnded());
  assert(!manager.deinitializationRequested());
  assert(manager.active());
  manager.onServiceEnded();
  assert(manager.serviceEnded());
  assert(!manager.requestStop());

  // Arduino's handler calls network_prov_mgr_deinit(); that external call
  // delivers DEINIT back to the application callback.
  assert(manager.onDeinitialized());
  assert(!manager.active());
  assert(!manager.onDeinitialized());
  assert(commit.poll(102, true, true) == wifi_provisioning_commit_gate::PollResult::None);
  assert(Preferences::profileWrites() == 2);

  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testCancelStopAndEndAreIdempotent() {
  wifi_provisioning_lifecycle::ManagerLifecycle manager;
  assert(manager.onInitialized());
  assert(manager.requestStop());
  assert(!manager.requestStop());
  assert(manager.stopRequested());

  assert(!wifi_provisioning_lifecycle::discardOnCancel(
      wifi_provisioning_lifecycle::ProviderEvent::ServiceEnded));
  manager.onServiceEnded();
  assert(manager.serviceEnded());
  assert(!manager.deinitializationRequested());
  assert(!wifi_provisioning_lifecycle::discardOnCancel(
      wifi_provisioning_lifecycle::ProviderEvent::Deinitialized));
  assert(wifi_provisioning_lifecycle::discardOnCancel(
      wifi_provisioning_lifecycle::ProviderEvent::CredentialsReceived));
  assert(manager.onDeinitialized());
  assert(!manager.onDeinitialized());
  assert(!manager.active());
}

static void testStartupFailureIsTheOnlyApplicationOwnedDeinit() {
  wifi_provisioning_lifecycle::ManagerLifecycle manager;
  assert(manager.onInitialized());
  assert(manager.requestDeinitialize());
  assert(manager.deinitializationRequested());
  assert(!manager.requestDeinitialize());
  assert(manager.onDeinitialized());
  assert(!manager.active());
}

static void testExternalDeinitMayBeDequeuedBeforeEnd() {
  wifi_provisioning_lifecycle::ManagerLifecycle manager;
  assert(manager.onInitialized());

  // The core can invoke the application's DEINIT callback before the loop has
  // consumed its queued END event. A late END must not reactivate the manager.
  assert(manager.onDeinitialized());
  assert(!manager.active());
  manager.onServiceEnded();
  assert(!manager.serviceEnded());
  assert(!manager.deinitializationRequested());
}

static void testServiceOverflowPreservesEndBeforeCredentials() {
  using wifi_provisioning_event_queue::Event;
  using wifi_provisioning_event_queue::Queue;
  using wifi_provisioning_event_queue::ServiceResult;
  using wifi_provisioning_lifecycle::ManagerLifecycle;
  using wifi_provisioning_lifecycle::ProviderEvent;
  Queue queue;
  ManagerLifecycle manager;
  assert(manager.onInitialized());

  wifi_config::Profile profile = makeProfile("host-test-network", 't');
  assert(queue.enqueue(ProviderEvent::ServiceEnded, NULL, 100));
  for (uint32_t i = 0; i < wifi_provisioning_event_queue::kCredentialQueueCapacity; ++i) {
    assert(queue.enqueue(ProviderEvent::CredentialsReceived, &profile, 101 + i));
  }
  assert(!queue.enqueue(ProviderEvent::CredentialsSucceeded, NULL, 105));

  // Exercise the exact portable service step called by wifi_ble_provisioning::service().
  Event event = {};
  wifi_provisioning_event_queue::ServiceStep step =
      wifi_provisioning_event_queue::serviceStep(queue, event, manager, 200);
  assert(step.result == ServiceResult::Overflow && step.requestStop);
  assert(manager.stopRequested());
  wifi_provisioning_event_queue::Snapshot snapshot = {};
  queue.snapshot(snapshot);
  assert(snapshot.credentialDepth == 0);
  assert(snapshot.serviceEndedPending && !snapshot.deinitializedPending);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 0);

  step = wifi_provisioning_event_queue::serviceStep(queue, event, manager, 201);
  assert(step.result == ServiceResult::Event && event.type == ProviderEvent::ServiceEnded);
  assert(manager.serviceEnded() && manager.active());
  assert(queue.enqueue(ProviderEvent::Deinitialized, NULL, 202));
  step = wifi_provisioning_event_queue::serviceStep(queue, event, manager, 203);
  assert(step.result == ServiceResult::Event && event.type == ProviderEvent::Deinitialized);
  assert(!manager.active());

  queue.snapshot(snapshot);
  assert(snapshot.callbacks[Queue::eventIndex(ProviderEvent::CredentialsReceived)] ==
         wifi_provisioning_event_queue::kCredentialQueueCapacity);
  assert(snapshot.dropped[Queue::eventIndex(ProviderEvent::CredentialsSucceeded)] == 1);
  assert(snapshot.callbacks[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 1);
  assert(snapshot.callbacks[Queue::eventIndex(ProviderEvent::Deinitialized)] == 1);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 1);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::Deinitialized)] == 1);
  assert(snapshot.overflowCount == 1);
  assert(!snapshot.serviceEndedPending && !snapshot.deinitializedPending);
  wifi_config::wipeSensitive(profile);
  wifi_config::wipeSensitive(event.profile);
}

static void testServiceOverflowPreservesDeinitBeforeEnd() {
  using wifi_provisioning_event_queue::Event;
  using wifi_provisioning_event_queue::Queue;
  using wifi_provisioning_event_queue::ServiceResult;
  using wifi_provisioning_lifecycle::ManagerLifecycle;
  using wifi_provisioning_lifecycle::ProviderEvent;
  Queue queue;
  ManagerLifecycle manager;
  assert(manager.onInitialized());

  assert(queue.enqueue(ProviderEvent::Deinitialized, NULL, 300));
  assert(queue.enqueue(ProviderEvent::ServiceEnded, NULL, 301));
  wifi_config::Profile profile = makeProfile("host-test-network", 't');
  for (uint32_t i = 0; i < wifi_provisioning_event_queue::kCredentialQueueCapacity; ++i) {
    assert(queue.enqueue(ProviderEvent::CredentialsReceived, &profile, 302 + i));
  }
  assert(!queue.enqueue(ProviderEvent::CredentialsFailed, NULL, 306));

  Event event = {};
  wifi_provisioning_event_queue::ServiceStep step =
      wifi_provisioning_event_queue::serviceStep(queue, event, manager, 400);
  assert(step.result == ServiceResult::Overflow && step.requestStop);
  assert(manager.stopRequested());
  wifi_provisioning_event_queue::Snapshot snapshot = {};
  queue.snapshot(snapshot);
  assert(snapshot.deinitializedPending && snapshot.serviceEndedPending);
  assert(snapshot.credentialDepth == 0);

  step = wifi_provisioning_event_queue::serviceStep(queue, event, manager, 401);
  assert(step.result == ServiceResult::Event && event.type == ProviderEvent::Deinitialized);
  assert(!manager.active());
  step = wifi_provisioning_event_queue::serviceStep(queue, event, manager, 402, true);
  assert(step.result == ServiceResult::Event && event.type == ProviderEvent::ServiceEnded);
  assert(!manager.active());
  queue.snapshot(snapshot);
  assert(!snapshot.deinitializedPending && !snapshot.serviceEndedPending);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::Deinitialized)] == 1);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 1);
  assert(snapshot.overflowCount == 1);
  wifi_config::wipeSensitive(profile);
  wifi_config::wipeSensitive(event.profile);
}

static void testTerminalQueuePreservesDeinitBeforeEndAndDeduplicatesPendingEvents() {
  using wifi_provisioning_event_queue::Event;
  using wifi_provisioning_event_queue::Queue;
  using wifi_provisioning_lifecycle::ProviderEvent;
  Queue queue;

  assert(queue.enqueue(ProviderEvent::Deinitialized, NULL, 300));
  assert(queue.enqueue(ProviderEvent::ServiceEnded, NULL, 301));
  assert(queue.enqueue(ProviderEvent::ServiceEnded, NULL, 302));

  Event event = {};
  bool overflowed = false;
  assert(queue.dequeue(event, overflowed, 310));
  assert(!overflowed && event.type == ProviderEvent::Deinitialized && event.callbackAtMs == 300);
  assert(queue.dequeue(event, overflowed, 311));
  assert(!overflowed && event.type == ProviderEvent::ServiceEnded && event.callbackAtMs == 301);
  assert(!queue.dequeue(event, overflowed, 312));

  wifi_provisioning_event_queue::Snapshot snapshot = {};
  queue.snapshot(snapshot);
  assert(snapshot.callbacks[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 2);
  assert(snapshot.dequeued[Queue::eventIndex(ProviderEvent::ServiceEnded)] == 1);
}

static void testManagerDrainFallbackUsesCompiledSdkTimeout() {
  const uint32_t startedAt = 1000;
  const uint32_t sdkAutoStopMs = 90000;
  const uint32_t fallbackMs = wifi_setup_intent::managerDrainTimeoutMs(sdkAutoStopMs);
  assert(fallbackMs == 105000);
  assert(!wifi_setup_intent::managerDrainFallbackExpired(
      startedAt + sdkAutoStopMs, startedAt, true, sdkAutoStopMs));
  assert(!wifi_setup_intent::managerDrainFallbackExpired(
      startedAt + fallbackMs - 1, startedAt, true, sdkAutoStopMs));
  assert(wifi_setup_intent::managerDrainFallbackExpired(
      startedAt + fallbackMs, startedAt, true, sdkAutoStopMs));
  assert(!wifi_setup_intent::managerDrainFallbackExpired(
      startedAt + fallbackMs, startedAt, false, sdkAutoStopMs));

  // Unsigned subtraction keeps the watchdog correct when millis() wraps.
  const uint32_t wrappedStart = UINT32_MAX - 50000U;
  assert(wifi_setup_intent::managerDrainFallbackExpired(
      wrappedStart + fallbackMs, wrappedStart, true, sdkAutoStopMs));
}

enum class PendingInterruption { ButtonCancel, LowBattery, SetupWindowExpired };

static void assertPendingInterruptionPreservesProfile(PendingInterruption interruption) {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  const uint32_t startedAt = 2000;
  wifi_ble_flow::Session session;
  assert(session.start(startedAt, true, true, true, wifi_setup_intent::kPhysicalSetupWindowMs));
  assert(session.credentialsReceived(candidate));
  wifi_provisioning_commit_gate::Gate gate;
  assert(gate.acceptCredentialsSucceeded(startedAt + 1));
  assert(gate.onProviderDeinitialized());

  switch (interruption) {
    case PendingInterruption::ButtonCancel:
      session.stop();
      gate.cancel();
      break;
    case PendingInterruption::LowBattery:
      assert(wifi_setup_intent::abortForBattery(14, false));
      session.stop();
      gate.cancel();
      break;
    case PendingInterruption::SetupWindowExpired:
      assert(session.expire(startedAt + wifi_setup_intent::kPhysicalSetupWindowMs, false));
      gate.cancel();
      break;
  }

  assert(!session.active());
  assert(gate.poll(startedAt + 2, true, true) == wifi_provisioning_commit_gate::PollResult::None);
  assert(Preferences::profileWrites() == 1);
  assertSavedProfile(oldProfile);

  wifi_config::wipeSensitive(oldProfile);
  wifi_config::wipeSensitive(candidate);
}

static void testCancelLowBatteryAndWindowExpiryDiscardPendingCandidate() {
  assertPendingInterruptionPreservesProfile(PendingInterruption::ButtonCancel);
  assertPendingInterruptionPreservesProfile(PendingInterruption::LowBattery);
  assertPendingInterruptionPreservesProfile(PendingInterruption::SetupWindowExpired);
}

static void testNvsFailureAfterAssociationDoesNotLoseOldProfile() {
  Preferences::reset();
  wifi_config::Profile oldProfile = makeProfile("old-network", 'o');
  wifi_config::Profile candidate = makeProfile("new-network", 'n');
  assert(wifi_config::save(oldProfile));

  wifi_provisioning_commit_gate::Gate gate;
  assert(gate.acceptCredentialsSucceeded(3000));
  assert(gate.onProviderDeinitialized());
  const wifi_provisioning_commit_gate::PollResult ready = gate.poll(3001, true, true);
  assert(ready == wifi_provisioning_commit_gate::PollResult::Commit);
  Preferences::failProfileWrites(true);
  if (ready == wifi_provisioning_commit_gate::PollResult::Commit) {
    assert(!wifi_config::save(candidate));
  }
  Preferences::failProfileWrites(false);
  assert(gate.poll(3002, true, true) == wifi_provisioning_commit_gate::PollResult::None);
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
  testCredentialSuccessThenDeinitThenIpCommitsOnce();
  testCredentialSuccessThenDeinitThenIpTimeoutKeepsOldProfile();
  testSdkAutoStopEndThenArduinoCoreDeinit();
  testCancelStopAndEndAreIdempotent();
  testStartupFailureIsTheOnlyApplicationOwnedDeinit();
  testExternalDeinitMayBeDequeuedBeforeEnd();
  testServiceOverflowPreservesEndBeforeCredentials();
  testServiceOverflowPreservesDeinitBeforeEnd();
  testTerminalQueuePreservesDeinitBeforeEndAndDeduplicatesPendingEvents();
  testManagerDrainFallbackUsesCompiledSdkTimeout();
  testCancelLowBatteryAndWindowExpiryDiscardPendingCandidate();
  testNvsFailureAfterAssociationDoesNotLoseOldProfile();
  testBoundedWindowCancelAndUsbDisconnect();
  return 0;
}

