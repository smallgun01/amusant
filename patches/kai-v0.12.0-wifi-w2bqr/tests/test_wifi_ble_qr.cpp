#include "wifi_ble_qr.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

namespace {

const char kTestService[] = "PROV_ABCDEF";
const char kTestPop[] = "0123456789ABCDEFGHJKMNPQRS";
const char kExpectedPayload[] =
    "{\"ver\":\"v1\",\"name\":\"PROV_ABCDEF\",\"pop\":\"0123456789ABCDEFGHJKMNPQRS\",\"transport\":\"ble\",\"security\":1}";

void testPayloadContract() {
  char payload[wifi_ble_qr::kPayloadCapacity] = {};
  assert(wifi_ble_qr::buildPayload(kTestService, kTestPop, payload, sizeof(payload)));
  assert(strcmp(payload, kExpectedPayload) == 0);
  assert(strlen(payload) == 99);
  for (size_t i = 0; i < strlen(payload); ++i) {
    const unsigned char c = static_cast<unsigned char>(payload[i]);
    assert(c >= 0x21 && c <= 0x7E);
  }
  assert(strstr(payload, "\"security\":1") != NULL);
  assert(strchr(payload, '\n') == NULL && strchr(payload, '\r') == NULL && strchr(payload, ' ') == NULL);

  char small[32] = "unchanged";
  assert(!wifi_ble_qr::buildPayload(kTestService, kTestPop, small, sizeof(small)));
  assert(small[0] == '\0');
  assert(!wifi_ble_qr::buildPayload("prov_ABCDEF", kTestPop, payload, sizeof(payload)));
  assert(payload[0] == '\0');
  assert(!wifi_ble_qr::buildPayload("PROV_ABCDEG", kTestPop, payload, sizeof(payload)));
  assert(!wifi_ble_qr::buildPayload(kTestService, "0123456789ABCDEFGHJKMNPQR", payload, sizeof(payload)));
  assert(!wifi_ble_qr::buildPayload(kTestService, "0123456789ABCDEFGHJKMNPQRI", payload, sizeof(payload)));
}

void testPageState() {
  wifi_ble_qr::PageState page;
  assert(!page.active() && !page.qrAvailable() && !page.qrPage());
  assert(!page.toggle());
  page.show(true, true);
  assert(page.active() && page.qrAvailable() && page.qrPage());
  assert(page.toggle() && !page.qrPage());
  assert(page.toggle() && page.qrPage());
  page.show(false, false);
  assert(page.active() && !page.qrAvailable() && !page.qrPage());
  assert(!page.toggle());
  page.clear();
  assert(!page.active() && !page.qrAvailable() && !page.qrPage());
}

void testMatrixAndQuietZone() {
  assert(wifi_ble_qr::kQrVersion == 6);
  assert(wifi_ble_qr::kModuleCount == 41);
  assert(wifi_ble_qr::kPixelsPerModule == 2);
  assert(wifi_ble_qr::kQuietZoneModules == 4);
  assert(wifi_ble_qr::kQrPixelSize == 98);
  assert(wifi_ble_qr::kQrLeft == 71 && wifi_ble_qr::kQrTop == 18);
  assert(wifi_ble_qr::kQrBottom == 116 && wifi_ble_qr::kFooterTop == 119);

  assert(wifi_ble_qr::begin(kTestService, kTestPop));
  assert(wifi_ble_qr::active() && wifi_ble_qr::size() == wifi_ble_qr::kModuleCount);
  bool original[wifi_ble_qr::kModuleCount][wifi_ble_qr::kModuleCount] = {};
  for (int y = 0; y < wifi_ble_qr::kModuleCount; ++y) {
    for (int x = 0; x < wifi_ble_qr::kModuleCount; ++x) {
      original[y][x] = wifi_ble_qr::darkModule(x, y);
    }
  }

  for (int y = 0; y < wifi_ble_qr::kQrPixelSize; ++y) {
    for (int x = 0; x < wifi_ble_qr::kQrPixelSize; ++x) {
      const int moduleX = x / wifi_ble_qr::kPixelsPerModule - wifi_ble_qr::kQuietZoneModules;
      const int moduleY = y / wifi_ble_qr::kPixelsPerModule - wifi_ble_qr::kQuietZoneModules;
      const bool expected = moduleX >= 0 && moduleX < wifi_ble_qr::kModuleCount &&
                            moduleY >= 0 && moduleY < wifi_ble_qr::kModuleCount &&
                            original[moduleY][moduleX];
      assert(wifi_ble_qr::darkPixel(x, y) == expected);
    }
  }
  for (int i = 0; i < wifi_ble_qr::kQrPixelSize; ++i) {
    assert(!wifi_ble_qr::darkPixel(i, 0));
    assert(!wifi_ble_qr::darkPixel(i, wifi_ble_qr::kQrPixelSize - 1));
    assert(!wifi_ble_qr::darkPixel(0, i));
    assert(!wifi_ble_qr::darkPixel(wifi_ble_qr::kQrPixelSize - 1, i));
  }
  assert(!wifi_ble_qr::darkPixel(-1, 0));
  assert(!wifi_ble_qr::darkPixel(wifi_ble_qr::kQrPixelSize, 0));

  // Repeated setup display calls do not regenerate or flicker the session QR.
  assert(!wifi_ble_qr::begin("PROV_123456", "ZZZZZZZZZZZZZZZZZZZZZZZZZZ"));
  for (int y = 0; y < wifi_ble_qr::kModuleCount; ++y) {
    for (int x = 0; x < wifi_ble_qr::kModuleCount; ++x) {
      assert(wifi_ble_qr::darkModule(x, y) == original[y][x]);
    }
  }

  wifi_ble_qr::clear();
  assert(!wifi_ble_qr::active() && wifi_ble_qr::size() == 0);
  assert(!wifi_ble_qr::darkModule(0, 0) && !wifi_ble_qr::darkPixel(20, 20));
}

void testSeparateSessionsDiffer() {
  bool first[wifi_ble_qr::kModuleCount][wifi_ble_qr::kModuleCount] = {};
  assert(wifi_ble_qr::begin(kTestService, kTestPop));
  for (int y = 0; y < wifi_ble_qr::kModuleCount; ++y)
    for (int x = 0; x < wifi_ble_qr::kModuleCount; ++x)
      first[y][x] = wifi_ble_qr::darkModule(x, y);
  wifi_ble_qr::clear();

  assert(wifi_ble_qr::begin("PROV_123456", "ZZZZZZZZZZZZZZZZZZZZZZZZZZ"));
  bool differs = false;
  for (int y = 0; y < wifi_ble_qr::kModuleCount; ++y)
    for (int x = 0; x < wifi_ble_qr::kModuleCount; ++x)
      differs |= first[y][x] != wifi_ble_qr::darkModule(x, y);
  assert(differs);
  wifi_ble_qr::clear();
}

void writePgm(const char* path) {
  assert(path != NULL);
  FILE* file = fopen(path, "wb");
  assert(file != NULL);
  assert(fprintf(file, "P5\n%d %d\n255\n", wifi_ble_qr::kQrPixelSize,
                 wifi_ble_qr::kQrPixelSize) > 0);
  for (int y = 0; y < wifi_ble_qr::kQrPixelSize; ++y) {
    for (int x = 0; x < wifi_ble_qr::kQrPixelSize; ++x) {
      const unsigned char pixel = wifi_ble_qr::darkPixel(x, y) ? 0 : 255;
      assert(fwrite(&pixel, 1, 1, file) == 1);
    }
  }
  assert(fclose(file) == 0);
}

}  // namespace

int main(int argc, char** argv) {
  testPayloadContract();
  testPageState();
  testMatrixAndQuietZone();
  testSeparateSessionsDiffer();
  assert(argc == 2);
  assert(wifi_ble_qr::begin(kTestService, kTestPop));
  writePgm(argv[1]);
  wifi_ble_qr::clear();
  puts("Wi-Fi QR encoder, payload, session stability, layout, and wipe tests passed.");
  return 0;
}
