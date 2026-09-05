#!/usr/bin/env python3
"""Compile and run bounded HTTP and request timing checks on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    compiler = shutil.which("c++")
    if not compiler:
        raise SystemExit("A host C++ compiler is required.")
    source = r'''
#include "BoundedHttpResponse.h"
#include "NasRequestSchedule.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>

template <size_t Body, size_t Line = 192, size_t Header = 512>
static void feed(BoundedHttpResponse<Body, Line, Header> &parser,
                 const std::string &response, size_t chunk = 3) {
    size_t offset = 0;
    while (offset < response.size() && parser.state() == HttpResponseState::Reading) {
        const size_t count = std::min(chunk, response.size() - offset);
        const size_t used = parser.feed(response.data() + offset, count);
        assert(used || parser.state() != HttpResponseState::Reading);
        offset += used;
    }
}

int main() {
    BoundedHttpResponse<16> parser;
    parser.reset();
    feed(parser, "HTTP/1.0 200 OK\r\nContent-Length: 7\r\nX-Test: yes\r\n\r\n{\"x\":1}");
    assert(parser.state() == HttpResponseState::Complete);
    assert(parser.statusCode() == 200 && parser.bodyLength() == 7);
    assert(std::strcmp(parser.body(), "{\"x\":1}") == 0);

    parser.reset();
    feed(parser, "HTTP/1.1 200 OK\ncontent-length: 0\n\n", 1);
    assert(parser.state() == HttpResponseState::Complete && parser.bodyLength() == 0);

    parser.reset();
    feed(parser, "HTTP/1.0 401 Unauthorized\r\nContent-Length: 2\r\n\r\n{}");
    assert(parser.state() == HttpResponseState::AuthRejected && parser.statusCode() == 401);
    parser.reset();
    feed(parser, "HTTP/1.0 503 Busy\r\nContent-Length: 2\r\n\r\n{}");
    assert(parser.state() == HttpResponseState::HttpError && parser.statusCode() == 503);

    parser.reset();
    feed(parser, "HTTP/1.0 200 OK\r\nContent-Length: 17\r\n\r\n");
    assert(parser.state() == HttpResponseState::TooLarge);
    parser.reset();
    feed(parser, "HTTP/1.0 200 OK\r\nContent-Length: 7\r\nContent-Length: 6\r\n\r\n");
    assert(parser.state() == HttpResponseState::Malformed);
    parser.reset();
    feed(parser, "HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n");
    assert(parser.state() == HttpResponseState::Malformed);
    parser.reset();
    feed(parser, "HTTP/1.0 200 OK\r\nContent-Length: 7\r\n\r\n{\"x\":");
    parser.close();
    assert(parser.state() == HttpResponseState::Malformed);

    BoundedHttpResponse<16, 16, 64> small;
    small.reset();
    feed(small, "HTTP/1.0 200 OK\r\nLong-Header: 123456789\r\n\r\n");
    assert(small.state() == HttpResponseState::TooLarge);

    NasRequestSchedule schedule;
    schedule.reset(100);
    assert(schedule.pick(100) == NasRequestKind::Net);
    schedule.start(NasRequestKind::Net, 100);
    assert(!schedule.expired(499));
    assert(schedule.expired(500));
    schedule.finish(500, false);
    assert(schedule.pick(500) == NasRequestKind::Status);
    schedule.start(NasRequestKind::Status, 500);
    schedule.finish(520, true);
    assert(schedule.pick(899) == NasRequestKind::None);
    assert(schedule.pick(900) == NasRequestKind::Net);

    schedule.reset(1000);
    schedule.start(NasRequestKind::Net, 1000);
    schedule.finish(1020, true);
    assert(schedule.pick(1020) == NasRequestKind::Status);
    schedule.start(NasRequestKind::Status, 1020);
    schedule.finish(1040, true);
    assert(schedule.pick(1199) == NasRequestKind::None);
    assert(schedule.pick(1200) == NasRequestKind::Net);

    const uint32_t nearWrap = UINT32_MAX - 100;
    schedule.reset(nearWrap);
    schedule.start(NasRequestKind::Net, nearWrap);
    assert(!schedule.expired(nearWrap + 399));
    assert(schedule.expired(nearWrap + 400));
}
'''
    with tempfile.TemporaryDirectory() as directory:
        cpp = Path(directory) / "check.cpp"
        binary = Path(directory) / "check"
        cpp.write_text(source)
        subprocess.run(
            [compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic",
             "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)
    print("PASS: fragmented HTTP, error bounds, deadlines, backoff and millis wrap")


if __name__ == "__main__":
    main()
