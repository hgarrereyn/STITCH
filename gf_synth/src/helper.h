#include <cstdio>
#include <type_traits>
#include <string>
#include <sstream>
#include <random>
#include <limits>
#include <setjmp.h>

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
    }

    void GRAPHFUZZ_POST_INIT() {
        for (void *ptr : gf_allocations) {
            free(ptr);
        }
    }
}

// Invoked from user code inside shims
void gf_bail() {
    longjmp(gf_jump_buf, 1);
}

namespace gf_rand {
    // Thread-local random number generator
    inline std::mt19937& get_generator() {
        static thread_local std::random_device rd;
        static thread_local std::mt19937 gen(rd());
        return gen;
    }

    // Set seed for the random number generator
    inline void seed(unsigned int s) {
        get_generator().seed(s);
    }

    // Primary template for get<T>()
    template<typename T>
    T get() {
        static_assert(std::is_standard_layout_v<T>, "Type must be standard layout");
        static_assert(!std::is_pointer_v<T>, "Pointer types are not supported in gf_rand::get");
        
        if constexpr (std::is_same_v<T, bool>) {
            std::uniform_int_distribution<int> dist(0, 1);
            return dist(get_generator()) == 1;
        }
        else if constexpr (std::is_integral_v<T>) {
            std::uniform_int_distribution<T> dist(
                std::numeric_limits<T>::min(),
                std::numeric_limits<T>::max()
            );
            return dist(get_generator());
        }
        else if constexpr (std::is_floating_point_v<T>) {
            std::uniform_real_distribution<T> dist(
                std::numeric_limits<T>::lowest(),
                std::numeric_limits<T>::max()
            );
            return dist(get_generator());
        }
        else if constexpr (std::is_enum_v<T>) {
            using underlying = typename std::underlying_type<T>::type;
            return static_cast<T>(get<underlying>());
        }
        else if constexpr (std::is_array_v<T>) {
            T arr;
            using ElementType = std::remove_extent_t<T>;
            constexpr std::size_t N = std::extent_v<T>;
            for (std::size_t i = 0; i < N; ++i) {
                arr[i] = get<ElementType>();
            }
            return arr;
        }
        else {
            // For other types, generate random bytes
            T value;
            auto* bytes = reinterpret_cast<unsigned char*>(&value);
            for (size_t i = 0; i < sizeof(T); ++i) {
                bytes[i] = get<unsigned char>();
            }
            return value;
        }
    }

    std::string get_string(size_t len) {
        std::string str;
        for (size_t i = 0; i < len; ++i) {
            str += get<char>();
        }
        return str;
    }
}