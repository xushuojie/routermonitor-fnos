#ifndef ROUTER_MONITOR_TEST_MOCK_PLATFORM_H
#define ROUTER_MONITOR_TEST_MOCK_PLATFORM_H

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <string>
#include <utility>

#define PROGMEM
#define F(value) value

template <typename T>
constexpr T min(T left, T right)
{
    return left < right ? left : right;
}

inline size_t strlcpy(char *target, const char *source, size_t size)
{
    const size_t length = std::strlen(source);
    if (size)
    {
        const size_t copied = std::min(length, size - 1);
        std::memcpy(target, source, copied);
        target[copied] = '\0';
    }
    return length;
}

class String
{
  public:
    String() = default;
    String(const char *value) : value_(value ? value : "") {}
    String(const std::string &value) : value_(value) {}

    size_t length() const { return value_.size(); }
    const char *c_str() const { return value_.c_str(); }
    void toCharArray(char *target, size_t size) const { strlcpy(target, value_.c_str(), size); }

    bool equalsConstantTime(const char *other) const
    {
        if (!other)
            return false;
        const size_t otherLength = std::strlen(other);
        size_t difference = value_.size() ^ otherLength;
        const size_t maximum = std::max(value_.size(), otherLength);
        for (size_t index = 0; index < maximum; ++index)
        {
            const uint8_t left = index < value_.size() ? value_[index] : 0;
            const uint8_t right = index < otherLength ? other[index] : 0;
            difference |= left ^ right;
        }
        return difference == 0;
    }

    String &operator+=(const char *value)
    {
        value_ += value ? value : "";
        return *this;
    }

    String &operator+=(const String &value)
    {
        value_ += value.value_;
        return *this;
    }

  private:
    std::string value_;
};

class IPAddress
{
  public:
    explicit IPAddress(uint32_t value = 0) : value_(value) {}
    bool operator==(const IPAddress &other) const { return value_ == other.value_; }
    bool operator!=(const IPAddress &other) const { return !(*this == other); }

  private:
    uint32_t value_;
};

struct SerialMock
{
    template <typename T>
    void print(const T &) {}
    template <typename T>
    void println(const T &) {}
    void println() {}
};

inline SerialMock Serial;
inline uint32_t mockMillis = 0;
inline uint32_t millis() { return mockMillis; }
inline void yield() {}

struct ESPMock
{
    bool erasedFlash = true;
    bool restarted = false;
    uint8_t nextRandom = 1;

    void random(uint8_t *target, size_t length)
    {
        for (size_t index = 0; index < length; ++index)
            target[index] = nextRandom++;
    }

    bool flashRead(uint32_t, uint32_t *target, size_t length)
    {
        std::memset(target, erasedFlash ? 0xff : 0, length);
        return true;
    }

    uint32_t getChipId() const { return 0x123456; }
    void restart() { restarted = true; }
};

inline ESPMock ESP;

enum
{
    WIFI_STA = 1,
    WIFI_AP_STA = 3,
    WL_DISCONNECTED = 6,
    WL_CONNECTED = 3,
};

struct WiFiMock
{
    int connectionStatus = WL_DISCONNECTED;
    bool apStarted = false;
    bool softApResult = true;
    IPAddress apAddress = IPAddress(0xc0a80401);
    int lastMode = WIFI_STA;

    int status() const { return connectionStatus; }
    bool mode(int value)
    {
        lastMode = value;
        return true;
    }
    bool softAP(const char *, const char *)
    {
        apStarted = softApResult;
        return softApResult;
    }
    IPAddress softAPIP() const { return apAddress; }
    bool softAPdisconnect(bool)
    {
        apStarted = false;
        return true;
    }
};

inline WiFiMock WiFi;

struct MockFileSystemState
{
    std::map<std::string, std::string> files;
    bool configAccepted = true;
    bool beginResult = true;
    bool formatResult = true;
    size_t formatCalls = 0;
    size_t writeOpenCalls = 0;
    size_t renameCalls = 0;
    size_t maximumWrite = std::numeric_limits<size_t>::max();
    std::string failRenameFrom;
    size_t failRenameCount = 0;

    void reset()
    {
        *this = MockFileSystemState();
    }
};

inline MockFileSystemState mockFileSystem;

class File
{
  public:
    File() = default;
    File(std::string path, bool writable, bool open)
        : path_(std::move(path)), writable_(writable), open_(open) {}

    explicit operator bool() const { return open_; }
    size_t size() const
    {
        const auto found = mockFileSystem.files.find(path_);
        return found == mockFileSystem.files.end() ? 0 : found->second.size();
    }
    size_t write(const uint8_t *data, size_t length)
    {
        if (!open_ || !writable_)
            return 0;
        const size_t allowed = written_ >= mockFileSystem.maximumWrite
                                   ? 0
                                   : std::min(length, mockFileSystem.maximumWrite - written_);
        mockFileSystem.files[path_].append(reinterpret_cast<const char *>(data), allowed);
        written_ += allowed;
        return allowed;
    }
    size_t write(uint8_t data)
    {
        return write(&data, 1);
    }
    int read()
    {
        if (!open_ || writable_)
            return -1;
        const auto found = mockFileSystem.files.find(path_);
        if (found == mockFileSystem.files.end() || readOffset_ >= found->second.size())
            return -1;
        return static_cast<uint8_t>(found->second[readOffset_++]);
    }
    size_t readBytes(char *target, size_t length)
    {
        if (!open_ || writable_)
            return 0;
        const auto found = mockFileSystem.files.find(path_);
        if (found == mockFileSystem.files.end())
            return 0;
        const size_t available = found->second.size() - std::min(readOffset_, found->second.size());
        const size_t copied = std::min(length, available);
        std::memcpy(target, found->second.data() + readOffset_, copied);
        readOffset_ += copied;
        return copied;
    }
    void flush() {}
    void close() { open_ = false; }

  private:
    std::string path_;
    bool writable_ = false;
    bool open_ = false;
    size_t written_ = 0;
    size_t readOffset_ = 0;
};

class LittleFSConfig
{
  public:
    explicit LittleFSConfig(bool autoFormat = true) : autoFormat_(autoFormat) {}
    bool autoFormat() const { return autoFormat_; }

  private:
    bool autoFormat_;
};

struct LittleFSMock
{
    bool setConfig(const LittleFSConfig &config)
    {
        lastAutoFormat = config.autoFormat();
        return mockFileSystem.configAccepted;
    }
    bool begin() { return mockFileSystem.beginResult; }
    bool format()
    {
        ++mockFileSystem.formatCalls;
        if (!mockFileSystem.formatResult)
            return false;
        mockFileSystem.files.clear();
        mockFileSystem.beginResult = true;
        return true;
    }
    File open(const char *path, const char *mode)
    {
        const bool writable = mode && mode[0] == 'w';
        if (writable)
        {
            ++mockFileSystem.writeOpenCalls;
            mockFileSystem.files[path].clear();
            return File(path, true, true);
        }
        return File(path, false, mockFileSystem.files.count(path) != 0);
    }
    bool exists(const char *path) const { return mockFileSystem.files.count(path) != 0; }
    bool rename(const char *from, const char *to)
    {
        ++mockFileSystem.renameCalls;
        if (mockFileSystem.failRenameCount && mockFileSystem.failRenameFrom == from)
        {
            --mockFileSystem.failRenameCount;
            return false;
        }
        const auto found = mockFileSystem.files.find(from);
        if (found == mockFileSystem.files.end())
            return false;
        mockFileSystem.files[to] = found->second;
        mockFileSystem.files.erase(found);
        return true;
    }

    bool lastAutoFormat = true;
};

inline LittleFSMock LittleFS;

struct DNSServer
{
    bool active = false;
    size_t stopCalls = 0;
    bool start(uint16_t, const char *, IPAddress)
    {
        active = true;
        return true;
    }
    void processNextRequest() {}
    void stop()
    {
        active = false;
        ++stopCalls;
    }
};

enum HTTPMethod
{
    HTTP_GET,
    HTTP_POST,
};

enum HTTPAuthMethod
{
    BASIC_AUTH,
    DIGEST_AUTH,
};

class WiFiClient
{
  public:
    IPAddress localIP() const { return localAddress; }
    IPAddress localAddress = IPAddress(0x0a000001);
};

class ESP8266WebServer
{
  public:
    using Handler = std::function<void()>;

    explicit ESP8266WebServer(uint16_t) {}
    WiFiClient &client() { return client_; }
    String arg(const char *name) const
    {
        const auto found = arguments.find(name);
        return found == arguments.end() ? String() : String(found->second);
    }
    bool authenticate(const char *, const char *) { return authenticationAllowed; }
    void requestAuthentication(HTTPAuthMethod, const char *, const String & = String()) { responseCode = 401; }
    void send(int code, const char *, const char *) { responseCode = code; }
    void send(int code, const char *, const String &) { responseCode = code; }
    void send_P(int code, const char *, const char *) { responseCode = code; }
    void sendHeader(const char *, const char *) {}
    void sendHeader(const char *, const String &) {}
    void on(const char *, HTTPMethod, Handler) {}
    void onNotFound(Handler) {}
    void collectHeaders(const char *) {}
    void begin() { began = true; }
    void handleClient() {}

    void reset()
    {
        arguments.clear();
        authenticationAllowed = false;
        responseCode = 0;
        began = false;
        client_.localAddress = IPAddress(0x0a000001);
    }

    std::map<std::string, std::string> arguments;
    bool authenticationAllowed = false;
    int responseCode = 0;
    bool began = false;

  private:
    WiFiClient client_;
};

#endif
