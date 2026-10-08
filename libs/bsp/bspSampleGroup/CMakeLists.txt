# IO-62: L1 contract of a sample group (sampleGroup/SampleGroup.h), header only, board and family neutral. The
# family implementation (s32k1xx: platforms/s32k1xx/bsp/bspSampleGroup) links it. Not yet part of the module list of
# the root CMakeLists.txt: no image uses a sample group before the fast-loop dispatcher exists (ARCH-65).
add_library(bspSampleGroup INTERFACE)

target_include_directories(bspSampleGroup INTERFACE include)
