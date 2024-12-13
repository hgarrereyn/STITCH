
#include <stdio.h>
#include "lib.h"

template<typename T>
bool can_deserialize_directly() {
    return std::is_standard_layout_v<T> && 
           std::is_trivially_copyable_v<T> &&
           !std::is_pointer_v<T>;  // Pointers shouldn't be directly serialized
}

template<typename T>
T deserialize_from_bytes(const char* data) {
    if (!can_deserialize_directly<T>()) {
        throw std::runtime_error("Type cannot be directly deserialized");
    }

    T result;
    std::memcpy(&result, data, sizeof(T));
    return result;
}

extern "C" void shim_0(void **in_ref, void **out_ref, const char *context) {
    // printf("shim_0\n");
    Foo *foo = new Foo();
    out_ref[0] = foo;
}

extern "C" int shim_1_size[] = { sizeof(int[4]), 0 };

extern "C" void shim_1(void **in_ref, void **out_ref, const char *context) {
    char val = deserialize_from_bytes<char>(context);
    context += sizeof(char);

    // printf("shim_1\n");
    Foo *foo = (Foo *)in_ref[0];
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
