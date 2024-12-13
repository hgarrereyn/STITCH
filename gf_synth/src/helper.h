#include <cstdio>
#include <type_traits>
#include <string>
#include <sstream>

template<typename T>
bool can_deserialize_directly() {
    return std::is_standard_layout_v<T> && 
           std::is_trivially_copyable_v<T> &&
           !std::is_pointer_v<T>;
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
