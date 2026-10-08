"""Builds the application UDS stack for the host as DLL (Zig/Clang), used by test_app_uds_host.py.

Compiles ecu/uds (DiagStack, router, the jobs generated from the project's diagnostic description by tools/diaggen,
session configuration) together with the unchanged OpenBSW
libraries uds, docan, transport, cpp2can and the util logger. async, interrupts and the CAN
transceiver are replaced by the host simulation in AppUdsHost.cpp.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BSW = ROOT / "external" / "openbsw" / "libs" / "bsw"
DEFAULT_OUT = ROOT / "build" / "host" / "app_uds_host.dll"
# bl_config.h includes the generated memory layout and diagnostic addresses of a project (ARCH-08, COM-11):
# the eval board demo is the default test data, another project gives the stack other CAN ids and ISO-TP values
DEFAULT_PROJECT = ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml"

EXTRA_DEFINES = [d for d in os.environ.get("APP_UDS_HOST_DEFINES", "").split() if d]
# NVM-04: a source compiled in place of another one (a deliberately broken copy, test_app_uds_dtc_mutations.py)
SOURCE_OVERRIDES: dict[Path, Path] = {}
UDS_OUTGOING_SOURCES = {"AbstractDiagApplication.cpp", "ManagedOutgoingDiagConnection.cpp",
                        "OutgoingDiagConnection.cpp", "OutgoingDiagConnectionManager.cpp"}


def sources(diag_dir: Path) -> list[Path]:
    src = [
        HERE / "AppUdsHost.cpp",
        ROOT / "ecu/uds/src/uds/app/DiagStack.cpp",
        ROOT / "ecu/uds/src/uds/app/BootloaderIdentification.cpp",
        ROOT / "ecu/uds/src/uds/app/ApplicationIdentification.cpp",   # COM-44: F191/F1A0 of the application
        ROOT / "ecu/uds/src/uds/app/DiagDescriptionServices.cpp",
        ROOT / "ecu/uds/src/uds/app/DeviceKeySecurity.cpp",
        ROOT / "ecu/uds/src/uds/app/DiagDidReader.cpp",   # NVM-03: snapshot identifiers of the fault memory
        ROOT / "ecu/uds/src/uds/app/FaultMemoryServices.cpp",  # NVM-04: 19 and 14
        # COM-28: jobs of the project's diagnostic description (tools/diaggen) and the RTE functions they call as a
        # value store (DiagRteStub.cpp, diag_stub_set/get), because the host build has no RTE
        diag_dir / "DiagDescription.cpp",
        diag_dir / "DiagRteStub.cpp",
        ROOT / "ecu/uds/src/uds/app/AppProgrammingPreconditions.cpp",
        ROOT / "ecu/uds/src/transport/routing/DiagTransportRouter.cpp",
        ROOT / "ecu/uds/configuration/src/transport/TransportConfiguration.cpp",
        ROOT / "ecu/uds/configuration/src/uds/session/DiagSession.cpp",
        ROOT / "ecu/configuration/common/src/busid/BusId.cpp",
    ]
    # PLAT-52: the sources of outgoing diagnostic connections belong to the OpenBSW target udsOutgoing, which exists
    # only with UDS_ENABLE_OUTGOING (off in the application, see libs/bsw/uds/CMakeLists.txt)
    src += sorted(p for p in (BSW / "uds/src").rglob("*.cpp") if p.name not in UDS_OUTGOING_SOURCES)
    src += sorted((BSW / "docan/src").rglob("*.cpp"))
    src += sorted((BSW / "transport/src").rglob("*.cpp"))
    src += sorted((BSW / "cpp2can/src").rglob("*.cpp"))
    src += [BSW / "transportRouterSimple/src/transport/TpRouterLogger.cpp"]
    src += sorted((BSW / "util/src/util/logger").glob("*.cpp"))
    src += sorted((BSW / "util/src/util/format").glob("*.cpp"))
    src += [BSW / "util/src/util/stream/StringBufferOutputStream.cpp"]
    return [SOURCE_OVERRIDES.get(p, p) for p in src]


def includes() -> list[Path]:
    return [
        HERE / "include",  # must precede the OpenBSW async/interrupts headers
        ROOT / "ecu/uds/include",
        ROOT / "ecu/uds/configuration/include",
        ROOT / "libs/bsw/dem/include",  # NVM-03: dem::IDidReader, dem::registry
        ROOT / "ecu/configuration/include",
        ROOT / "bootloader/include",
        ROOT / "ecu/etl_profile",
        ROOT / "external/openbsw/libs/3rdparty/etl/include",
        BSW / "async/include",
        BSW / "asyncImpl/include",
        BSW / "uds/include",
        BSW / "docan/include",
        BSW / "transport/include",
        BSW / "transportRouterSimple/include",
        BSW / "cpp2can/include",
        BSW / "common/include",
        BSW / "util/include",
        BSW / "time/include",
        BSW / "lifecycle/include",
        BSW / "bsp/include",
        ROOT / "external/openbsw/libs/bsp/bspInterrupts/include",
        ROOT / "bootloader/src/core",  # COM-30: sha256.h, crc32.h (last, as PRIVATE in ecu/uds/CMakeLists.txt)
    ]


# COM-30: C sources of the bootloader core the application compiles as they are (ecu/uds/CMakeLists.txt)
C_SOURCES = [ROOT / "bootloader/src/core/sha256.c", ROOT / "bootloader/src/core/crc32.c"]


def c_objects(obj_dir: Path) -> list[Path]:
    """The C sources as objects: zig c++ would compile a .c file as C++ (other symbol names)."""
    obj_dir.mkdir(parents=True, exist_ok=True)
    objects = []
    for source in C_SOURCES:
        obj = obj_dir / f"{source.stem}.o"
        cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-O1", "-std=c11",
               "-Wall", "-Wextra", "-Werror", "-c", str(source), "-o", str(obj)]
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            raise RuntimeError(f"host build of {source.name} failed")
        objects.append(obj)
    return objects


def generate_diag(project: Path, out_dir: Path, diag: Path | None = None, product: bool = False) -> Path:
    """Diagnostic jobs of `project` (or of the description `diag` on its own) with the RTE stand-in (COM-28);
    `product`: generated for a product build (COM-43, 28/85 need security access)."""
    sys.path.insert(0, str(ROOT / "tools" / "diaggen"))
    import diaggen  # noqa: E402

    d = diaggen.load_file(diag) if diag is not None else diaggen.load_project(project)
    d.product = product
    diaggen.write(diaggen.plan(d), out_dir, rte_stub=True)
    return out_dir


def generate_dem(diag: Path, out_dir: Path) -> Path:
    """NVM-04: tables of the fault memory of the description `diag` (RteDem.h of tools/rtegen) for the host build.

    Only descriptions whose events the BSW reports (event.bsw) fit: the host build has no components whose ports
    could be sampled, the test reports the results itself (app_dem_report). RteDem.h checks its sizes against
    RteConfig.h of the RTE; the host build writes the three sizes it needs instead of the whole RTE.
    """
    sys.path.insert(0, str(ROOT / "tools" / "rtegen"))
    import rtegen  # noqa: E402
    from nxconfig import config as nxconfig_config  # noqa: E402

    desc, messages, _ = nxconfig_config.read_diag(Path(diag))
    if desc is None:
        raise RuntimeError(f"{diag}: " + "; ".join(str(m) for m in messages))
    plan = rtegen.plan_dem(desc, {}, {}, {})
    if plan is None:
        raise RuntimeError(f"{diag}: no DTCs, nothing for the fault memory of the host build")
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = (f"constexpr size_t DEM_DTC_COUNT     = {len(plan.dtcs)}U;",
             f"constexpr size_t DEM_EVENTS_BYTES  = {plan.events_bytes}U;",
             f"constexpr size_t DEM_ENTRIES_BYTES = {plan.entries_bytes}U;")
    (out_dir / "RteConfig.h").write_text("\n".join((
        "// Generated by ecu/uds/test/host/build_app_uds_host.py - sizes of the fault memory for RteDem.h",
        "#pragma once", "#include <cstddef>", "namespace rte", "{", *sizes, "} // namespace rte", "")),
        encoding="utf-8")
    (out_dir / "RteDem.h").write_text(rtegen.render_dem_header(plan, Path(diag).name), encoding="utf-8")
    return out_dir


def build(out: Path = DEFAULT_OUT, project: Path = DEFAULT_PROJECT, mcudb: Path | None = None,
          diag: Path | None = None, dem: bool = False, product: bool = False) -> Path:
    """`dem`: with a fault memory (libs/bsw/dem) of the tables of `diag` in dem::registry (NVM-04, AppUdsHost.cpp);
    without, 19 answers like a server without DTCs and 14 with NRC 0x22. `product`: as the product build
    (NX_PRODUCT_BUILD, SYS-74) compiles ecu/uds - COM-43: 28 and 85 behind security access."""
    out.parent.mkdir(parents=True, exist_ok=True)
    diag_dir = generate_diag(project, out.parent / f"{out.stem}_diag", diag, product)
    logger_defs = HERE / "HostLoggerComponents.cpp"
    cmd = [
        sys.executable, "-m", "ziglang", "c++",
        "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c++17",
        "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-missing-field-initializers",  # OpenBSW sources
        "-fno-exceptions", "-fno-rtti",
        "-DPLATFORM_SUPPORT_CAN=1", "-DPLATFORM_SUPPORT_TRANSPORT=1", "-DPLATFORM_SUPPORT_UDS=1",
        *EXTRA_DEFINES,
        *(["-DNX_PRODUCT_BUILD=1"] if product else []),
    ]
    cmd += [f"-I{p}" for p in includes()] + [f"-I{diag_dir}"]
    if dem:
        if diag is None:
            raise RuntimeError("the fault memory of the host build needs a description of its own (diag)")
        cmd += [f"-I{generate_dem(diag, out.parent / f'{out.stem}_dem')}", "-DAPP_UDS_HOST_DEM=1"]
    # memory layout and diagnostic addresses generated from `project` (ARCH-08, COM-11)
    sys.path.insert(0, str(ROOT / "tools" / "canflash"))
    import hostbuild  # noqa: E402

    cmd.append(f"-I{hostbuild.generate_layout(Path(project), out.parent / f'{out.stem}_gen', mcudb)}")
    cmd += [str(p) for p in sources(diag_dir)] + [str(logger_defs)]
    cmd += [str(p) for p in c_objects(out.parent / f"{out.stem}_obj")] + ["-o", str(out)]
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        raise RuntimeError(f"host build of {out.name} failed")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT,
                Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PROJECT))
