#include <cstring>
#include <vector>
#include <cstdio>
#include <cassert>

class Foo {
public:
    Foo(): buffer(0) {}

    // write an ascii character to the buffer
    void write(char val) {
        assert(val >= 0 && val <= 127);
        buffer.push_back(val);
    }

    void check() {
        if (buffer.size() >= 4 && \
            buffer[0] == 'F' && \
            buffer[1] == 'U' && \
            buffer[2] == 'Z' && \
            buffer[3] == 'Z'
        ) {
            __builtin_trap();
        }
    }

    void append_elements(const char *data, size_t size) {
        buffer.insert(buffer.end(), data, data + size);
    }
private:
    std::vector<char> buffer;
};
