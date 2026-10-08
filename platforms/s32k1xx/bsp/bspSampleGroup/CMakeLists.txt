# IO-62: sample group of the family s32k1xx (FTM trigger -> TRGMUX -> PDB -> ADC), header only
# (sampleGroup/SampleGroupChain.h), implementation of the contract libs/bsp/bspSampleGroup. Configuration: the
# generated bsp/sampleGroup/sampleGroupConfiguration.h (NX_SAMPLE_GROUP_COUNT). Not yet added from
# port/bspConfiguration: the board port that arms a group and owns its ADC vector comes with the fast-loop dispatcher
# (ARCH-65); until then the family code is compiled by tools/bswgen/test_bswgen_samplegroup.py (host and target).
add_library(bspSampleGroupS32k1xx INTERFACE)

target_include_directories(bspSampleGroupS32k1xx INTERFACE include)

target_link_libraries(bspSampleGroupS32k1xx INTERFACE bspMcu bspSampleGroup)
