#include <cstring>
#include <vector>
#include <cstdio>

class Bar {
public:
    Bar(uint64_t magic) : magic(magic), enabled(false) {}

    void enable() {
        enabled = true;
    }

    uint64_t value() {
        return enabled ? magic : 0;
    }
private:
    uint64_t magic;
    bool enabled;
};


class Foo {
public:
    Foo(): buffer(0) {}

    void compose(Bar *b1, Bar *b2) {
        buffer.push_back(b1->value());
        buffer.push_back(b2->value());
    }

    void check() {
        if (buffer.size() >= 2 && \
            buffer[0] == 0xdeadbeef12345678 && \
            buffer[1] == 0xcafebabe87654321
        ) {
            __builtin_trap();
        }
    }
private:
    std::vector<uint64_t> buffer;
};
