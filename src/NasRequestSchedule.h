#ifndef ROUTER_MONITOR_NAS_REQUEST_SCHEDULE_H
#define ROUTER_MONITOR_NAS_REQUEST_SCHEDULE_H

#include <stdint.h>

enum class NasRequestKind : uint8_t
{
    None,
    Net,
    Status,
};

class NasRequestSchedule
{
public:
    enum : uint32_t
    {
        NetIntervalMs = 200,
        StatusIntervalMs = 1000,
        NetDeadlineMs = 400,
        StatusDeadlineMs = 750,
        NetMaxBackoffMs = 5000,
        StatusMaxBackoffMs = 30000,
    };

    void reset(uint32_t now)
    {
        active_ = NasRequestKind::None;
        netDueAt_ = statusDueAt_ = now;
        startedAt_ = deadlineAt_ = now;
        netFailureStreak_ = statusFailureStreak_ = 0;
    }

    NasRequestKind pick(uint32_t now) const
    {
        if (active_ != NasRequestKind::None)
            return NasRequestKind::None;
        if (reached(now, netDueAt_))
            return NasRequestKind::Net;
        return reached(now, statusDueAt_) ? NasRequestKind::Status : NasRequestKind::None;
    }

    void start(NasRequestKind kind, uint32_t now)
    {
        active_ = kind;
        startedAt_ = now;
        deadlineAt_ = now + (kind == NasRequestKind::Net ? NetDeadlineMs : StatusDeadlineMs);
    }

    bool expired(uint32_t now) const
    {
        return active_ != NasRequestKind::None && reached(now, deadlineAt_);
    }

    uint32_t elapsed(uint32_t now) const { return now - startedAt_; }
    NasRequestKind active() const { return active_; }

    void finish(uint32_t now, bool success)
    {
        if (active_ == NasRequestKind::None)
            return;
        uint8_t &streak = active_ == NasRequestKind::Net ? netFailureStreak_ : statusFailureStreak_;
        uint32_t &dueAt = active_ == NasRequestKind::Net ? netDueAt_ : statusDueAt_;
        const uint32_t interval = active_ == NasRequestKind::Net ? NetIntervalMs : StatusIntervalMs;
        const uint32_t maximum = active_ == NasRequestKind::Net ? NetMaxBackoffMs : StatusMaxBackoffMs;
        if (success)
        {
            streak = 0;
            dueAt = startedAt_ + interval;
            if (reached(now, dueAt))
                dueAt = now;
        }
        else
        {
            if (streak < 15)
                ++streak;
            uint32_t delay = interval;
            for (uint8_t count = 0; count < streak && delay < maximum; ++count)
                delay = delay > maximum / 2 ? maximum : delay * 2;
            dueAt = now + delay;
        }
        active_ = NasRequestKind::None;
    }

    void cancel() { active_ = NasRequestKind::None; }
    void makeDue(uint32_t now) { netDueAt_ = statusDueAt_ = now; }

    static bool reached(uint32_t now, uint32_t deadline)
    {
        return static_cast<int32_t>(now - deadline) >= 0;
    }

private:
    NasRequestKind active_ = NasRequestKind::None;
    uint32_t netDueAt_ = 0;
    uint32_t statusDueAt_ = 0;
    uint32_t startedAt_ = 0;
    uint32_t deadlineAt_ = 0;
    uint8_t netFailureStreak_ = 0;
    uint8_t statusFailureStreak_ = 0;
};

#endif
