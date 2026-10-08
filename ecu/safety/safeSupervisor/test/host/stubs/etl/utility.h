/* SYS-23 host test stub: etl::to_underlying as SafeSupervisor.cpp uses it. */
#pragma once

#include <type_traits>

namespace etl
{
template<typename T>
constexpr typename std::underlying_type<T>::type to_underlying(T const value)
{
    return static_cast<typename std::underlying_type<T>::type>(value);
}
} // namespace etl
