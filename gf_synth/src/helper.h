#include <cstdio>
#include <type_traits>
#include <string>
#include <sstream>
#include <random>
#include <limits>
#include <setjmp.h>
#include <fstream>  // For file operations
#include <vector>
#include <unistd.h> // For close() on POSIX systems
#include <cstring>  // For strcpy()

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
    } else if constexpr (std::is_enum_v<T>) {
        // Handle enum types by casting to their underlying type
        using underlying_type = std::underlying_type_t<T>;
        return std::to_string(static_cast<underlying_type>(value));
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

template <typename T>
void get_input(void *input, T *value) {
    if (sizeof(T) <= sizeof(void *)) {
        std::memcpy(value, &input, sizeof(T));
    } else {
        T *ptr = (T *)input;
        std::memcpy(value, ptr, sizeof(T));
        free(ptr);
    }
}

template <typename T>
void set_output(void **output, T *value) {
    if (sizeof(T) <= sizeof(void *)) {
        std::memcpy(output, value, sizeof(T));
    } else {
        T *ptr = (T *)malloc(sizeof(T));
        std::memcpy(ptr, value, sizeof(T));
        *output = ptr;
    }
}

// Keep track of allocated pointers
std::vector<void *> gf_allocations;

// Keep track of allocated files
std::vector<std::string> gf_files;

static size_t MAX_MALLOC = 0x8000;

void gf_bail();

void *gf_malloc(size_t size) {
    if (size > MAX_MALLOC) {
        gf_bail();
    }
    void *ptr = malloc(size);
    gf_allocations.push_back(ptr);
    return ptr;
}

void gf_free(void *ptr) {
    // Ignore
}

// Thread-local jump buffer for bail mechanism
static thread_local jmp_buf gf_jump_buf;

extern "C" {
    void GRAPHFUZZ_INVOKE_SHIM(void *shim, void *in, void *out, void *context, int *bailed) {
        if (setjmp(gf_jump_buf) != 0) {
            *bailed = 1;
            return;
        }
        *bailed = 0;
        ((void (*)(void *, void *, void *))shim)(in, out, context);
    }

    void GRAPHFUZZ_PRE_INIT() {
        gf_allocations.clear();
        gf_files.clear();
    }

    void GRAPHFUZZ_POST_INIT() {
        for (void *ptr : gf_allocations) {
            free(ptr);
        }
        for (const std::string& filename : gf_files) {
            remove(filename.c_str());
        }
    }
}

// Invoked from user code inside shims
void gf_bail() {
    longjmp(gf_jump_buf, 1);
}


namespace gf_fuzz_param {
    template <typename T>
    T fuzz_param() {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        static_assert(!std::is_pointer_v<T>, "Pointer types are not supported in FUZZ_PARAM");
        static_assert(!std::is_class_v<T>, "Struct/class types are not supported in FUZZ_PARAM");
    }

    template <size_t N>
    const char *fuzz_param_str() {}

    template <size_t N>
    const char *fuzz_param_file() {}

    const char *write_to_temp_file(const char *data, size_t len) {
        std::string filename;

        // POSIX: Use mkstemp
        char template_name[] = "/tmp/gfuzz_XXXXXX";
        int fd = mkstemp(template_name);
        if (fd == -1) {
            gf_bail();
        }
        close(fd);
        filename = std::string(template_name);
        
        std::ofstream file(filename, std::ios::binary);
        if (!file.is_open()) {
            gf_bail();
        }

        file.write(data, len);
        file.close();

        gf_files.push_back(filename);

        char *result = (char *)gf_malloc(filename.size() + 1);
        strcpy(result, filename.c_str());
        return result;
    }
}


// Fuzzable data, these get replaced during template synthesis
#define FUZZ_PARAM(T) gf_fuzz_param::fuzz_param<T>()
#define FUZZ_PARAM_STR(N) gf_fuzz_param::fuzz_param_str<N>()
#define FUZZ_PARAM_FILE(N) gf_fuzz_param::fuzz_param_file<N>()

/*
----------------------------

Before:
int x = FUZZ_PARAM(int);

After:
// args += ["int"];
int x = $a0;

----------------------------

Before:
char *x = FUZZ_PARAM_STR(10);

After:
// args += ["char[10]"];
char *x = (const char *)$a0;

----------------------------

Before:
char *x = FUZZ_PARAM_FILE(10);

After:
// args += ["char[10]"];
char *x = gf_fuzz_param::write_to_temp_file($a0, 10);

----------------------------

*/


// Marker for bailing out
#define FUZZ_BAIL() gf_bail()
