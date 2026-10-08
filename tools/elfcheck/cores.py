"""Build attributes a linked image must carry for a core of `mcudb` (PLAT-80).

Own table on purpose: tools/nxconfig/config.py CORES turns derivative.core into compiler options, this one says what
the linked result has to show, so that a wrong core or FP ABI in the chain (derivative, nxconfig, toolchain file,
a library built with other flags) cannot pass both. Values are those of IHI 0045 "Addenda to the ABI for the Arm
Architecture", section "Build attributes":

- Tag_CPU_arch (6): 10 v7, 11 v6-M, 12 v6S-M, 13 v7E-M, 17 v8-M.mainline
- Tag_CPU_arch_profile (7): 'M' microcontroller, 'R' real-time
- Tag_FP_arch (10): 0 none, 4 VFPv3-D16, 6 VFPv4-D16 (FPv4-SP-D16 of the Cortex-M4 reports this), 8 FP for ARMv8
  (FPv5 of Cortex-M7/M33)
- Tag_ABI_HardFP_use (27): 0 as Tag_FP_arch implies, 1 single precision only, 3 single and double precision
- Tag_ABI_VFP_args (28): 0 base AAPCS (integer registers, soft/softfp), 1 VFP registers (hard)

Every core with an FPU is built with the hard float ABI in this project (the toolchain file sets -mfloat-abi=hard
when the derivative has an FPU, soft otherwise); an image mixing in soft or softfp objects is refused by the linker,
an image built entirely softfp shows Tag_ABI_VFP_args 0 here.

The FPU variant: derivative.fpu if given, else the core's only variant (same rule as the configuration check).
"""

from __future__ import annotations

from dataclasses import dataclass

ARCH_NAMES = {10: "v7", 11: "v6-M", 12: "v6S-M", 13: "v7E-M", 14: "v8-A", 15: "v8-R", 16: "v8-M.baseline",
              17: "v8-M.mainline"}
FP_ARCH_NAMES = {0: "none", 2: "VFPv2", 3: "VFPv3", 4: "VFPv3-D16", 5: "VFPv4", 6: "VFPv4-D16", 7: "FP-ARMv8",
                 8: "FPv5/FP-D16 for ARMv8"}


@dataclass(frozen=True)
class CoreAttributes:
    cpu_arch: frozenset[int]
    profile: str                                # 'M' or 'R'
    fp_arch: int                                # 0 = no FPU
    hardfp_use: frozenset[int]                  # accepted Tag_ABI_HardFP_use values (0 when absent)
    vfp_args: int                               # expected Tag_ABI_VFP_args (0 when absent)


def _fpu(arch: set[int], profile: str, fp_arch: int, hardfp: set[int]) -> CoreAttributes:
    return CoreAttributes(frozenset(arch), profile, fp_arch, frozenset(hardfp), 1)


def _nofpu(arch: set[int], profile: str) -> CoreAttributes:
    return CoreAttributes(frozenset(arch), profile, 0, frozenset({0}), 0)


# derivative.core -> {fpu variant: attributes}
CORES: dict[str, dict[str, CoreAttributes]] = {
    # ARMv6-M; GCC marks Cortex-M0+ as v6S-M (SVC instruction), plain v6-M is accepted as well
    "cortex-m0plus": {"none": _nofpu({11, 12}, "M")},
    "cortex-m4f": {"sp": _fpu({13}, "M", 6, {1})},
    "cortex-m7f": {"sp": _fpu({13}, "M", 8, {1}), "dp": _fpu({13}, "M", 8, {0, 3})},
    "cortex-m33": {"sp": _fpu({17}, "M", 8, {1}), "none": _nofpu({17}, "M")},
    "cortex-r4f": {"dp": _fpu({10}, "R", 4, {0, 3})},
}


def expected(core: str, fpu: str | None) -> tuple[CoreAttributes | None, str]:
    """Attributes for a derivative's core and FPU; (None, reason) for an unknown core or an undecidable FPU."""
    variants = CORES.get(core)
    if variants is None:
        return None, f"core '{core}' is not in the table of tools/elfcheck/cores.py"
    if fpu is None:
        if len(variants) != 1:
            return None, f"core '{core}' has the FPU variants {sorted(variants)} and the derivative names none"
        return next(iter(variants.values())), ""
    if fpu not in variants:
        return None, f"core '{core}' has no FPU variant '{fpu}' (known: {sorted(variants)})"
    return variants[fpu], ""
