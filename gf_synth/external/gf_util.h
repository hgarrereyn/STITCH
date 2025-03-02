
// External GraphFuzz utilities

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
#include <type_traits>
#include <random>


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
