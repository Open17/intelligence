// =============================================================================
//  blocking_queue.hpp - a thread-safe FIFO queue for passing work between threads
// =============================================================================
//
//  Unlike the lock-free ring buffer (used only by the real-time audio
//  callback), this queue uses a mutex + condition variable. That is fine for
//  our worker threads, which are allowed to wait.
//
//    producer:  queue.push(job);
//    consumer:  while (queue.pop(job)) { ... }   // sleeps while empty
//    shutdown:  queue.close();                   // wakes the consumer; pop() -> false
// =============================================================================
#pragma once

#include <condition_variable>
#include <deque>
#include <mutex>

namespace echo {

template <typename T>
class BlockingQueue {
public:
    /// Adds an item and wakes one waiting consumer. Ignored after close().
    void push(T item) {
        {
            // lock_guard locks the mutex here and unlocks it automatically at
            // the closing brace (RAII - "Resource Acquisition Is Initialization").
            std::lock_guard<std::mutex> lock(mutex_);
            if (closed_) return;
            items_.push_back(std::move(item));
        }
        // Notify AFTER unlocking so the woken thread does not immediately
        // block on the mutex we still hold.
        ready_.notify_one();
    }

    /// Waits for an item. Returns false (without an item) once the queue is closed.
    bool pop(T& out) {
        std::unique_lock<std::mutex> lock(mutex_);
        // wait() releases the mutex while sleeping and re-locks it on wake-up.
        // The predicate protects against "spurious" wake-ups.
        ready_.wait(lock, [this] { return closed_ || !items_.empty(); });
        if (closed_) return false;
        out = std::move(items_.front());
        items_.pop_front();
        return true;
    }

    /// Takes an item if one is ready; never waits.
    bool tryPop(T& out) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (closed_ || items_.empty()) return false;
        out = std::move(items_.front());
        items_.pop_front();
        return true;
    }

    /// Wakes every waiting consumer; pending items are discarded.
    void close() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            closed_ = true;
            items_.clear();
        }
        ready_.notify_all();
    }

    /// Makes a closed queue usable again (empty).
    void reopen() {
        std::lock_guard<std::mutex> lock(mutex_);
        items_.clear();
        closed_ = false;
    }

    std::size_t size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return items_.size();
    }

private:
    mutable std::mutex mutex_;  // `mutable` so size() can lock it despite being const
    std::condition_variable ready_;
    std::deque<T> items_;
    bool closed_ = false;
};

}  // namespace echo
