
// External GraphFuzz utilities
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

void *gf_malloc(size_t size) {
    return malloc(size);
}

void gf_free(void *ptr) {
    if (ptr) {
        free(ptr);
    }
}

// Invoked from user code inside shims
void gf_bail() {
    printf("Bailing\n");
    exit(99);
}


namespace gf_fuzz_param {
    template <typename T>
    T *fuzz_param() {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        static_assert(!std::is_pointer_v<T>, "Pointer types are not supported in FUZZ_PARAM");
        static_assert(!std::is_class_v<T>, "Struct/class types are not supported in FUZZ_PARAM");
    }

    template <size_t N>
    const char *fuzz_param_str() {
        // Placeholder for compilation tests
        char *result = (char *)gf_malloc(N + 1);
        for (size_t i = 0; i < N; i++) {
            result[i] = 'A';
        }
        result[N] = '\0';
        return result;
    }

    const char *fuzz_param_file() {
        // Placeholder for compilation tests
        return "filename.txt";
    }

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

        char *result = (char *)gf_malloc(filename.size() + 1);
        strcpy(result, filename.c_str());
        return result;
    }

    template <typename T>
    void set_attr_int(T *var, const char *key, int value) {
        // Placeholder for compilation tests
    }

    template <typename T>
    void set_attr_str(T *var, const char *key, const char *value) {
        // Placeholder for compilation tests
    }

    template <typename T>
    int get_attr_int(T *var, const char *key) {
        // Placeholder for compilation tests
        return 0;
    }

    template <typename T>
    const char *get_attr_str(T *var, const char *key) {
        // Placeholder for compilation tests
        return "value";
    }

    void set_attr_int_global(const char *key, int value) {
        // Placeholder for compilation tests
    }

    void set_attr_str_global(const char *key, const char *value) {
        // Placeholder for compilation tests
    }

    int get_attr_int_global(const char *key) {
        // Placeholder for compilation tests
        return 0;
    }

    const char *get_attr_str_global(const char *key) {
        // Placeholder for compilation tests
        return "value";
    }
}

// Indicator
#define FUZZ_BAIL() gf_bail()

// Random sampling
#define FUZZ_PARAM(T) gf_fuzz_param::fuzz_param<T>()
#define FUZZ_PARAM_STR(N) gf_fuzz_param::fuzz_param_str<N>()
#define FUZZ_PARAM_FILE() gf_fuzz_param::fuzz_param_file()

// Dynamic validation
#define FUZZ_SET_ATTR_INT(var, key, value) gf_fuzz_param::set_attr_int(var, key, value)
#define FUZZ_SET_ATTR_STR(var, key, value) gf_fuzz_param::set_attr_str(var, key, value)
#define FUZZ_GET_ATTR_INT(var, key) gf_fuzz_param::get_attr_int(var, key)
#define FUZZ_GET_ATTR_STR(var, key) gf_fuzz_param::get_attr_str(var, key)

// Global state validation
#define FUZZ_SET_ATTR_INT_GLOBAL(key, value) gf_fuzz_param::set_attr_int_global(key, value)
#define FUZZ_SET_ATTR_STR_GLOBAL(key, value) gf_fuzz_param::set_attr_str_global(key, value)
#define FUZZ_GET_ATTR_INT_GLOBAL(key) gf_fuzz_param::get_attr_int_global(key)
#define FUZZ_GET_ATTR_STR_GLOBAL(key) gf_fuzz_param::get_attr_str_global(key)
