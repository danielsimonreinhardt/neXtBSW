# AUTOSAR base types (`autosarBase`)

Header-only library with the AUTOSAR base types `Std_Types.h`, `Platform_Types.h` and `Compiler.h`. The generated
RTE and the component code generated from the Simulink models include them. It is a catalogue entry only
(`kind: library`): `tools/nxconfig` and `tools/bswgen` assign no board device to it, take no project values and
generate nothing for it.

**State:** verification `mock` - host type test and translation probe for the core profiles, nothing on a board
(see [Tests](#tests)).

## Interface

| Header | Content |
|---|---|
| `include/Platform_Types.h` | `CPU_TYPE` = `CPU_TYPE_32`, `CPU_BIT_ORDER` = `LSB_FIRST`, `CPU_BYTE_ORDER` = `LOW_BYTE_FIRST`; includes `<stdint.h>`; `uint8`..`uint64` and `sint8`..`sint64` are typedefs of `uint8_t`..`uint64_t` and `int8_t`..`int64_t` (ARCH-46); `boolean` (`uint8_t`, not `_Bool`), the `*_least` types (all `int`), `float32` (`float`), `float64` (`double`), `VoidPtr`, `ConstVoidPtr`; `TRUE`/`FALSE` unless already defined |
| `include/Std_Types.h` | includes the two other headers; `Std_ReturnType` (`uint8`), `E_OK`, `E_NOT_OK`, `STD_HIGH`/`STD_LOW`, `STD_ACTIVE`/`STD_IDLE`, `STD_ON`/`STD_OFF`; `StatusType` and `E_OK` only if `STATUSTYPEDEFINED` is not set yet |
| `include/Compiler.h` | compiler abstraction for GCC/Clang: `FUNC`, `P2VAR`, `P2CONST`, `CONSTP2VAR`, `CONSTP2CONST`, `P2FUNC`, `CONSTP2FUNC`, `CONST`, `VAR`, `FUNC_P2CONST`, `FUNC_P2VAR`, `STATIC`, `INLINE`, `LOCAL_INLINE`, `NULL_PTR` |

`Std_Types.h` is described in its header as a *minimal subset* of AUTOSAR_SWS_StandardTypes. Types such as
`Std_VersionInfoType` are not there.

## Integration

- CMake: `add_library(autosarBase INTERFACE)` with `include/` as include directory. Users in this repository:
  `ecu/rte/CMakeLists.txt` (generated RTE and the component library `swcComponents`); the generator tests of
  `tools/rtegen` and `tools/a2lgen` put `libs/bsw/autosarBase/include` on the include path when they compile
  generated code on the host.
- Configuration: none - no `board.yaml` or `project.yaml` key, no module parameters
  (see [modules reference](../../../../doc/reference/modules.md#m-autosarbase)).

## Behaviour

Definitions only, no code and no state.

- `Compiler.h` ignores all memory and pointer classes: a flat memory model without near/far pointers is assumed, so
  `P2VAR(uint8, AUTOMATIC, RTE_APPL_DATA)` becomes `uint8*`.
- `Platform_Types.h` takes the fixed-width integer types from `<stdint.h>` of the toolchain (ARCH-46, user decision
  2026-09-19, option B): `uint32` *is* `uint32_t`, so pointers to AUTOSAR and `<stdint.h>` types can be exchanged
  without cast in C and C++. With the newlib of `arm-none-eabi-gcc`, `uint32`/`sint32` are therefore
  `unsigned long`/`long` (before ARCH-46: `unsigned int`/`int`, same size and range). The host builds of the tests
  use the same header and get the host's `<stdint.h>` types.
- `boolean` stays the AUTOSAR unsigned 8-bit type (`uint8_t`) with `TRUE`/`FALSE`, deliberately not C99 `bool`:
  `_Bool` converts every non-zero value to 1, which would change the generated component code.
- The `*_least` types stay `signed int`/`unsigned int` (natural word size of the 32-bit core); option B does not
  cover them, and `uint_least8_t` would be 8 bits wide.

## Limits and known gaps

- `Platform_Types.h` is written for the core profiles of decision D11 in
  [`doc/architecture/README.md`](../../../../doc/architecture/README.md): ARM Cortex-M and Cortex-R, 32 bit, little
  endian. Its header comment says so since ARCH-45 (before, it named Cortex-M only); the definitions did not change.
  The translation probe (see [Tests](#tests)) checks them for Cortex-M4F, Cortex-M0+ and Cortex-R4.
- Since ARCH-46 the integer types depend on the toolchain's `<stdint.h>`: a `printf` format or a C++ overload
  written for `unsigned int` does not fit `uint32` on newlib (`unsigned long`); use `PRIu32` or the `<stdint.h>`
  name. Until ARCH-45, `uint32`/`sint32` were `unsigned int`/`signed int` and distinct from `uint32_t`/`int32_t`
  of newlib (incompatible-pointer warning in C, error in C++). The machine code of the target images did not change
  with ARCH-46 (SHA-256 comparison in CHANGELOG.md).
- On the 64-bit host, `VoidPtr` and `ConstVoidPtr` are 8 bytes although `CPU_TYPE` is `CPU_TYPE_32`; host tests use
  the target header unchanged. Only the target probe checks the 32-bit pointer width.
- A target whose `int` is not 32 bits or which is big endian would need its own `Platform_Types.h`; there is no
  selection mechanism for that today.
- How far the project follows AUTOSAR in general is described in
  [`doc/architecture/autosar.md`](../../../../doc/architecture/autosar.md).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-autosarbase).

```sh
.venv/Scripts/python tools/test -k autosar-base      # both entries
```
