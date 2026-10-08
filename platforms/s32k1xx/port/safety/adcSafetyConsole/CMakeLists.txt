# Console group "safeadc" of the safety supervision (IO-19). Deliberately NOT part of safeSystem: the
# application linker script puts the data of every libsafe* archive into the MPU protected safety region and
# asserts that it has no preinitialized data, while a command object carries a statically initialized vtable
# pointer. Diagnosis is not safety code.
add_library(adcSafetyConsole src/adcSafetyConsole/AdcSafetyConsole.cpp)

target_include_directories(adcSafetyConsole PUBLIC include)

target_link_libraries(
    adcSafetyConsole
    PUBLIC util
    PRIVATE safeSystem asyncConsole)
