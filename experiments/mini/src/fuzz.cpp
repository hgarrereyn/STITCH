#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include "lib.h"

extern "C" void exec_shim_0(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
out_ref[0] = new Foo();
// -------------------------
}

extern "C" void exec_shim_1(void **in_ref, void **out_ref, char *context) {
char __arg_0 = context[0];
// ----- TEMPLATE CODE -----
((Foo *)in_ref[0])->write(__arg_0);
out_ref[0] = ((Foo *)in_ref[0]);
// -------------------------
}

extern "C" void exec_shim_2(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
((Foo *)in_ref[0])->check();
out_ref[0] = ((Foo *)in_ref[0]);
// -------------------------
}

extern "C" void exec_shim_3(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
delete ((Foo *)in_ref[0]);
// -------------------------
}

extern "C" void write_shim_0(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("Foo *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo()\n");
printf("var_%1$td = new Foo();", (intptr_t)out_ref[0]);
printf("} // end Foo()\n");
// -------------------------
}

extern "C" void write_shim_1(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("Foo *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo::write(char val)\n");
printf("var_%1$td->write($a0);\nvar_%2$td = var_%1$td;", (intptr_t)in_ref[0], (intptr_t)out_ref[0]);
printf("} // end Foo::write(char val)\n");
// -------------------------
}

extern "C" void write_shim_2(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("Foo *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo::check()\n");
printf("var_%1$td->check();\nvar_%2$td = var_%1$td;", (intptr_t)in_ref[0], (intptr_t)out_ref[0]);
printf("} // end Foo::check()\n");
// -------------------------
}

extern "C" void write_shim_3(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("{ // start ~Foo()\n");
printf("delete var_%1$td;", (intptr_t)in_ref[0]);
printf("} // end ~Foo()\n");
// -------------------------
}

extern "C" {
    void (*GRAPHFUZZ_EXEC_SHIMS[])(void **, void **, char *) = {
        exec_shim_0,
        exec_shim_1,
        exec_shim_2,
        exec_shim_3,
    };

    void (*GRAPHFUZZ_WRITE_SHIMS[])(void **, void **, char *) = {
        write_shim_0,
        write_shim_1,
        write_shim_2,
        write_shim_3,
    };

    const unsigned int GRAPHFUZZ_SHIMS_COUNT = 4;

    void GRAPHFUZZ_TARGET_INFO(void **exec_shim_ptr, void **write_shim_ptr, unsigned int *shim_count) {
        *exec_shim_ptr = GRAPHFUZZ_EXEC_SHIMS;
        *write_shim_ptr = GRAPHFUZZ_WRITE_SHIMS;
        *shim_count = GRAPHFUZZ_SHIMS_COUNT;
    }
}