#include <stdio.h>
#include <stdint.h>
#include "lib.h"

extern "C" void shim_0(void **in_ref, void **out_ref, const char *context) {
    // Bar constructor with magic value
    uint64_t magic;
    memcpy(&magic, context, sizeof(uint64_t));
    Bar *bar = new Bar(magic);
    out_ref[0] = bar;
}

extern "C" void shim_1(void **in_ref, void **out_ref, const char *context) {
    // Bar::enable
    Bar *bar = (Bar *)in_ref[0];
    bar->enable();
    out_ref[0] = bar;
}

extern "C" void shim_2(void **in_ref, void **out_ref, const char *context) {
    // Bar::value
    Bar *bar = (Bar *)in_ref[0];
    bar->value();
    out_ref[0] = bar;
}

extern "C" void shim_3(void **in_ref, void **out_ref, const char *context) {
    // Bar destructor
    Bar *bar = (Bar *)in_ref[0];
    delete bar;
}

extern "C" void shim_4(void **in_ref, void **out_ref, const char *context) {
    // Foo constructor
    Foo *foo = new Foo();
    out_ref[0] = foo;
}

extern "C" void shim_5(void **in_ref, void **out_ref, const char *context) {
    // Foo::compose
    Foo *foo = (Foo *)in_ref[0];
    Bar *bar1 = (Bar *)in_ref[1];
    Bar *bar2 = (Bar *)in_ref[2];
    foo->compose(bar1, bar2);
    out_ref[0] = foo;
    out_ref[1] = bar1;
    out_ref[2] = bar2;
}

extern "C" void shim_6(void **in_ref, void **out_ref, const char *context) {
    // Foo::check
    Foo *foo = (Foo *)in_ref[0];
    foo->check();
    out_ref[0] = foo;
}

extern "C" void shim_7(void **in_ref, void **out_ref, const char *context) {
    // Foo destructor
    Foo *foo = (Foo *)in_ref[0];
    delete foo;
}

extern "C" {
    void (*FUZZER_SHIMS[])(void **, void **, const char *) = {
        shim_0,
        shim_1,
        shim_2,
        shim_3,
        shim_4,
        shim_5,
        shim_6,
        shim_7,
    };

    const unsigned int FUZZER_SHIMS_COUNT = 8;

    void GRAPHFUZZ_TARGET_INFO(void **shim_ptr, unsigned int *shim_count) {
        *shim_ptr = FUZZER_SHIMS;
        *shim_count = FUZZER_SHIMS_COUNT;
    }
}