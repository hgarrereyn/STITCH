
#include <stdio.h>
#include "lib.h"

extern "C" void shim_0(void **in_ref, void **out_ref, const char *context) {
    // printf("shim_0\n");
    Foo *foo = new Foo();
    out_ref[0] = foo;
}

extern "C" void shim_1(void **in_ref, void **out_ref, const char *context) {
    // printf("shim_1\n");
    Foo *foo = (Foo *)in_ref[0];
    char val = context[0];
    foo->write(val);
    out_ref[0] = foo;
}

extern "C" void shim_2(void **in_ref, void **out_ref, const char *context) {
    // printf("shim_2\n");
    Foo *foo = (Foo *)in_ref[0];
    foo->check();
    out_ref[0] = foo;
}

extern "C" void shim_3(void **in_ref, void **out_ref, const char *context) {
    // printf("shim_3\n");
    Foo *foo = (Foo *)in_ref[0];
    delete foo;
}

extern "C" {
    void (*FUZZER_SHIMS[])(void **, void **, const char *) = {
        shim_0,
        shim_1,
        shim_2,
        shim_3,
    };

    const unsigned int FUZZER_SHIMS_COUNT = 4;

    void GRAPHFUZZ_TARGET_INFO(void **shim_ptr, unsigned int *shim_count) {
        *shim_ptr = FUZZER_SHIMS;
        *shim_count = FUZZER_SHIMS_COUNT;
    }
}
