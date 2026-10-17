#include "jellii_text_utf8.h"
#include <cassert>
#include <iostream>
#include <string>
#include <vector>

using std::string;

static size_t count_points(const char* p, size_t bytes) {
  size_t n = 0;
  for (size_t i = 0; i < bytes; i = jellii_text::next(p, bytes, i)) ++n;
  return n;
}
static std::vector<string> split(const string& s, int width) {
  std::vector<string> out;
  auto measure = [&](size_t start, size_t end) {
    return static_cast<int>(count_points(s.data() + start, end - start));
  };
  auto emit = [&](size_t start, size_t end) { out.emplace_back(s.substr(start, end - start)); };
  jellii_text::wrap(s.data(), s.length(), width, measure, emit);
  return out;
}
int main() {
  const string chinese = "繁體中文測試";
  assert(jellii_text::next(chinese.data(), chinese.size(), 0) == 3);
  assert(jellii_text::prefix(chinese.data(), chinese.size(), 5) == 3);
  assert(jellii_text::prefix(chinese.data(), chinese.size(), 6) == 6);
  assert(jellii_text::prefix(chinese.data(), chinese.size(), 2048) == chinese.size());
  assert((split(chinese, 2) == std::vector<string>{"繁體", "中文", "測試"}));
  assert((split("Hello world again", 6) == std::vector<string>{"Hello", "world", "again"}));
  assert((split("你好 world 123", 5) == std::vector<string>{"你好", "world", "123"}));
  assert((split("第一行\n第二行", 20) == std::vector<string>{"第一行", "第二行"}));
  assert((split("第一行\r\n第二行", 20) == std::vector<string>{"第一行", "第二行"}));
  assert((split("你好  world", 5) == std::vector<string>{"你好", "world"}));
  assert((split("\xF0\x9F\xAA\xBC" "你好", 1) == std::vector<string>{"\xF0\x9F\xAA\xBC", "你", "好"}));
  assert(jellii_text::fitting_prefix(chinese.data(), chinese.size(), 3,
    [&](size_t a, size_t b){return (int)count_points(chinese.data() + a, b - a);}) == 9);
  const string malformed = "\xE4\xB8" "A";
  assert(split(malformed, 1).size() == 3);
  assert(split("abc", 0).empty());
  std::cout << "Jellii UTF-8 display tests PASS\n";
}

