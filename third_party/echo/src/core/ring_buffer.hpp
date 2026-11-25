// =============================================================================
//  ring_buffer.hpp - a lock-free single-producer / single-consumer ring buffer
// =============================================================================
//
//  WHY THIS EXISTS
//  ---------------
//  The microphone callback runs on a real-time audio thread owned by the
//  operating system. If that thread waits (on a mutex, memory allocation,
//  file I/O, ...) the OS may drop audio and you hear/see glitches. So the
//  callback must hand samples to the rest of the program WITHOUT EVER BLOCKING.
//
//  A ring buffer ("circular buffer") with exactly one writer and one reader can
//  be made safe using only two atomic counters - no locks:
//
//      buffer:  [ . . . x x x x x . . . ]
//                      ^tail     ^head
//                      (reader)  (writer)
//
//   * The producer (audio callback) writes at `head` then advances it.
//   * The consumer (VAD thread) reads at `tail` then advances it.
//   * Each side only ever WRITES its own index, and READS the other's, so
//     there is no data race as long as the index updates use the right
//     memory ordering (release on publish, acquire on observe).
//
//  One slot is always kept empty so that "head == tail" unambiguously means
//  "empty" (otherwise a full buffer would look the same as an empty one).
// =============================================================================
#pragma once

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <vector>

namespace echo {

template <typename T>
class SpscRingBuffer {
public:
    /// @param capacity  how many elements the buffer can hold at once.
    explicit SpscRingBuffer(std::size_t capacity) : storage_(capacity + 1) {}

    // Copying a buffer that another thread may be using makes no sense.
    SpscRingBuffer(const SpscRingBuffer&) = delete;
    SpscRingBuffer& operator=(const SpscRingBuffer&) = delete;

    /// PRODUCER ONLY. Copies up to `count` elements in. Never blocks.
    /// @return how many elements were actually written (less than `count` when
    ///         the buffer is full - the caller decides what to do with the rest).
    std::size_t push(const T* data, std::size_t count) {
        const std::size_t head = head_.load(std::memory_order_relaxed);  // we own head
        const std::size_t tail = tail_.load(std::memory_order_acquire);  // see reader's progress
        const std::size_t n = std::min(count, freeSpace(head, tail));

        // The free region may wrap around the end of the array, so copy in at
        // most two pieces: [head, end) and then [0, ...).
        const std::size_t first = std::min(n, storage_.size() - head);
        std::copy(data, data + first, storage_.begin() + static_cast<std::ptrdiff_t>(head));
        std::copy(data + first, data + n, storage_.begin());

        // `release` makes the copied samples visible before the new head is.
        head_.store((head + n) % storage_.size(), std::memory_order_release);
        return n;
    }

    /// CONSUMER ONLY. Copies up to `max_count` elements out. Never blocks.
    /// @return how many elements were read (0 when the buffer is empty).
    std::size_t pop(T* out, std::size_t max_count) {
        const std::size_t tail = tail_.load(std::memory_order_relaxed);  // we own tail
        const std::size_t head = head_.load(std::memory_order_acquire);  // see writer's data
        const std::size_t n = std::min(max_count, usedSpace(head, tail));

        const std::size_t first = std::min(n, storage_.size() - tail);
        const auto begin = storage_.begin() + static_cast<std::ptrdiff_t>(tail);
        std::copy(begin, begin + static_cast<std::ptrdiff_t>(first), out);
        std::copy(storage_.begin(), storage_.begin() + static_cast<std::ptrdiff_t>(n - first),
                  out + first);

        tail_.store((tail + n) % storage_.size(), std::memory_order_release);
        return n;
    }

    /// CONSUMER ONLY. Throws away everything currently buffered.
    void clear() { tail_.store(head_.load(std::memory_order_acquire), std::memory_order_release); }

    /// Number of elements waiting to be read (a snapshot; may change at once).
    std::size_t size() const {
        return usedSpace(head_.load(std::memory_order_acquire),
                         tail_.load(std::memory_order_acquire));
    }

    std::size_t capacity() const { return storage_.size() - 1; }

private:
    std::size_t usedSpace(std::size_t head, std::size_t tail) const {
        return (head + storage_.size() - tail) % storage_.size();
    }
    std::size_t freeSpace(std::size_t head, std::size_t tail) const {
        return capacity() - usedSpace(head, tail);
    }

    std::vector<T> storage_;

    // `alignas(64)` puts each index on its own CPU cache line. Without it the
    // two threads would keep stealing the same cache line from each other
    // ("false sharing"), which is slow even though they touch different data.
    alignas(64) std::atomic<std::size_t> head_{0};  // next slot to write
    alignas(64) std::atomic<std::size_t> tail_{0};  // next slot to read
};

}  // namespace echo
