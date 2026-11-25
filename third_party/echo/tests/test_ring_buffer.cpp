// Unit tests for SpscRingBuffer (src/core/ring_buffer.hpp).
#include "core/ring_buffer.hpp"

#include <numeric>
#include <thread>
#include <vector>

#include "test_framework.hpp"

using echo::SpscRingBuffer;

TEST_CASE(starts_empty) {
    SpscRingBuffer<float> rb(8);
    CHECK_EQ(rb.size(), 0u);
    CHECK_EQ(rb.capacity(), 8u);
    float out[4];
    CHECK_EQ(rb.pop(out, 4), 0u);
}

TEST_CASE(push_then_pop_preserves_order) {
    SpscRingBuffer<int> rb(8);
    const int in[5] = {1, 2, 3, 4, 5};
    CHECK_EQ(rb.push(in, 5), 5u);
    CHECK_EQ(rb.size(), 5u);

    int out[5] = {};
    CHECK_EQ(rb.pop(out, 5), 5u);
    for (int i = 0; i < 5; ++i) CHECK_EQ(out[i], in[i]);
}

TEST_CASE(push_stops_when_full) {
    SpscRingBuffer<int> rb(4);
    const int in[6] = {1, 2, 3, 4, 5, 6};
    CHECK_EQ(rb.push(in, 6), 4u);  // only 4 fit
    CHECK_EQ(rb.push(in, 1), 0u);  // nothing fits now
}

TEST_CASE(wraps_around_the_end) {
    SpscRingBuffer<int> rb(5);
    int out[5];
    const int a[4] = {1, 2, 3, 4};
    rb.push(a, 4);
    rb.pop(out, 3);  // tail is now near the end of the storage array

    const int b[4] = {5, 6, 7, 8};
    CHECK_EQ(rb.push(b, 4), 4u);  // this write has to wrap around
    CHECK_EQ(rb.pop(out, 5), 5u);
    const int expected[5] = {4, 5, 6, 7, 8};
    for (int i = 0; i < 5; ++i) CHECK_EQ(out[i], expected[i]);
}

TEST_CASE(clear_discards_data) {
    SpscRingBuffer<int> rb(4);
    const int in[3] = {1, 2, 3};
    rb.push(in, 3);
    rb.clear();
    CHECK_EQ(rb.size(), 0u);
}

// One thread writes 1..N while another reads; the reader must see every value
// exactly once and in order. This is the real-world usage pattern.
TEST_CASE(concurrent_producer_consumer) {
    constexpr int kTotal = 200000;
    SpscRingBuffer<int> rb(1024);

    std::thread producer([&] {
        int next = 0;
        while (next < kTotal) {
            int chunk[64];
            const int n = std::min(64, kTotal - next);
            std::iota(chunk, chunk + n, next);
            next += static_cast<int>(rb.push(chunk, static_cast<std::size_t>(n)));
        }
    });

    int expected = 0;
    bool in_order = true;
    while (expected < kTotal) {
        int chunk[100];
        const std::size_t n = rb.pop(chunk, 100);
        for (std::size_t i = 0; i < n; ++i) in_order &= (chunk[i] == expected++);
    }
    producer.join();
    CHECK(in_order);
}

int main() { return echo::test::runAll(); }
