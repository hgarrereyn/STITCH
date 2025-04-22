#include <cassert>
#include <type_traits>

// Primary template - defaults to false
template<typename T, typename = void>
struct is_primitive_constructable : std::false_type {};

// Specialization for arithmetic types (int, float, double, char, etc.)
template<typename T>
struct is_primitive_constructable<T, 
    std::enable_if_t<std::is_arithmetic_v<T>>> 
    : std::true_type {};

// Helper to detect if T is an array type
template<typename T>
struct is_array_type : std::false_type {};

template<typename T, std::size_t N>
struct is_array_type<T[N]> : std::true_type {};

// Helper to get array element type
template<typename T>
struct array_element;

template<typename T, std::size_t N>
struct array_element<T[N]> {
    using type = T;
};

// Specialization for fixed-size arrays
template<typename T, std::size_t N>
struct is_primitive_constructable<T[N]> : 
    std::bool_constant<
        is_primitive_constructable<T>::value
    > {};

// Specialization for enums
template<typename T>
struct is_primitive_constructable<T, 
    std::enable_if_t<std::is_enum_v<T>>> 
    : std::true_type {};

// Helper variable template for easier usage
template<typename T>
inline constexpr bool is_primitive_constructable_v = 
    is_primitive_constructable<T>::value;

int main() {
    assert(is_primitive_constructable_v<TYPE_HERE>);
    return 0;
}
