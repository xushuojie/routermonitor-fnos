#ifndef ROUTER_MONITOR_BOUNDED_HTTP_RESPONSE_H
#define ROUTER_MONITOR_BOUNDED_HTTP_RESPONSE_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum class HttpResponseState : uint8_t
{
    Reading,
    Complete,
    AuthRejected,
    HttpError,
    Malformed,
    TooLarge,
};

template <size_t MaxBody, size_t MaxLine = 192, size_t MaxHeader = 512>
class BoundedHttpResponse
{
public:
    void reset()
    {
        state_ = HttpResponseState::Reading;
        statusCode_ = 0;
        headerBytes_ = 0;
        lineLength_ = 0;
        bodyLength_ = 0;
        expectedBodyLength_ = 0;
        firstLine_ = true;
        headersComplete_ = false;
        contentLengthSeen_ = false;
        transferEncodingSeen_ = false;
        http11_ = connectionClose_ = false;
        body_[0] = '\0';
    }

    size_t feed(const char *data, size_t size)
    {
        size_t used = 0;
        while (used < size && state_ == HttpResponseState::Reading)
        {
            const char value = data[used++];
            if (!headersComplete_)
            {
                if (++headerBytes_ > MaxHeader || value == '\0')
                {
                    state_ = HttpResponseState::TooLarge;
                    break;
                }
                if (value == '\n')
                {
                    if (lineLength_ && line_[lineLength_ - 1] == '\r')
                        --lineLength_;
                    line_[lineLength_] = '\0';
                    processLine();
                    lineLength_ = 0;
                }
                else if (lineLength_ + 1 < MaxLine)
                {
                    line_[lineLength_++] = value;
                }
                else
                {
                    state_ = HttpResponseState::TooLarge;
                }
                continue;
            }

            if (bodyLength_ >= expectedBodyLength_ || bodyLength_ >= MaxBody)
            {
                state_ = HttpResponseState::TooLarge;
                break;
            }
            body_[bodyLength_++] = value;
            if (bodyLength_ == expectedBodyLength_)
            {
                body_[bodyLength_] = '\0';
                state_ = HttpResponseState::Complete;
            }
        }
        return used;
    }

    void close()
    {
        if (state_ == HttpResponseState::Reading)
            state_ = HttpResponseState::Malformed;
    }

    bool reusable() const { return state_ == HttpResponseState::Complete && http11_ && !connectionClose_; }
    HttpResponseState state() const { return state_; }
    uint16_t statusCode() const { return statusCode_; }
    char *body() { return body_; }
    const char *body() const { return body_; }
    size_t bodyLength() const { return bodyLength_; }

private:
    static bool equalIgnoreCase(const char *left, size_t leftLength, const char *right)
    {
        if (strlen(right) != leftLength)
            return false;
        for (size_t index = 0; index < leftLength; ++index)
        {
            char a = left[index];
            char b = right[index];
            if (a >= 'A' && a <= 'Z')
                a += 'a' - 'A';
            if (b >= 'A' && b <= 'Z')
                b += 'a' - 'A';
            if (a != b)
                return false;
        }
        return true;
    }

    static const char *skipSpaces(const char *value)
    {
        while (*value == ' ' || *value == '\t')
            ++value;
        return value;
    }

    bool parseLength(const char *value, size_t &parsed, bool &tooLarge) const
    {
        tooLarge = false;
        value = skipSpaces(value);
        if (*value < '0' || *value > '9')
            return false;
        size_t result = 0;
        do
        {
            const unsigned digit = static_cast<unsigned>(*value++ - '0');
            if (result > MaxBody / 10 ||
                (result == MaxBody / 10 && digit > MaxBody % 10))
            {
                tooLarge = true;
                return false;
            }
            result = result * 10 + digit;
        } while (*value >= '0' && *value <= '9');
        value = skipSpaces(value);
        if (*value != '\0')
            return false;
        parsed = result;
        return true;
    }

    void processLine()
    {
        if (firstLine_)
        {
            firstLine_ = false;
            const char *space = strchr(line_, ' ');
            if (strncmp(line_, "HTTP/1.", 7) != 0 || !space || space != line_ + 8 ||
                (line_[7] != '0' && line_[7] != '1') ||
                static_cast<size_t>(line_ + lineLength_ - space) < 4 ||
                space[1] < '1' || space[1] > '5' ||
                space[2] < '0' || space[2] > '9' ||
                space[3] < '0' || space[3] > '9' ||
                (space[4] != ' ' && space[4] != '\0'))
            {
                state_ = HttpResponseState::Malformed;
                return;
            }
            http11_ = line_[7] == '1';
            statusCode_ = static_cast<uint16_t>((space[1] - '0') * 100 +
                                                (space[2] - '0') * 10 + space[3] - '0');
            return;
        }

        if (!lineLength_)
        {
            if (statusCode_ == 401 || statusCode_ == 403)
                state_ = HttpResponseState::AuthRejected;
            else if (statusCode_ != 200)
                state_ = HttpResponseState::HttpError;
            else if (!contentLengthSeen_ || transferEncodingSeen_)
                state_ = HttpResponseState::Malformed;
            else
            {
                headersComplete_ = true;
                if (!expectedBodyLength_)
                    state_ = HttpResponseState::Complete;
            }
            return;
        }

        char *colon = strchr(line_, ':');
        if (!colon)
        {
            state_ = HttpResponseState::Malformed;
            return;
        }
        const size_t nameLength = static_cast<size_t>(colon - line_);
        const char *value = colon + 1;
        if (equalIgnoreCase(line_, nameLength, "content-length"))
        {
            size_t parsed = 0;
            bool tooLarge = false;
            if (!parseLength(value, parsed, tooLarge) || (contentLengthSeen_ && parsed != expectedBodyLength_))
            {
                state_ = tooLarge ? HttpResponseState::TooLarge : HttpResponseState::Malformed;
                return;
            }
            expectedBodyLength_ = parsed;
            contentLengthSeen_ = true;
        }
        else if (equalIgnoreCase(line_, nameLength, "connection"))
        {
            while (*value)
            {
                value = skipSpaces(value);
                const char *end = strchr(value, ',');
                if (!end) end = value + strlen(value);
                const char *trim = end;
                while (trim > value && (trim[-1] == ' ' || trim[-1] == '\t')) --trim;
                if (equalIgnoreCase(value, trim - value, "close")) connectionClose_ = true;
                value = *end ? end + 1 : end;
            }
        }
        else if (equalIgnoreCase(line_, nameLength, "transfer-encoding"))
        {
            transferEncodingSeen_ = true;
        }
    }

    HttpResponseState state_ = HttpResponseState::Reading;
    uint16_t statusCode_ = 0;
    size_t headerBytes_ = 0;
    size_t lineLength_ = 0;
    size_t bodyLength_ = 0;
    size_t expectedBodyLength_ = 0;
    bool firstLine_ = true;
    bool headersComplete_ = false;
    bool contentLengthSeen_ = false;
    bool http11_ = false;
    bool connectionClose_ = false;
    bool transferEncodingSeen_ = false;
    char line_[MaxLine] = {0};
    char body_[MaxBody + 1] = {0};
};

#endif
