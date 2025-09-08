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
#include <unordered_map>
#include <cctype>
#include <tuple>

namespace std {
    template<>
    struct hash<std::tuple<void *, std::string>> {
        size_t operator()(const std::tuple<void *, std::string>& k) const {
            auto h1 = std::hash<void*>{}(std::get<0>(k));
            auto h2 = std::hash<std::string>{}(std::get<1>(k));
            // Cheap hash combine:
            return h1 ^ (h2 << 1);
        }
    };
}

template <typename T>
constexpr auto type_name() {
  std::string_view name, prefix, suffix;
#ifdef __clang__
  name = __PRETTY_FUNCTION__;
  prefix = "auto type_name() [T = ";
  suffix = "]";
#elif defined(__GNUC__)
  name = __PRETTY_FUNCTION__;
  prefix = "constexpr auto type_name() [with T = ";
  suffix = "]";
#elif defined(_MSC_VER)
  name = __FUNCSIG__;
  prefix = "auto __cdecl type_name<";
  suffix = ">(void)";
#endif
  name.remove_prefix(prefix.size());
  name.remove_suffix(suffix.size());
  return name;
}

// Forward declaration
template<typename T>
std::string generate_initializer(const T& value);

// Specialization for fundamental types (int, char, etc.)
template<typename T>
std::string generate_fundamental_initializer(const T& value) {
    if constexpr (std::is_same_v<T, char>) {
        static const std::unordered_map<char, std::string> escape_sequences = {
            {'\'', "\\'"},
            {'\\', "\\\\"},
            {'\n', "\\n"},
            {'\t', "\\t"},
            {'\r', "\\r"},
            {'\0', "\\0"},
            {'\b', "\\b"},
            {'\f', "\\f"},
            {'\v', "\\v"},
            {'\a', "\\a"}
        };

        auto it = escape_sequences.find(value);
        if (it != escape_sequences.end()) {
            return "'" + it->second + "'";
        } else if (std::isprint(static_cast<unsigned char>(value))) {
            return "'" + std::string(1, value) + "'";
        } else {
            // Non-printable and not in the predefined escape list, output as numeric
            return std::to_string(static_cast<int>(static_cast<unsigned char>(value)));
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
        // Handle enum types by casting with type name
        using underlying_type = std::underlying_type_t<T>;
        // convert underlying_type to c++ string
        auto underlying_type_str = type_name<underlying_type>();
        return "(" + std::string(underlying_type_str) + ") " + std::to_string(static_cast<underlying_type>(value));
    } else if constexpr (std::is_array_v<T>) {
        return generate_array_initializer(value);
    } else {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        // For structs and other complex types, we'd need additional handling here
        return "/* Complex type not yet supported */";
    }
}

template <typename T>
void get_input(void *input, T *value) {
    if (sizeof(T) <= sizeof(void *)) {
        std::memcpy((void *)value, &input, sizeof(T));
    } else {
        T *ptr = (T *)input;
        std::memcpy((void *)value, (void *)ptr, sizeof(T));
        free((void *)ptr);
    }
}

template <typename T>
void set_output(void **output, T *value) {
    if (sizeof(T) <= sizeof(void *)) {
        std::memcpy(output, (void *)value, sizeof(T));
    } else {
        T *ptr = (T *)malloc(sizeof(T));
        std::memcpy((void *)ptr, (void *)value, sizeof(T));
        *output = (void *)ptr;
    }
}

// Keep track of allocated pointers
std::vector<void *> gf_allocations;

// Keep track of allocated files
std::vector<std::string> gf_files;

// Keep track of attributes
std::unordered_map<std::tuple<void *, std::string>, int> *gf_attrs_int;
std::unordered_map<std::tuple<void *, std::string>, std::string> *gf_attrs_str;
std::unordered_map<std::string, int> *gf_attrs_int_global;
std::unordered_map<std::string, std::string> *gf_attrs_str_global;


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

// Invoked from user code inside shims
void gf_bail() {
    longjmp(gf_jump_buf, 1);
}

extern "C" {
    void GRAPHFUZZ_INVOKE_SHIM(void *shim, void *in, void *out, void *context, int *bailed) {
        if (setjmp(gf_jump_buf) != 0) {
            *bailed = 1;
            return;
        }
        *bailed = 0;
        try {
            ((void (*)(void *, void *, void *))shim)(in, out, context);
        } catch (std::exception& e) {
            // fprintf(stderr, "Terminating due to std::exception: %s\n", e.what());
            *bailed = 1;
        }
    }

    void GRAPHFUZZ_PRE_INIT() {
        gf_allocations.clear();
        gf_files.clear();

        gf_attrs_int = new std::unordered_map<std::tuple<void *, std::string>, int>();
        gf_attrs_str = new std::unordered_map<std::tuple<void *, std::string>, std::string>();
        gf_attrs_int_global = new std::unordered_map<std::string, int>();
        gf_attrs_str_global = new std::unordered_map<std::string, std::string>();
    }

    void GRAPHFUZZ_POST_INIT() {
        // Skip this to avoid double-free if harness is incorrect
        // for (void *ptr : gf_allocations) {
        //     free(ptr);
        // }
        for (const std::string& filename : gf_files) {
            remove(filename.c_str());
        }
    }
}


namespace gf_fuzz_param {
    template <typename T>
    T *fuzz_param() {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        static_assert(!std::is_pointer_v<T>, "Pointer types are not supported in FUZZ_PARAM");
        static_assert(!std::is_class_v<T>, "Struct/class types are not supported in FUZZ_PARAM");
    }

    template <size_t N>
    const char *fuzz_param_str() {}

    template <size_t N>
    const char *fuzz_param_file() {}

    const char *write_to_temp_file(const unsigned char *data, size_t len) {
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

        file.write((const char *)data, len);
        file.close();

        gf_files.push_back(filename);

        char *result = (char *)gf_malloc(filename.size() + 1);
        strcpy(result, filename.c_str());
        return result;
    }

    template <typename T>
    void set_attr_int(T *var, const char *key, int value) {
        (*gf_attrs_int)[std::make_tuple(var, key)] = value;
    }

    template <typename T>
    void set_attr_str(T *var, const char *key, const char *value) {
        (*gf_attrs_str)[std::make_tuple(var, key)] = value;
    }

    template <typename T>
    int get_attr_int(T *var, const char *key) {
        auto it = gf_attrs_int->find(std::make_tuple(var, key));
        if (it != gf_attrs_int->end()) {
            return it->second;
        }
        gf_bail();
        return 0;
    }

    template <typename T>
    const char *get_attr_str(T *var, const char *key) {
        auto it = gf_attrs_str->find(std::make_tuple(var, key));
        if (it != gf_attrs_str->end()) {
            return it->second;
        }
        gf_bail();
        return nullptr;
    }

    void set_attr_int_global(const char *key, int value) {
        (*gf_attrs_int_global)[key] = value;
    }

    void set_attr_str_global(const char *key, const char *value) {
        (*gf_attrs_str_global)[key] = value;
    }

    int get_attr_int_global(const char *key) {
        auto it = gf_attrs_int_global->find(key);
        if (it != gf_attrs_int_global->end()) {
            return it->second;
        }
        gf_bail();
        return 0;
    }

    const char *get_attr_str_global(const char *key) {
        auto it = gf_attrs_str_global->find(key);
        if (it != gf_attrs_str_global->end()) {
            return it->second.c_str();
        }
        gf_bail();
        return nullptr;
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
