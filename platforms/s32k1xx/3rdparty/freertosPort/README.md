# Forwarding stub for the FreeRTOS port selection (neXtBSW code, no third-party source)

This folder holds a single file, `CMakeLists.txt` (neXtBSW, Apache-2.0), that forwards to
`platforms/common/3rdparty/freertosPort` for callers that still name the old place (ARCH-25). It contains **no
FreeRTOS source**. Origin and licenses of the actual ports are listed in
[`platforms/common/3rdparty/freertosPort/README.md`](../../../common/3rdparty/freertosPort/README.md).
