"""Host regression checks for production portal code; no ESP8266 required.

Run: python tests/test_portal_regressions.py --cxx /path/to/g++
The harness compiles the actual parameter, menu and scan implementations with
small WiFi/HTTP fakes. It does not emulate the ESP8266 allocator or radio.
"""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HEADER = (ROOT / "pio/lib/WiFiManagerKT/WiFiManagerKT.h").read_text(encoding="utf-8")
SOURCE = (ROOT / "pio/lib/WiFiManagerKT/WiFiManagerKT.cpp").read_text(encoding="utf-8")
FIRMWARE = (ROOT / "pio/src/iSpindel.cpp").read_text(encoding="utf-8")


def function(signature):
    start = SOURCE.index(signature)
    end = SOURCE.index("\n}\n", start) + 3
    return SOURCE[start:end]


def harness():
    constants = "\n".join(
        re.search(rf"^const char {name}\[\].*;$", HEADER, re.MULTILINE).group(0)
        for name in ("HTTP_HEADER", "HTTP_STYLE", "HTTP_HEADER_END", "HTTP_PORTAL_OPTIONS", "HTTP_END")
    )
    parameter_class = HEADER.split("class WiFiManagerParameter\n", 1)[1].split("class WiFiManager\n", 1)[0]
    parameter_code = SOURCE.split("WiFiManagerParameter::WiFiManagerParameter", 1)[1].split(
        "WiFiManager::WiFiManager()", 1
    )[0]
    sync_code = "auto syncCalibrationParameters = [&]() {" + FIRMWARE.split(
        "auto syncCalibrationParameters = [&]() {", 1
    )[1].split("\n  };", 1)[0] + "\n  };"
    return r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <string>
#include <type_traits>
#define PROGMEM
#define F(x) x
#define FPSTR(x) x
#define DEBUG_WM(x) ((void)0)
#define WFM_LABEL_BEFORE 1
#define CONTENT_LENGTH_UNKNOWN size_t(-1)
#define WL_CONNECTED 3
using std::isnan;

// Limit each temporary string to 512 bytes to catch menu assembly regressions.
struct String : std::string {
  using std::string::string;
  String() = default;
  String(const std::string &s) : std::string(s) {}
  String(float value, uint8_t decimals) {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%.*f", decimals, double(value));
    assign(buf);
  }
  String &operator+=(const char *s) {
    assert(size() + std::strlen(s) <= 512);
    append(s);
    return *this;
  }
  String &operator+=(const String &s) { return *this += s.c_str(); }
  void replace(const char *from, const char *to) {
    auto pos = find(from);
    if (pos != npos) std::string::replace(pos, std::strlen(from), to);
  }
};
unsigned long now = 1234;
unsigned long millis() { return now; }
struct IP { String toString() { return "192.168.1.10"; } };
struct FakeWiFi {
  int found = 0, state = 0;
  String ssid;
  int scanNetworks() { return found; }
  String SSID() { return ssid; }
  String SSID(int i) { return i < 2 ? "same" : "other"; }
  int RSSI(int i) { return -40 - 10 * i; }
  int status() { return state; }
  IP localIP() { return {}; }
} WiFi;
struct FakeServer {
  std::string body;
  std::map<std::string, std::string> headers;
  bool started = false, finished = false;
  void sendHeader(const char *k, const char *v) { headers[k] = v; }
  void setContentLength(size_t) {}
  void send(int code, const char *, const String &s) {
    assert(code == 200);
    started = true;
    body = s;
  }
  void sendContent(const String &s) {
    assert(started && !finished);
    body += s;
    if (s.empty()) finished = true;
  }
  void sendContent_P(const char *s) { sendContent(String(s)); }
};
''' + constants + "\nclass WiFiManagerParameter\n" + parameter_class + (
        "\nWiFiManagerParameter::WiFiManagerParameter" + parameter_code
    ) + r'''
struct WiFiManager {
  FakeServer storage, *server = &storage;
  const char *_apName = "iSpindel_test";
  unsigned long _configPortalStart = 0;
  bool _removeDuplicateAPs = true;
  int _minimumQuality = -1;
  bool captivePortal() { return false; }
  void header();
  void reportStatus(String &);
  void handleRoot();
  int scanWifiNetworks(int **);
  int getRSSIasQuality(int);
};
''' + "\n".join(function(sig) for sig in (
        "void WiFiManager::header()",
        "void WiFiManager::reportStatus(String &page)",
        "void WiFiManager::handleRoot()",
        "int WiFiManager::scanWifiNetworks(int **indicesptr)",
        "int WiFiManager::getRSSIasQuality(int RSSI)",
    )) + r'''
String htmlencode(const char *s) { return s; }
''' + (
        "static String calValue(" + FIRMWARE.split("static String calValue(", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    ) + r'''
int main() {
  static_assert(!std::is_copy_constructible<WiFiManagerParameter>::value,
                "Owned buffers must not be shallow-copied");
  WiFiManagerParameter exact("id", "label", "12345678", 8);
  assert(std::strlen(exact.getValue()) == 8);
  assert(exact.getValue()[8] == '\0');
  exact.setValue("abcdefghijkl");
  assert(std::strcmp(exact.getValue(), "abcdefgh") == 0);
  exact.setValue("x");
  assert(std::strcmp(exact.getValue(), "x") == 0);
  exact.setValue(nullptr);
  assert(exact.getValue()[0] == '\0');
  WiFiManagerParameter empty("id", "label", nullptr, 0);
  assert(empty.getValue()[0] == '\0');
  WiFiManagerParameter html("<fieldset>Calibration</fieldset>");
  html.setValue("ignored");
  assert(html.getValue() == nullptr && html.getID() == nullptr);
  std::cout << "PASS: parameter boundaries, empty values and custom HTML\n";

  WiFiManager manager;
  for (int result : {0, -1, -2}) {
    WiFi.found = result;
    int sentinel;
    int *indices = &sentinel;
    assert(manager.scanWifiNetworks(&indices) == 0);
    assert(indices == nullptr);
    std::free(indices);
  }
  WiFi.found = 3;
  int *indices = nullptr;
  assert(manager.scanWifiNetworks(&indices) == 3);
  assert(indices[0] == 0 && indices[1] == -1 && indices[2] == 2);
  std::free(indices);
  std::cout << "PASS: empty, failed and successful WiFi scans\n";

  for (int state : {0, WL_CONNECTED}) {
    for (const char *ssid : {"", "Configured_WiFi"}) {
      WiFi.ssid = ssid;
      WiFi.state = state;
      manager.storage = FakeServer{};
      ++now;
      manager.handleRoot();
      auto &response = manager.storage;
      assert(response.finished);
      assert(response.body.find("action=\"/cal\"") != std::string::npos);
      assert(response.body.find(">Calibration</button>") != std::string::npos);
      assert(response.body.find("<script>") == std::string::npos);
      assert(response.headers["Cache-Control"].find("no-store") != std::string::npos);
      assert(manager._configPortalStart == now);
    }
  }
  std::cout << "PASS: complete Calibration menu with/without WiFi, 512-byte temporaries\n";

  constexpr int CAL_POINTS = 6;
  struct {
    const char *polynominal = "0.01*tilt^2-0.2*tilt+1";
    float calTilt[CAL_POINTS] = {25, 35, 45, 55, 65, NAN};
    float calSG[CAL_POINTS] = {1, 1.02f, 1.04f, 1.06f, 1.08f, NAN};
  } myData;
  WiFiManagerParameter custom_polynom("POLYN", "poly", "old polynomial", 250);
  WiFiManagerParameter t0("t0", "", "old", 10), t1("t1", "", "old", 10),
      t2("t2", "", "old", 10), t3("t3", "", "old", 10),
      t4("t4", "", "old", 10), t5("t5", "", "old", 10);
  WiFiManagerParameter s1("s1", "", "old", 10), s2("s2", "", "old", 10),
      s3("s3", "", "old", 10), s4("s4", "", "old", 10), s5("s5", "", "old", 10);
  WiFiManagerParameter *calTiltParams[CAL_POINTS] = {&t0, &t1, &t2, &t3, &t4, &t5};
  WiFiManagerParameter *calSGParams[CAL_POINTS] = {nullptr, &s1, &s2, &s3, &s4, &s5};
''' + sync_code + r'''
  syncCalibrationParameters();
  assert(std::strcmp(custom_polynom.getValue(), myData.polynominal) == 0);
  assert(std::strcmp(t0.getValue(), "25.00") == 0);
  assert(std::strcmp(s1.getValue(), "1.020") == 0);
  assert(t5.getValue()[0] == '\0' && s5.getValue()[0] == '\0');
  myData.calTilt[0] = 27.5f;
  myData.polynominal = "new polynomial";
  syncCalibrationParameters();
  assert(std::strcmp(t0.getValue(), "27.50") == 0);
  assert(std::strcmp(custom_polynom.getValue(), "new polynomial") == 0);
  std::cout << "PASS: calibration updates replace stale configuration form values\n";
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    if not args.cxx:
        parser.error("Pass --cxx with the path to a host C++ compiler")
    env = dict(os.environ)
    env["PATH"] = str(Path(args.cxx).resolve().parent) + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="ispindel-portal-") as tmp:
        source = Path(tmp) / "portal.cpp"
        binary = Path(tmp) / "portal.exe"
        source.write_text(harness(), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++11", "-Wall", "-Wextra", str(source), "-o", str(binary)], check=True, env=env)
        subprocess.run([str(binary)], check=True, env=env)


if __name__ == "__main__":
    main()
