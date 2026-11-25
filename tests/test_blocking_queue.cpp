// Unit tests for BlockingQueue (src/core/blocking_queue.hpp).
#include "core/blocking_queue.hpp"

#include <atomic>
#include <chrono>
#include <thread>

#include "test_framework.hpp"

using echo::BlockingQueue;

TEST_CASE(fifo_order) {
    BlockingQueue<int> q;
    q.push(1);
    q.push(2);
    int v = 0;
    CHECK(q.pop(v));
    CHECK_EQ(v, 1);
    CHECK(q.tryPop(v));
    CHECK_EQ(v, 2);
    CHECK(!q.tryPop(v));
}

TEST_CASE(pop_waits_for_producer) {
    BlockingQueue<int> q;
    std::thread producer([&] {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        q.push(42);
    });
    int v = 0;
    CHECK(q.pop(v));  // blocks until the producer pushes
    CHECK_EQ(v, 42);
    producer.join();
}

TEST_CASE(close_wakes_waiting_consumer) {
    BlockingQueue<int> q;
    std::atomic<bool> returned{false};
    std::thread consumer([&] {
        int v;
        returned = !q.pop(v);  // must return false once closed
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    q.close();
    consumer.join();
    CHECK(returned.load());
}

TEST_CASE(reopen_after_close) {
    BlockingQueue<int> q;
    q.close();
    q.push(1);  // ignored while closed
    q.reopen();
    CHECK_EQ(q.size(), 0u);
    q.push(2);
    int v = 0;
    CHECK(q.pop(v));
    CHECK_EQ(v, 2);
}

int main() { return echo::test::runAll(); }
