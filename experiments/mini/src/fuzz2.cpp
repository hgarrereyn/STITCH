#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include "lib.h"

// ----- HELPER CODE -----
#include <cstdio>
#include <type_traits>
#include <string>
#include <sstream>

// Forward declaration
template<typename T>
std::string generate_initializer(const T& value);

// Specialization for fundamental types (int, char, etc.)
template<typename T>
std::string generate_fundamental_initializer(const T& value) {
    if constexpr (std::is_same_v<T, char>) {
        // Handle special characters and printable chars differently
        if (std::isprint(value)) {
            return "'" + std::string(1, value) + "'";
        } else {
            return std::to_string(static_cast<int>(value));
        }
    } else {
        return std::to_string(value);
    }
}

// Specialization for arrays
template<typename T, std::size_t N>
std::string generate_array_initializer(const T(&arr)[N]) {
    std::string result = "{";
    for (std::size_t i = 0; i < N; ++i) {
        if (i > 0) result += ", ";
        result += generate_initializer(arr[i]);
    }
    result += "}";
    return result;
}

// Main template function
template<typename T>
std::string generate_initializer(const T& value) {
    if constexpr (std::is_fundamental_v<T>) {
        return generate_fundamental_initializer(value);
    } else if constexpr (std::is_array_v<T>) {
        return generate_array_initializer(value);
    } else {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        // For structs and other complex types, we'd need additional handling here
        return "/* Complex type not yet supported */";
    }
}

// Function to generate complete declaration
template<typename T>
std::string generate_declaration(const std::string& var_name, const T& value) {
    std::string type_name;
    if constexpr (std::is_array_v<T>) {
        using element_type = std::remove_extent_t<T>;
        type_name = std::string(typeid(element_type).name()) + 
                   "[" + std::to_string(std::extent_v<T>) + "]";
    } else {
        type_name = typeid(T).name();
    }
    
    return type_name + " " + var_name + " = " + generate_initializer(value) + ";";
}
// -------------------------
extern "C" void exec_shim_0(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
out_ref[0] = new Foo();

// -------------------------
}

extern "C" void exec_shim_1(void **in_ref, void **out_ref, char *context) {
using __arg_0_type = char;
__arg_0_type __arg_0;
std::memcpy(&__arg_0, context, sizeof(char));
context += sizeof(char);
// ----- TEMPLATE CODE -----
((Foo*)in_ref[0])->write(__arg_0);
out_ref[0] = ((Foo*)in_ref[0]);

// -------------------------
}

extern "C" void exec_shim_2(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
((Foo*)in_ref[0])->check();
out_ref[0] = ((Foo*)in_ref[0]);

// -------------------------
}

extern "C" void exec_shim_3(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
delete ((Foo*)in_ref[0]);

// -------------------------
}

extern "C" void write_shim_0(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("Foo* *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo()\n");
printf("var_%1$td = new Foo();\n\n", (intptr_t)out_ref[0]);
printf("} // end Foo()\n");
// -------------------------
}

extern "C" void write_shim_1(void **in_ref, void **out_ref, char *context) {
using __arg_0_type = char;
__arg_0_type __arg_0;
std::memcpy(&__arg_0, context, sizeof(char));
context += sizeof(char);
// ----- TEMPLATE CODE -----
printf("Foo* *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo::write(char val)\n");
printf("using __arg_0_type = char;\n");
printf("__arg_0_type __arg_0 = %s;\n", generate_initializer(__arg_0).c_str());
printf("var_%1$td->write(__arg_0);\nvar_%2$td = var_%1$td;\n\n", (intptr_t)in_ref[0], (intptr_t)out_ref[0]);
printf("} // end Foo::write(char val)\n");
// -------------------------
}

extern "C" void write_shim_2(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("Foo* *var_%td;\n", (intptr_t)out_ref[0]);
printf("{ // start Foo::check()\n");
printf("var_%1$td->check();\nvar_%2$td = var_%1$td;\n\n", (intptr_t)in_ref[0], (intptr_t)out_ref[0]);
printf("} // end Foo::check()\n");
// -------------------------
}

extern "C" void write_shim_3(void **in_ref, void **out_ref, char *context) {
// ----- TEMPLATE CODE -----
printf("{ // start ~Foo()\n");
printf("delete var_%1$td;\n\n", (intptr_t)in_ref[0]);
printf("} // end ~Foo()\n");
// -------------------------
}

const unsigned int shim_size_0 = 0;
const unsigned int shim_size_1 = sizeof(char);
const unsigned int shim_size_2 = 0;
const unsigned int shim_size_3 = 0;
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

    unsigned int GRAPHFUZZ_SHIM_SIZES[] = {
        shim_size_0,
        shim_size_1,
        shim_size_2,
        shim_size_3,
    };

    const unsigned int GRAPHFUZZ_SHIMS_COUNT = 4;

    void GRAPHFUZZ_TARGET_INFO(void **exec_shim_ptr, void **write_shim_ptr, unsigned int **shim_size_ptr, unsigned int *shim_count) {
        *exec_shim_ptr = GRAPHFUZZ_EXEC_SHIMS;
        *write_shim_ptr = GRAPHFUZZ_WRITE_SHIMS;
        *shim_size_ptr = GRAPHFUZZ_SHIM_SIZES;
        *shim_count = GRAPHFUZZ_SHIMS_COUNT;
    }
}
