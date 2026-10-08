# CSEc of the family s32k1xx (NVM-13). See README.md.
#
#   bspCsecRecovery  csec/CsecRecovery.h: the way back to the factory state (LOAD_KEY of the MASTER_ECU_KEY into its
#                    empty slot, INIT_RNG, GET_ID, DBG_CHAL, DBG_AUTH), launched from RAM, IRREVERSIBLE steps behind a
#                    typed confirmation, no key flag ever. EXCLUDE_FROM_ALL and linked NOWHERE: NVM-07 adds it to a
#                    test build behind an option (default OFF), for the board run with the user.
#
# Host tests: test/test_csec.py (CSEc model, mutants, guards, target object). Host side: tools/csec.

add_library(bspCsecRecovery EXCLUDE_FROM_ALL src/CsecRecovery.cpp)
target_include_directories(bspCsecRecovery PUBLIC include)
# bspFlexNvm (INTERFACE, flexnvm/FlexNvmChip.h: SIM SDID CSEc bit, FCNFG EEERDY) must exist before this directory
target_link_libraries(bspCsecRecovery PRIVATE bspFlexNvm bspCore bspInterrupts bspMcu)
