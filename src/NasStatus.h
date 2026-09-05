#ifndef __NAS_STATUS_H
#define __NAS_STATUS_H

#include <ArduinoJson.h>
#include <ESP8266WiFi.h>
#include <math.h>

extern "C"
{
#include <lwip/dns.h>
#include <lwip/pbuf.h>
#include <lwip/tcp.h>
}

#include "BoundedHttpResponse.h"
#include "ConfigPortal.h"
#include "NasRequestSchedule.h"
#include "NetRate.h"

struct NasStatusSnapshot
{
    double cpuPercent = 0;
    double gpuPercent = 0;
    double memoryPercent = 0;
    double cpuTemperature = -1;
    double diskTemperature = -1;
    uint32_t uptimeSeconds = 0;
    double rxBytesPerSecond = -1;
    double txBytesPerSecond = -1;
    double rxBytes24h = -1;
    double txBytes24h = -1;
    uint32_t trafficCoverageSeconds = 0;
    bool trafficHistoryValid = false;
    double diskReadBytesPerSecond = -1;
    double diskWriteBytesPerSecond = -1;
    bool diskIoValid = false;
    double storageTotalBytes = -1;
    double storageUsedBytes = -1;
    double storagePercent = -1;
    bool storageValid = false;
};

enum class NasRequestError : uint8_t
{
    None,
    InvalidConfig,
    WifiUnavailable,
    Dns,
    Connect,
    Timeout,
    Transport,
    HttpStatus,
    AuthRejected,
    ResponseTooLarge,
    Protocol,
    Json,
};

struct NasRequestHealth
{
    bool authRejected = false;
    NasRequestError lastError = NasRequestError::None;
    uint16_t lastHttpStatus = 0;
    uint32_t lastNetSuccessAt = 0;
    uint32_t lastStatusSuccessAt = 0;
    uint32_t netRequests = 0;
    uint32_t netSuccesses = 0;
    uint32_t netFailures = 0;
    uint32_t netLastLatencyMs = 0;
    uint32_t netMaxLatencyMs = 0;
    uint32_t statusRequests = 0;
    uint32_t statusSuccesses = 0;
    uint32_t statusFailures = 0;
    uint32_t statusLastLatencyMs = 0;
    uint32_t statusMaxLatencyMs = 0;
};

class NasRequestScheduler
{
public:
    void poll(uint16_t byteBudget)
    {
        const uint32_t now = millis();
        if (!hasNasConfig())
        {
            stopForUnavailable(NasRequestError::InvalidConfig, now);
            configured_ = false;
            return;
        }

        const uint32_t hash = configHash();
        if (!configured_ || hash != configHash_)
            useConfig(hash, now);

        if (WiFi.status() != WL_CONNECTED)
        {
            stopForUnavailable(NasRequestError::WifiUnavailable, now);
            wifiWasConnected_ = false;
            return;
        }
        if (!wifiWasConnected_)
        {
            wifiWasConnected_ = true;
            schedule_.makeDue(now);
        }

        if (schedule_.expired(now))
        {
            fail(NasRequestError::Timeout, now);
            return;
        }

        processDns(now);
        if (schedule_.active() == NasRequestKind::None)
        {
            startDueRequest(now);
            return;
        }

        if (tcpError_)
        {
            fail(NasRequestError::Transport, now);
            return;
        }
        if (connectDone_)
        {
            connectDone_ = false;
            if (connectError_ != ERR_OK)
            {
                fail(NasRequestError::Connect, now);
                return;
            }
            phase_ = Phase::Sending;
        }
        if (responseTooLarge_)
        {
            fail(NasRequestError::ResponseTooLarge, now);
            return;
        }

        if (phase_ == Phase::Sending)
            sendSome(byteBudget);
        if (phase_ == Phase::Receiving && byteBudget)
            receiveSome(byteBudget);

        switch (response_.state())
        {
        case HttpResponseState::Complete:
            if (parseResponse())
                succeed(now);
            else
                fail(NasRequestError::Json, now);
            return;
        case HttpResponseState::AuthRejected:
            fail(NasRequestError::AuthRejected, now);
            return;
        case HttpResponseState::HttpError:
            fail(NasRequestError::HttpStatus, now);
            return;
        case HttpResponseState::TooLarge:
            fail(NasRequestError::ResponseTooLarge, now);
            return;
        case HttpResponseState::Malformed:
            fail(NasRequestError::Protocol, now);
            return;
        case HttpResponseState::Reading:
            break;
        }

        if (remoteClosed_ && !received_)
        {
            response_.close();
            fail(NasRequestError::Protocol, now);
        }
    }

    bool takeNet(NasNetSample &sample)
    {
        if (!netReady_)
            return false;
        sample = netSample_;
        netReady_ = false;
        return true;
    }

    bool takeStatus(NasStatusSnapshot &snapshot)
    {
        if (!statusReady_)
            return false;
        const double rxRate = snapshot.rxBytesPerSecond;
        const double txRate = snapshot.txBytesPerSecond;
        snapshot = statusSnapshot_;
        snapshot.rxBytesPerSecond = rxRate;
        snapshot.txBytesPerSecond = txRate;
        statusReady_ = false;
        return true;
    }

    const NasRequestHealth &health() const { return health_; }

    void reset()
    {
        abortConnection();
        clearReceived();
        health_ = NasRequestHealth();
        configured_ = false;
        wifiWasConnected_ = false;
        addressValid_ = false;
        dnsDone_ = false;
        netReady_ = statusReady_ = false;
        phase_ = Phase::Idle;
        clearRequest();
        schedule_.reset(millis());
    }

private:
    enum class Phase : uint8_t
    {
        Idle,
        Resolving,
        Connecting,
        Sending,
        Receiving,
    };

    static const size_t MaxQueuedBytes = 1536;

    static bool safeHeaderValue(const char *value)
    {
        if (!value[0])
            return false;
        for (const unsigned char *cursor = reinterpret_cast<const unsigned char *>(value); *cursor; ++cursor)
            if (*cursor <= 0x20 || *cursor >= 0x7f)
                return false;
        return true;
    }

    uint32_t configHash() const
    {
        uint32_t hash = 2166136261UL;
        const char *values[] = {deviceConfig.nasHost, deviceConfig.nasToken};
        for (const char *value : values)
        {
            while (*value)
            {
                hash ^= static_cast<uint8_t>(*value++);
                hash *= 16777619UL;
            }
            hash ^= 0xff;
            hash *= 16777619UL;
        }
        hash ^= deviceConfig.nasPort & 0xff;
        hash *= 16777619UL;
        hash ^= deviceConfig.nasPort >> 8;
        return hash * 16777619UL;
    }

    void useConfig(uint32_t hash, uint32_t now)
    {
        abortConnection();
        clearReceived();
        schedule_.reset(now);
        phase_ = Phase::Idle;
        configHash_ = hash;
        configured_ = true;
        addressValid_ = false;
        dnsDone_ = false;
        netReady_ = statusReady_ = false;
        clearRequest();
        health_.authRejected = false;
        health_.lastError = NasRequestError::None;
        health_.lastHttpStatus = 0;
    }

    void stopForUnavailable(NasRequestError error, uint32_t now)
    {
        if (schedule_.active() != NasRequestKind::None)
            fail(error, now);
        else
            health_.lastError = error;
    }

    void startDueRequest(uint32_t now)
    {
        if (health_.authRejected && !NasRequestSchedule::reached(now, authRetryAt_))
            return;
        const NasRequestKind kind = schedule_.pick(now);
        if (kind == NasRequestKind::None)
            return;
        if (dnsPending_ && !addressValid_)
        {
            ip_addr_t numericAddress;
            if (!ipaddr_aton(deviceConfig.nasHost, &numericAddress))
                return;
            ip_addr_copy(address_, numericAddress);
            addressValid_ = true;
        }

        schedule_.start(kind, now);
        if (kind == NasRequestKind::Net)
            ++health_.netRequests;
        else
            ++health_.statusRequests;
        response_.reset();
        clearReceived();
        connectDone_ = tcpError_ = remoteClosed_ = responseTooLarge_ = false;
        connectError_ = ERR_OK;

        if (!buildRequest(kind))
        {
            fail(NasRequestError::InvalidConfig, now);
            return;
        }
        if (addressValid_)
        {
            beginConnect(now);
            return;
        }
        if (ipaddr_aton(deviceConfig.nasHost, &address_))
        {
            addressValid_ = true;
            beginConnect(now);
            return;
        }

        phase_ = Phase::Resolving;
        dnsHash_ = configHash_;
        dnsPending_ = true;
        const err_t error = dns_gethostbyname(deviceConfig.nasHost, &dnsAddress_, dnsFound, this);
        if (error == ERR_OK)
        {
            dnsPending_ = false;
            ip_addr_copy(address_, dnsAddress_);
            addressValid_ = true;
            beginConnect(now);
        }
        else if (error != ERR_INPROGRESS)
        {
            dnsPending_ = false;
            fail(NasRequestError::Dns, now);
        }
    }

    bool buildRequest(NasRequestKind kind)
    {
        if (!deviceConfig.nasPort || !safeHeaderValue(deviceConfig.nasHost) ||
            !safeHeaderValue(deviceConfig.nasToken))
            return false;
        const char *path = kind == NasRequestKind::Net ? "/net" : "/status?display=1";
        const int length = snprintf(request_, sizeof(request_),
                                    "GET %s HTTP/1.0\r\nHost: %s\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n",
                                    path, deviceConfig.nasHost, deviceConfig.nasToken);
        if (length < 0 || static_cast<size_t>(length) >= sizeof(request_))
            return false;
        requestLength_ = static_cast<size_t>(length);
        requestOffset_ = 0;
        return true;
    }

    void beginConnect(uint32_t now)
    {
        pcb_ = tcp_new_ip_type(IP_GET_TYPE(&address_));
        if (!pcb_)
        {
            fail(NasRequestError::Connect, now);
            return;
        }
        tcp_setprio(pcb_, TCP_PRIO_MIN);
        tcp_nagle_disable(pcb_);
        tcp_arg(pcb_, this);
        tcp_recv(pcb_, tcpReceived);
        tcp_err(pcb_, tcpFailed);
        phase_ = Phase::Connecting;
        const err_t error = tcp_connect(pcb_, &address_, deviceConfig.nasPort, tcpConnected);
        if (error != ERR_OK)
            fail(NasRequestError::Connect, now);
    }

    void processDns(uint32_t now)
    {
        if (!dnsDone_)
            return;
        dnsDone_ = false;
        const bool matches = dnsHash_ == configHash_;
        if (matches && dnsSucceeded_)
        {
            ip_addr_copy(address_, dnsAddress_);
            addressValid_ = true;
        }
        if (phase_ != Phase::Resolving)
            return;
        if (matches && addressValid_)
            beginConnect(now);
        else
            fail(NasRequestError::Dns, now);
    }

    void sendSome(uint16_t &budget)
    {
        if (!pcb_ || !budget)
            return;
        const size_t remaining = requestLength_ - requestOffset_;
        const size_t available = tcp_sndbuf(pcb_);
        size_t count = remaining < available ? remaining : available;
        if (count > budget)
            count = budget;
        if (!count)
            return;
        const uint8_t flags = TCP_WRITE_FLAG_COPY |
            (count < remaining ? TCP_WRITE_FLAG_MORE : 0);
        const err_t error = tcp_write(pcb_, request_ + requestOffset_, static_cast<u16_t>(count), flags);
        if (error == ERR_MEM)
            return;
        if (error != ERR_OK)
        {
            tcpError_ = true;
            return;
        }
        requestOffset_ += count;
        budget -= static_cast<uint16_t>(count);
        tcp_output(pcb_);
        if (requestOffset_ == requestLength_)
        {
            memset(request_, 0, sizeof(request_));
            requestLength_ = requestOffset_ = 0;
            phase_ = Phase::Receiving;
        }
    }

    void receiveSome(uint16_t &budget)
    {
        while (received_ && budget && response_.state() == HttpResponseState::Reading)
        {
            if (receivedOffset_ == received_->len)
            {
                popReceived();
                continue;
            }
            size_t count = received_->len - receivedOffset_;
            if (count > budget)
                count = budget;
            const char *data = static_cast<const char *>(received_->payload) + receivedOffset_;
            const size_t used = response_.feed(data, count);
            consumeReceived(used);
            budget -= static_cast<uint16_t>(used);
            if (!used)
                break;
        }
    }

    void consumeReceived(size_t count)
    {
        receivedOffset_ += count;
        queuedBytes_ -= count;
        if (pcb_ && count)
            tcp_recved(pcb_, static_cast<u16_t>(count));
        if (received_ && receivedOffset_ == received_->len)
            popReceived();
    }

    void popReceived()
    {
        pbuf *head = received_;
        received_ = received_->next;
        receivedOffset_ = 0;
        if (received_)
            pbuf_ref(received_);
        pbuf_free(head);
    }

    bool parseResponse()
    {
        return schedule_.active() == NasRequestKind::Net ? parseNet() : parseStatus();
    }

    bool parseNet()
    {
        StaticJsonDocument<320> doc;
        if (deserializeJson(doc, response_.body(), response_.bodyLength()))
            return false;
        const char *iface = doc["iface"] | "";
        const char *epoch = "";
        JsonVariant epochValue = doc["counter_epoch"];
        if (doc.containsKey("counter_epoch"))
        {
            if (!epochValue.is<const char *>())
                return false;
            epoch = epochValue.as<const char *>();
            const size_t length = strlen(epoch);
            if (!length || length >= sizeof(netSample_.counterEpoch))
                return false;
            for (size_t index = 0; index < length; ++index)
                if (epoch[index] < '0' || epoch[index] > '9')
                    return false;
        }
        const double sampleTime = doc["sample_time"].as<double>();
        const double rxBytes = doc["rx_bytes"].as<double>();
        const double txBytes = doc["tx_bytes"].as<double>();
        if (!doc["sample_time"].is<double>() || !doc["rx_bytes"].is<double>() ||
            !doc["tx_bytes"].is<double>() || !iface[0] || strlen(iface) >= sizeof(netSample_.iface) ||
            !isfinite(sampleTime) || sampleTime <= 0 ||
            !isfinite(rxBytes) || rxBytes < 0 || !isfinite(txBytes) || txBytes < 0)
            return false;
        NasNetSample sample;
        sample.sampleTime = sampleTime;
        sample.rxBytes = rxBytes;
        sample.txBytes = txBytes;
        strlcpy(sample.iface, iface, sizeof(sample.iface));
        strlcpy(sample.counterEpoch, epoch, sizeof(sample.counterEpoch));
        netSample_ = sample;
        netReady_ = true;
        return true;
    }

    static bool number(JsonVariantConst value, double &result)
    {
        if (!value.is<double>())
            return false;
        result = value.as<double>();
        return isfinite(result);
    }

    static double optionalNumber(JsonVariantConst value)
    {
        double result = -1;
        return number(value, result) ? result : -1;
    }

    bool parseStatus()
    {
        StaticJsonDocument<768> doc;
        if (deserializeJson(doc, response_.body(), response_.bodyLength()))
            return false;
        NasStatusSnapshot snapshot;
        if (!number(doc["cpu"]["percent"], snapshot.cpuPercent) ||
            !number(doc["gpu"]["utilization"], snapshot.gpuPercent) ||
            !number(doc["memory"]["percent"], snapshot.memoryPercent) ||
            snapshot.cpuPercent < 0 || snapshot.cpuPercent > 100 ||
            snapshot.gpuPercent < 0 || snapshot.gpuPercent > 100 ||
            snapshot.memoryPercent < 0 || snapshot.memoryPercent > 100 ||
            !doc["uptime"].is<uint32_t>())
            return false;
        snapshot.uptimeSeconds = doc["uptime"].as<uint32_t>();
        snapshot.cpuTemperature = optionalNumber(doc["temperature_summary"]["cpu"]);
        snapshot.diskTemperature = optionalNumber(doc["temperature_summary"]["disk"]);

        JsonObjectConst history = doc["traffic_24h"];
        snapshot.rxBytes24h = optionalNumber(history["rx_bytes"]);
        snapshot.txBytes24h = optionalNumber(history["tx_bytes"]);
        const uint32_t coverage = history["coverage_seconds"].is<uint32_t>() ?
            history["coverage_seconds"].as<uint32_t>() : 0;
        snapshot.trafficCoverageSeconds = coverage > 86400 ? 86400 : coverage;
        snapshot.trafficHistoryValid = history["valid"].is<bool>() && history["valid"].as<bool>() &&
            snapshot.rxBytes24h >= 0 && snapshot.txBytes24h >= 0;

        JsonObjectConst diskIo = doc["disk_io"];
        snapshot.diskReadBytesPerSecond = optionalNumber(diskIo["read_speed"]);
        snapshot.diskWriteBytesPerSecond = optionalNumber(diskIo["write_speed"]);
        snapshot.diskIoValid = diskIo["valid"].is<bool>() && diskIo["valid"].as<bool>() &&
            snapshot.diskReadBytesPerSecond >= 0 && snapshot.diskWriteBytesPerSecond >= 0;

        JsonObjectConst storage = doc["storage"];
        snapshot.storageTotalBytes = optionalNumber(storage["total"]);
        snapshot.storageUsedBytes = optionalNumber(storage["used"]);
        snapshot.storagePercent = optionalNumber(storage["percent"]);
        snapshot.storageValid = storage["valid"].is<bool>() && storage["valid"].as<bool>() &&
            snapshot.storageTotalBytes >= 0 && snapshot.storageUsedBytes >= 0 &&
            snapshot.storagePercent >= 0 && snapshot.storagePercent <= 100;
        statusSnapshot_ = snapshot;
        statusReady_ = true;
        return true;
    }

    void succeed(uint32_t now)
    {
        finishMetrics(now, true);
        health_.authRejected = false;
        health_.lastError = NasRequestError::None;
        health_.lastHttpStatus = response_.statusCode();
        finishTransport();
    }

    void fail(NasRequestError error, uint32_t now)
    {
        health_.lastError = error;
        health_.lastHttpStatus = response_.statusCode();
        if (error == NasRequestError::AuthRejected)
        {
            health_.authRejected = true;
            authRetryAt_ = now + 30000;
        }
        if (error == NasRequestError::Dns || error == NasRequestError::Connect ||
            error == NasRequestError::Timeout || error == NasRequestError::Transport ||
            error == NasRequestError::WifiUnavailable)
            addressValid_ = false;
        finishMetrics(now, false);
        finishTransport();
    }

    void finishMetrics(uint32_t now, bool success)
    {
        const NasRequestKind kind = schedule_.active();
        const uint32_t latency = schedule_.elapsed(now);
        if (kind == NasRequestKind::Net)
        {
            health_.netLastLatencyMs = latency;
            if (latency > health_.netMaxLatencyMs)
                health_.netMaxLatencyMs = latency;
            if (success)
            {
                ++health_.netSuccesses;
                health_.lastNetSuccessAt = now;
            }
            else
                ++health_.netFailures;
        }
        else if (kind == NasRequestKind::Status)
        {
            health_.statusLastLatencyMs = latency;
            if (latency > health_.statusMaxLatencyMs)
                health_.statusMaxLatencyMs = latency;
            if (success)
            {
                ++health_.statusSuccesses;
                health_.lastStatusSuccessAt = now;
            }
            else
                ++health_.statusFailures;
        }
        schedule_.finish(now, success);
    }

    void finishTransport()
    {
        abortConnection();
        clearReceived();
        clearRequest();
        phase_ = Phase::Idle;
        connectDone_ = tcpError_ = remoteClosed_ = responseTooLarge_ = false;
    }

    void abortConnection()
    {
        if (!pcb_)
            return;
        tcp_arg(pcb_, nullptr);
        tcp_recv(pcb_, nullptr);
        tcp_err(pcb_, nullptr);
        tcp_abort(pcb_);
        pcb_ = nullptr;
    }

    void clearReceived()
    {
        if (received_)
            pbuf_free(received_);
        received_ = nullptr;
        receivedOffset_ = queuedBytes_ = 0;
    }

    void clearRequest()
    {
        memset(request_, 0, sizeof(request_));
        requestLength_ = requestOffset_ = 0;
    }

    static void dnsFound(const char *, const ip_addr_t *address, void *argument)
    {
        NasRequestScheduler *self = static_cast<NasRequestScheduler *>(argument);
        self->dnsPending_ = false;
        self->dnsSucceeded_ = address != nullptr;
        if (address)
            ip_addr_copy(self->dnsAddress_, *address);
        self->dnsDone_ = true;
    }

    static err_t tcpConnected(void *argument, tcp_pcb *pcb, err_t error)
    {
        NasRequestScheduler *self = static_cast<NasRequestScheduler *>(argument);
        if (self->pcb_ == pcb)
        {
            self->connectError_ = error;
            self->connectDone_ = true;
        }
        return ERR_OK;
    }

    static void tcpFailed(void *argument, err_t error)
    {
        NasRequestScheduler *self = static_cast<NasRequestScheduler *>(argument);
        self->pcb_ = nullptr;
        (void)error;
        self->tcpError_ = true;
    }

    static err_t tcpReceived(void *argument, tcp_pcb *pcb, pbuf *buffer, err_t error)
    {
        NasRequestScheduler *self = static_cast<NasRequestScheduler *>(argument);
        if (!buffer)
        {
            self->remoteClosed_ = true;
            return ERR_OK;
        }
        const size_t incoming = buffer->tot_len;
        if (error != ERR_OK || self->queuedBytes_ + incoming > MaxQueuedBytes)
        {
            tcp_recved(pcb, static_cast<u16_t>(incoming));
            pbuf_free(buffer);
            if (error != ERR_OK)
            {
                self->tcpError_ = true;
            }
            else
                self->responseTooLarge_ = true;
            return ERR_OK;
        }
        if (self->received_)
            pbuf_cat(self->received_, buffer);
        else
            self->received_ = buffer;
        self->queuedBytes_ += incoming;
        return ERR_OK;
    }

    NasRequestSchedule schedule_;
    NasRequestHealth health_;
    BoundedHttpResponse<1024, 128, 512> response_;
    NasNetSample netSample_;
    NasStatusSnapshot statusSnapshot_;
    tcp_pcb *pcb_ = nullptr;
    pbuf *received_ = nullptr;
    ip_addr_t address_;
    ip_addr_t dnsAddress_;
    size_t receivedOffset_ = 0;
    size_t queuedBytes_ = 0;
    size_t requestLength_ = 0;
    size_t requestOffset_ = 0;
    uint32_t configHash_ = 0;
    uint32_t dnsHash_ = 0;
    uint32_t authRetryAt_ = 0;
    err_t connectError_ = ERR_OK;
    Phase phase_ = Phase::Idle;
    bool configured_ = false;
    bool wifiWasConnected_ = false;
    bool addressValid_ = false;
    bool dnsPending_ = false;
    bool dnsDone_ = false;
    bool dnsSucceeded_ = false;
    bool connectDone_ = false;
    bool tcpError_ = false;
    bool remoteClosed_ = false;
    bool responseTooLarge_ = false;
    bool netReady_ = false;
    bool statusReady_ = false;
    char request_[320] = {0};
};

inline NasRequestScheduler &nasRequestScheduler()
{
    static NasRequestScheduler scheduler;
    return scheduler;
}

inline void pollNasRequests(uint16_t byteBudget = 256)
{
    nasRequestScheduler().poll(byteBudget);
}

inline bool takeNasNetSample(NasNetSample &sample)
{
    return nasRequestScheduler().takeNet(sample);
}

inline bool takeNasStatus(NasStatusSnapshot &snapshot)
{
    return nasRequestScheduler().takeStatus(snapshot);
}

inline const NasRequestHealth &getNasRequestHealth()
{
    return nasRequestScheduler().health();
}

inline void resetNasRequests()
{
    nasRequestScheduler().reset();
}

#endif
