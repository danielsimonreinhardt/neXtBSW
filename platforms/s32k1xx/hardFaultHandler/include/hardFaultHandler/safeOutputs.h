/* IO-32: safe outputs of the fault handlers - layout of the table customHardFaultHandler works through first.
 *
 * Shared by the assembler handlers (src/<architecture>/hardFaultHandler.S) and the C/C++ definition of the table.
 * The table nxSafeOutputs is an array of entries of four 32-bit words in the flash, ended by an entry with address 0.
 * For each entry the handler writes `value` to `address` (32-bit store), unless `gateAddress` is not 0 and
 * (*gateAddress & gateMask) is 0 - the clock of the peripheral is gated and the access would bus fault.
 *
 * The handler uses R0-R3 only (stacked by the exception entry), no stack and no RAM, and runs before the dump and
 * before any output. The library holds a weak empty table; the board port of the family defines the strong one from
 * its generat (e.g. platforms/s32k1xx/port/main/src/os/isr/isr_wdg.cpp with bsp/safeOutputs/safeOutputsConfiguration.h
 * of tools/bswgen). Family neutral: only addresses and values, no register names.
 *
 * IO-32 (deadman): nxSafeOutputsSwitchOff() below is the same walk for an interrupt handler that **returns** - the
 * halt deadman of the debug halt guard (DebugHaltGuardSystem.h). The assembler handlers cannot call it (they must
 * keep LR = EXC_RETURN and must not touch the stack), so the table semantics exist twice; both are checked against
 * each other by the host test of the guard and by the disassembly test test/test_safe_outputs_order.py. */

#ifndef HARD_FAULT_HANDLER_SAFE_OUTPUTS_H
#define HARD_FAULT_HANDLER_SAFE_OUTPUTS_H

#define NX_SAFE_OUTPUT_ADDRESS      0x00 /* register written, 0 ends the table */
#define NX_SAFE_OUTPUT_VALUE        0x04 /* value written (32 bit) */
#define NX_SAFE_OUTPUT_GATE_ADDRESS 0x08 /* clock gate register, 0: always write */
#define NX_SAFE_OUTPUT_GATE_MASK    0x0C /* bits of the gate register that must not all be 0 */
#define NX_SAFE_OUTPUT_SIZE         0x10

/* SYS-116 (doc/safety/partner-and-degraded.md part C, decision C1 (a)): the safe state of a PWM group is selected at
 * run time out of the states the project allows - freewheel (every switch off, the values of nxSafeOutputs) or an
 * active short (every low-side resp. high-side switch on). Only a project in which a group allows a short is built
 * with NX_SAFE_OUTPUT_SELECT (tools/bswgen safeOutputsConfiguration.cmake, public definition of this library); every
 * other project keeps the walks and the table above, byte for byte.
 *
 * With NX_SAFE_OUTPUT_SELECT every walk - the assembler handlers, nxSafeOutputsSwitchOff() below, the degraded walk
 * of safeIo - works through nxSafeOutputsSelect instead: the same entries in the same order (generated from the same
 * list, checked entry by entry against nxSafeOutputs where the table is defined), each with nine words. For an entry
 * whose `group` is k (not 0) the walk reads the selection word nxSafeStateSelection[k - 1] - **one read of ordinary
 * RAM per entry of a group**, no stack, no RAM write - and writes
 *   shortLow   if the word is exactly NX_SAFE_STATE_WORD_SHORT_LOW and the clock of the group's timer is on,
 *   shortHigh  if the word is exactly NX_SAFE_STATE_WORD_SHORT_HIGH and the clock of the group's timer is on,
 *   value      (freewheel) in every other case - 0 after a reset, any corrupted word, a gated timer clock.
 * The generator puts the freewheel value into the short slot of a state the group does not allow, so a wild write of
 * a pattern the project did not allow still ends in freewheel. The enable of a gate driver bound to the group
 * (IO-45) has 0 as its short values: a store of 0 to a set/clear register changes no pin, the enable stays as it was
 * (decision C2 (a)); its freewheel value switches it off as before.
 *
 * Each entry reads the word itself. If the word changes between the timer's entry and the enable's entry of a group
 * (only possible while a caller of the C walk has interrupts enabled), the result is "timer short, enable off" or
 * "timer freewheel, enable left on" - both are freewheel at the bridge (a disabled gate driver switches every gate
 * off). Never a short with a half-applied pattern: the timer entry is one store. */
#define NX_SAFE_STATE_WORD_SHORT_LOW  0xC35A3CA5UL /* 16 bits set, complement of the other pattern */
#define NX_SAFE_STATE_WORD_SHORT_HIGH 0x3CA5C35AUL

#define NX_SAFE_SELECT_ADDRESS         0x00 /* as nxSafeOutputs: register written, 0 ends the table */
#define NX_SAFE_SELECT_VALUE           0x04 /* freewheel value */
#define NX_SAFE_SELECT_GATE_ADDRESS    0x08 /* clock gate of the entry's peripheral, 0: always */
#define NX_SAFE_SELECT_GATE_MASK       0x0C
#define NX_SAFE_SELECT_GROUP           0x10 /* 0: no selection, k: selection word nxSafeStateSelection[k - 1] */
#define NX_SAFE_SELECT_GROUP_GATE      0x14 /* clock gate register of the group's timer (a short needs it on) */
#define NX_SAFE_SELECT_GROUP_GATE_MASK 0x18
#define NX_SAFE_SELECT_SHORT_LOW       0x1C /* value in active short low */
#define NX_SAFE_SELECT_SHORT_HIGH      0x20 /* value in active short high */
#define NX_SAFE_SELECT_SIZE            0x24

#ifndef __ASSEMBLER__
#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

typedef struct
{
    uint32_t address;
    uint32_t value;
    uint32_t gateAddress;
    uint32_t gateMask;
} NxSafeOutput;

/* Table of the project, ended by an entry with address 0 (weak empty default in the handler library). */
extern NxSafeOutput const nxSafeOutputs[];

/**
 * IO-32: the table walk for a handler that returns (the halt deadman interrupt of the debug halt guard).
 *
 * Same semantics as the assembler loop of customHardFaultHandler: for every entry write value to address (32 bit
 * store), skipping an entry whose clock gate register reads 0 under its mask - that peripheral's clock is off and
 * the access would bus fault. Ends with a data synchronization barrier so the stores have left the core before
 * anything else runs.
 *
 * No RTOS service, no logging, no driver object: callable from an interrupt of any priority and from a stalled
 * system. Header only (static inline) so that it needs no extra translation unit and so that the host test can
 * exercise it directly.
 */
#ifndef NX_SAFE_OUTPUT_REG
/* Register access of the walk: the address of the entry, as it stands. A host test defines this macro before
 * including the header to point the addresses of its model at its own register file - the loop, the gate check
 * and the order are then the ones of the target. */
#define NX_SAFE_OUTPUT_REG(address) ((volatile uint32_t*)(uintptr_t)(address))
#endif

#if defined(NX_SAFE_OUTPUT_SELECT)
/* SYS-116: entry of the extended table (layout NX_SAFE_SELECT_* above). */
typedef struct
{
    uint32_t address;
    uint32_t value;
    uint32_t gateAddress;
    uint32_t gateMask;
    uint32_t group;
    uint32_t groupGateAddress;
    uint32_t groupGateMask;
    uint32_t shortLow;
    uint32_t shortHigh;
} NxSafeOutputSelect;

/* Extended table of the project (hardFaultHandler/src/SafeOutputSelection.cpp from the generated
 * NX_SAFE_OUTPUTS_SELECT), ended by an entry with address 0. */
extern NxSafeOutputSelect const nxSafeOutputsSelect[];

/* SYS-116: the selection word per PWM group. Ordinary RAM (not the MPU protected safety RAM), for the reason of
 * nxSafeStateLatch below: every switch-off path reads it, also with the MPU gate closed. Zero initialized: after a
 * reset every group is in freewheel. Written only through hardFaultHandler/safeStateSelection.h. */
extern volatile uint32_t nxSafeStateSelection[];

/** SYS-116: the value the walks write for `entry` - see the rule above NX_SAFE_STATE_WORD_SHORT_LOW. */
static inline uint32_t nxSafeOutputSelectedValue(NxSafeOutputSelect const* const entry)
{
    if ((entry->group != 0U) && (entry->groupGateAddress != 0U))
    {
        volatile uint32_t const* const gate = NX_SAFE_OUTPUT_REG(entry->groupGateAddress);
        if ((*gate & entry->groupGateMask) != 0U)
        {
            uint32_t const word = nxSafeStateSelection[entry->group - 1U];
            if (word == NX_SAFE_STATE_WORD_SHORT_LOW)
            {
                return entry->shortLow;
            }
            if (word == NX_SAFE_STATE_WORD_SHORT_HIGH)
            {
                return entry->shortHigh;
            }
        }
    }
    return entry->value;
}

static inline void nxSafeOutputsSwitchOff(void)
{
    NxSafeOutputSelect const* entry = &nxSafeOutputsSelect[0];
    for (; entry->address != 0U; ++entry)
    {
        if (entry->gateAddress != 0U)
        {
            volatile uint32_t const* const gate = NX_SAFE_OUTPUT_REG(entry->gateAddress);
            if ((*gate & entry->gateMask) == 0U)
            {
                continue;
            }
        }
        *NX_SAFE_OUTPUT_REG(entry->address) = nxSafeOutputSelectedValue(entry);
    }
#if defined(__ARM_ARCH)
    __asm volatile("dsb" ::: "memory");
#endif
}
#else
static inline void nxSafeOutputsSwitchOff(void)
{
    NxSafeOutput const* entry = &nxSafeOutputs[0];
    for (; entry->address != 0U; ++entry)
    {
        if (entry->gateAddress != 0U)
        {
            volatile uint32_t const* const gate = NX_SAFE_OUTPUT_REG(entry->gateAddress);
            if ((*gate & entry->gateMask) == 0U)
            {
                continue;
            }
        }
        *NX_SAFE_OUTPUT_REG(entry->address) = entry->value;
    }
#if defined(__ARM_ARCH)
    __asm volatile("dsb" ::: "memory");
#endif
}
#endif

/* SYS-36: latch of the safe state S1.
 *
 * Board finding 2026-09-21 (IO-37): the table walk alone does not hold S1. It drives the pins low behind the back
 * of the drivers, and a driver that rewrites its outputs every cycle (the high-side switch) wrote IN high again
 * after one cycle, about 10 ms. Every entry into S1 (safety::SafeState::enterSafeState(), and through it every
 * reaction of the safe supervisor including the hold of SYS-23) therefore also sets this latch. Every driver of an
 * output that a module declares safe_off (module.yaml connect.<name>.safe_off, the second block of the table)
 * reads it in its own cycle and before every switch-on, and refuses to drive that output active while it is set
 * (the adapters in platforms/s32k1xx/port/main/src/integration wire it); the integration also walks the table
 * again in every poll while it is set.
 *
 * A single store, no call, no RTOS service: callable from an interrupt, with all interrupts suspended and from the
 * hold loop. Zero initialized (.bss), so a reset clears it. Any value other than 0 reads as latched, so a
 * corrupted latch errs on the safe side.
 *
 * SYS-35 (user decision safe-state-rueckweg, 2026-09-21): the explicit return path is
 * safety::SafeState::leaveSafeState() - the only caller of nxSafeStateLatchClear(). Clearing switches nothing on:
 * the drivers unlock and wait for their next explicit enable, and the PWM inhibit that the table walk left in the
 * timer stays until then as well (the integration hands it to the debug halt guard, SafeStateHold.h). While the
 * latch is set every enable is refused ("refused: safe state").
 *
 * Where the latch lives (re-evaluated with SYS-35): ordinary RAM, not the MPU protected safety RAM. Its writers are
 * the fault reactions - enterSafeState() before a reset, in every round of the hold loop of SYS-23, possibly from an
 * interrupt - and a store into the protected RAM from a path in which the MPU gate is closed ends in a fault
 * (SYS-37). A latch that cannot be set exactly when it is needed is worse than one without protection. The residual
 * risk of ordinary RAM is a wild write of exactly 0: it only unlocks the drivers, it switches nothing on, and the
 * PWM stays inhibited until an explicit enable. */
#define NX_SAFE_STATE_LATCHED 0x5AFE0051UL

/* Defined once in the handler library (src/SafeStateLatch.cpp). */
extern volatile uint32_t nxSafeStateLatch;

/* SYS-35: entries into S1 since the reset (wraps). Lets a poller see that S1 was entered even if S1 was entered and
 * left again between two of its polls - the PWM inhibit of the table walk must never be left without an owner that
 * releases it at the next explicit enable (board finding 2026-09-21: `gd enable` reported enabled, the phases were
 * floating). Only compared for a change, so an increment lost between two racing entries still reads as one. */
extern volatile uint32_t nxSafeStateEntries;

/** Latches S1 (idempotent) and counts the entry. The latch comes first: it is what locks the drivers. */
static inline void nxSafeStateLatchSet(void)
{
    nxSafeStateLatch   = NX_SAFE_STATE_LATCHED;
    nxSafeStateEntries = nxSafeStateEntries + 1U;
}

/** 1 while S1 is latched, 0 otherwise. */
static inline int nxSafeStateIsLatched(void) { return (nxSafeStateLatch != 0U) ? 1 : 0; }

/** SYS-35: entries into S1 since the reset (see nxSafeStateEntries). */
static inline uint32_t nxSafeStateEntryCount(void) { return nxSafeStateEntries; }

/**
 * SYS-35: releases the latch of S1. Called only by safety::SafeState::leaveSafeState(), the one return path out of
 * S1. Switches no output on.
 */
static inline void nxSafeStateLatchClear(void) { nxSafeStateLatch = 0U; }

#ifdef __cplusplus
}
#endif
#endif /* __ASSEMBLER__ */

#endif /* HARD_FAULT_HANDLER_SAFE_OUTPUTS_H */
