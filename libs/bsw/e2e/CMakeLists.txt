# AUTOSAR E2E library, profiles P01, P02 and P11 (COM-10): C99, no OpenBSW dependency, no dynamic memory.
# Host tests: test/host/E2eHostTest.c and tools/e2e/test_e2e_host.py (doc/e2e.md).
add_library(e2e src/e2e/E2E.c src/e2e/E2E_P01.c src/e2e/E2E_P02.c src/e2e/E2E_P11.c)

target_include_directories(e2e PUBLIC include)

set_target_properties(
    e2e
    PROPERTIES C_STANDARD 99
               C_STANDARD_REQUIRED ON
               C_EXTENSIONS OFF)

target_compile_options(e2e PRIVATE $<$<C_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wconversion -Werror>)
