#pragma once

#include <stddef.h>
#include <stdint.h>

#include <map>
#include <set>
#include <string>
#include <vector>

class Preferences {
 public:
  bool begin(const char* nameSpace, bool readOnly) {
    nameSpace_ = nameSpace;
    readOnly_ = readOnly;
    if (readOnly_ && namespaces().find(nameSpace_) == namespaces().end()) return false;
    if (!readOnly_) namespaces().insert(nameSpace_);
    return true;
  }

  size_t getBytesLength(const char* key) const {
    const std::map<std::string, std::vector<uint8_t> >::const_iterator found =
        values().find(composeKey(key));
    return found == values().end() ? 0 : found->second.size();
  }

  size_t getBytes(const char* key, void* destination, size_t length) const {
    const std::map<std::string, std::vector<uint8_t> >::const_iterator found =
        values().find(composeKey(key));
    if (found == values().end() || found->second.size() != length) return 0;
    uint8_t* output = static_cast<uint8_t*>(destination);
    for (size_t i = 0; i < length; ++i) output[i] = found->second[i];
    return length;
  }

  size_t putBytes(const char* key, const void* source, size_t length) {
    if (readOnly_) return 0;
    const uint8_t* input = static_cast<const uint8_t*>(source);
    std::vector<uint8_t> bytes(input, input + length);
    values()[composeKey(key)] = bytes;
    ++writeCount();
    return length;
  }

  bool remove(const char* key) {
    if (readOnly_) return false;
    return values().erase(composeKey(key)) != 0;
  }

  void end() {}

  static void reset() {
    values().clear();
    namespaces().clear();
    writeCount() = 0;
  }

  static void corrupt(const char* nameSpace, const char* key, size_t index) {
    std::map<std::string, std::vector<uint8_t> >::iterator found =
        values().find(std::string(nameSpace) + "/" + key);
    if (found != values().end() && index < found->second.size()) found->second[index] ^= 0x5A;
  }

  static size_t writes() { return writeCount(); }

 private:
  std::string composeKey(const char* key) const { return nameSpace_ + "/" + key; }

  static std::map<std::string, std::vector<uint8_t> >& values() {
    static std::map<std::string, std::vector<uint8_t> > stored;
    return stored;
  }

  static std::set<std::string>& namespaces() {
    static std::set<std::string> opened;
    return opened;
  }

  static size_t& writeCount() {
    static size_t count = 0;
    return count;
  }

  std::string nameSpace_;
  bool readOnly_;
};
