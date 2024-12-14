#include <cstring>
#include <vector>
#include <cstdio>
#include <cassert>


class Foo {
public:
    Foo(): buffer(0) {}

    void f1(char val) {
        buffer.push_back(val);
    }

    void f2(int x, float y) {}

    void f3(size_t size) {}

    void f4(int x[20]) {}

    void f5(int x[5][5]) {}

    void f6(char a, char b[10], int x[10], float y[100]) {
        if (a == 'a') {
            __builtin_trap();
        }
    }
private:
    std::vector<char> buffer;
};
