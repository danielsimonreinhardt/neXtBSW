#pragma once

// Host stub of etl::typed_storage for HighSideSwitchBindingHostTest.cpp (ARCH-66): the trait only names the type and
// its create(); the test never creates a system, so create() is declared, not defined.

namespace etl
{
template<class T>
class typed_storage
{
public:
    template<class... Args>
    T& create(Args&&... args);
};
} // namespace etl
