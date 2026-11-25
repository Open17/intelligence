// =============================================================================
//  test_framework.hpp - a deliberately tiny unit-test helper
// =============================================================================
//
//  Real projects often use GoogleTest or Catch2. This header shows the core
//  idea in ~50 lines so there is nothing magic to learn:
//
//      TEST_CASE(my_test) {
//          CHECK(1 + 1 == 2);
//          CHECK_EQ(std::string("a") + "b", "ab");
//      }
//      int main() { return echo::test::runAll(); }
//
//  TEST_CASE registers a function in a global list (via a static object whose
//  constructor runs before main()). runAll() calls every function and counts
//  failed CHECKs. A non-zero exit code tells CTest the test failed.
// =============================================================================
#pragma once

#include <functional>
#include <iostream>
#include <string>
#include <vector>

namespace echo::test {

struct TestCase {
    std::string name;
    std::function<void()> fn;
};

inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> tests;  // created on first use
    return tests;
}

inline int& failureCount() {
    static int failures = 0;
    return failures;
}

struct Registrar {
    Registrar(const char* name, std::function<void()> fn) {
        registry().push_back({name, std::move(fn)});
    }
};

inline int runAll() {
    for (const auto& test : registry()) {
        const int before = failureCount();
        test.fn();
        std::cout << (failureCount() == before ? "[ PASS ] " : "[ FAIL ] ") << test.name << '\n';
    }
    std::cout << registry().size() << " test(s), " << failureCount() << " failed check(s)\n";
    return failureCount() == 0 ? 0 : 1;
}

}  // namespace echo::test

// Defines a test function and registers it automatically.
#define TEST_CASE(name)                                                    \
    static void name();                                                    \
    static const ::echo::test::Registrar name##_registrar(#name, &name);   \
    static void name()

#define CHECK(cond)                                                                   \
    do {                                                                              \
        if (!(cond)) {                                                                \
            std::cerr << __FILE__ << ':' << __LINE__ << ": CHECK(" #cond ") failed\n"; \
            ++::echo::test::failureCount();                                           \
        }                                                                             \
    } while (0)

#define CHECK_EQ(actual, expected)                                                   \
    do {                                                                             \
        const auto& a_ = (actual);                                                   \
        const auto& e_ = (expected);                                                 \
        if (!(a_ == e_)) {                                                           \
            std::cerr << __FILE__ << ':' << __LINE__ << ": CHECK_EQ(" #actual ", " #expected \
                      << ") failed: got [" << a_ << "] expected [" << e_ << "]\n";   \
            ++::echo::test::failureCount();                                          \
        }                                                                            \
    } while (0)
