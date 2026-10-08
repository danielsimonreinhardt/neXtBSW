"""Generates the OpenBSW board configuration headers (family s32k1xx) for a project.

Input: projects/<project>/project.yaml, loaded with board and derivative through tools/nxconfig, which checks
structure, references, pin functions and resource conflicts. Only what the project uses is generated. Derivative
facts (instances, ADC resolution, pin multiplexing, device header) come from mcudb. This module adds the checks
that depend on the s32k1xx drivers (FTM register values, ADC slots). Clock sources, PLL, dividers, CAN bit timing and
UART dividers come from the nxconfig clock model (config.clock); here they are only encoded into register values.
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import nxconfig  # noqa: E402
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
from nxconfig import adc as nxadc  # noqa: E402
from nxconfig import sensor as nxsensor  # noqa: E402
from nxconfig import can as nxcan  # noqa: E402
from nxconfig import checks as nxchecks  # noqa: E402
from nxconfig import clock as nxclock  # noqa: E402
from nxconfig import nvm as nxnvm  # noqa: E402
from nxconfig import uart as nxuart  # noqa: E402
from nxconfig import identity as nxidentity  # noqa: E402  PLAT-79
from nxconfig.config import Reporter  # noqa: E402

import blgen  # noqa: E402
import modules  # noqa: E402

PIN_RE = re.compile(r"^PT([A-E])(\d{1,2})$")

# ARCH-38: pins of an SPI bus as their Io names end (<bus>_<ROLE>, see Board.collect), in the order the board port
# muxes them
SPI_PIN_ROLES = ("SCK", "SIN", "SOUT", "PCS")

ALT_NAMES = {0: "PINDISABLE", 1: "GPIO", 2: "ALT2", 3: "ALT3", 4: "ALT4", 5: "ALT5", 6: "ALT6", 7: "ALT7"}

FAMILY = "s32k1xx"
# ADC instances handled by bspConfiguration AnalogInput.cpp (index = AnalogInput::ADC_<n>_CH)
ADC_INSTANCES = ("ADC0", "ADC1")
# FlexCAN (ARCH-07): instance number of the family, interrupt vectors and message buffer split per bus.
# The CanFlex2Transceiver dispatches its interrupts over the FlexCAN instance number
# (FlexCANDevice::getIndex from the base address, fpCanTransceivers[8]).
FLEXCAN_RE = re.compile(r"^FlexCAN(\d)$")
FLEXCAN_MAX_INSTANCE = 7            # CanFlex2Transceiver::fpCanTransceivers[8]
# COM-26: the FlexCAN driver of the family port (port/bspConfiguration/bspFlexCan) splits the message buffers of each
# instance: receive below n/2 (standard ids, then CAN_RX_BUFS_EXT extended ids), application transmit buffers from n/2,
# the callback buffer n-1 (can_buffer_split). n is the derivative fact message_buffers of the instance (mcudb).
CAN_DEFAULT_MESSAGE_BUFFERS = 32    # instance without message_buffers in mcudb, and the most FlexCANDevice uses (IFLAG1)
CAN_MIN_MESSAGE_BUFFERS = 8         # smallest instance with one standard id receive buffer next to the extended ones
CAN_RX_BUFS_EXT = 2                 # receive buffers for extended ids on every instance
# Split of an instance with 32 buffers (FlexCAN0 of every S32K1xx), unchanged since ARCH-07
CAN_RX_BUFS_STD = CAN_DEFAULT_MESSAGE_BUFFERS // 2 - CAN_RX_BUFS_EXT      # 14: buffers 0..13 standard ids
CAN_TX_BUFS_APP = CAN_DEFAULT_MESSAGE_BUFFERS // 2 - 1                    # 15: buffers 16..30, 31 for the callback
# Wake-up bit of a CAN bus, same layout as the ECU state management (ecuState/EcuState.h, wakeup::canBus,
# SYS-01): bit 8 + index of the bus. The board's IEcuPowerStateController decides what it means.
CAN_WAKEUP_SHIFT = 8
CAN_WAKEUP_COUNT = 8
# Bus ids of ecu/configuration (busid/BusId.h): SELFDIAG, then the CAN buses of the project, then Ethernet
BUSID_SELFDIAG = 1
BUSID_ETHERNET = ("ETH_0", "ETH_1")
ADC_DRIVER_BITS = 12            # bspConfiguration AnalogInput.h is configured for 12 bit
ADC_SE_RE = re.compile(r"^SE(\d{1,2})$")
ADC_SLOTS = 16  # ADC_SC1_COUNT, maxChannels of bios::Adc
# internal ADCH inputs, names from bspAdc adc/adcPhysicalName.h
ADC_INTERNAL = {"INTERNAL0": "_ADCINTERNAL0", "INTERNAL1": "_ADCINTERNAL1", "INTERNAL2": "_ADCINTERNAL2",
                "INTERNAL3": "_ADCINTERNAL3", "BANDGAP": "_BANDGAP", "VREFSH": "_VREFSH", "VREFS": "_VREFS"}
# IO-31: averaging of an ADC channel over one PWM period (project adc_sampling.<channel>, adc/AdcAveraging.h).
# 64 full-scale 12-bit samples still fit the 32-bit sum; below 2 there is nothing to average. The spacing should
# leave the caller half a conversion for the call and the time source, otherwise samples come late (warning).
ADC_SAMPLING_KIND = "pwm_period_average"
ADC_SAMPLING_MIN_SAMPLES = 2
ADC_SAMPLING_MAX_SAMPLES = 64
ADC_SAMPLING_SPACING_MARGIN = Fraction(3, 2)   # spacing >= 1.5 conversions, otherwise a warning


def adc_sampling_specs(config: "nxconfig.Config") -> dict:
    """project.yaml adc_sampling (IO-31). The schema (tools/nxconfig/model.py) gets the section with the proposal
    platforms/s32k1xx/doc-proposal-IO-31.diff; until then a project cannot carry it and every channel converts once
    per read, exactly as before."""
    return dict(getattr(config.project, "adc_sampling", None) or {})


@dataclass(frozen=True)
class AdcSamplingPlan:
    period_ns: Fraction         # realized PWM period
    spacing_ns: int             # between two conversions, rounded to whole ns
    window_ns: int              # first to last sample instant: (samples - 1) * spacing
    problem: str | None         # the samples do not fit into one period
    warning: str | None         # they fit, but with little room for the call overhead


def adc_sampling_plan(period_ns: Fraction, samples: int, conversion_ns: int) -> AdcSamplingPlan:
    """Spacing of `samples` conversions over one PWM period and whether they fit (family neutral arithmetic)."""
    spacing = int(period_ns / samples + Fraction(1, 2))   # round half up
    problem = warning = None
    if samples * conversion_ns > period_ns:
        problem = (f"{samples} conversions of {conversion_ns} ns do not fit into one PWM period of "
                   f"{float(period_ns):.0f} ns (at most {int(period_ns // conversion_ns)})")
    elif spacing < conversion_ns * ADC_SAMPLING_SPACING_MARGIN:
        warning = (f"{samples} conversions of {conversion_ns} ns leave {spacing - conversion_ns} ns between two "
                   f"samples (spacing {spacing} ns); with the call overhead samples may come late (console "
                   f"'adcaccess stats'); at most {int(period_ns / (conversion_ns * ADC_SAMPLING_SPACING_MARGIN))} "
                   f"samples keep half a conversion free")
    return AdcSamplingPlan(period_ns, spacing, spacing * (samples - 1), problem, warning)


# FlexTimer (bsp/io/outputPwm/ComplementaryPwm.h)
FTM_RE = re.compile(r"^FTM\d$")
# Counting limits of the PWM counter (MOD + 1 <= 0xFFFF, >= 100 ticks, prescaler 1..128, DTVAL 6 bit, DTPS 1/4/16)
# live in nxconfig (checks.PWM_COUNTERS, ARCH-33), which rejects a project value with its YAML path before this
# generator runs; here only the register encoding of the dead time prescaler.
PWM_COUNTER = nxchecks.PWM_COUNTERS[FAMILY]
DEADTIME_DTPS = {1: 0, 4: 2, 16: 3}  # divider -> DEADTIME.DTPS value
# FTM dual edge capture (bsp/capture/InputCapture.h, IO-03): period and duty cycle of a PWM input. The pair
# (2k, 2k+1) captures both edges of the signal on the pin of the even channel; the counter runs free over its
# full range, so the measurement needs no interrupt (the driver is polled by the RTE task).
CAPTURE_MODULE = "capture"          # module manifest of the family driver (project.yaml modules.capture)
CAPTURE_COUNTER_RANGE = 1 << 16     # MOD + 1 with CNTIN = 0
# One period must fit into half the counter range: the 16-bit difference of two captures is then unambiguous
# even when the counter wrapped once between them.
CAPTURE_MAX_PERIOD_TICKS = CAPTURE_COUNTER_RANGE // 2
CAPTURE_MIN_PERIOD_TICKS = 1000     # shortest period that still resolves 0.1 % (duty_permille)
CAPTURE_FTM_CHANNELS = 8            # channels per FTM (pairs 0..3)
CAPTURE_MAX_TICKS = 0x7FFFFFFF      # timeout in counter ticks, kept inside a signed 32-bit range
# Hall sensors (hall/HallSensors.h, IO-07): three digital signals per set on GPIO with the pin detect
# interrupt of their port, one time stamp per edge. Nothing about a motor is decided here: the project names
# the arrangement, the pole pairs, the speed range and the times (module hall), the board the pins, the pull
# and the inversion. Derived below: sector table, inversion mask, pin masks, interrupt vectors and levels,
# and whether the times fit the speed range and the microsecond time base.
HALL_MODULE = "hall"
HALL_SIGNALS = ("u", "v", "w")      # electrical order of the phases, U -> bit 2, V -> bit 1, W -> bit 0
HALL_SECTORS = 6                    # both arrangements have six valid states per electrical revolution
# State (u << 2) | (v << 1) | w in the order the sectors 1..6 follow each other. Sources (doc/changelog.d/IO-07.md):
# deg120 the Hall state table of the MathWorks Motor Control Blockset (Six Step Commutation: 4, 6, 2, 3, 1, 5 for
# clockwise; the six valid combinations 001, 010, 011, 100, 101, 110, so 000 and 111 cannot occur), deg60 the
# sequence 111, 110, 100, 000, 001, 011 of the same documentation (there 010 and 101 cannot occur).
HALL_SEQUENCES = {
    "deg120": (0b100, 0b110, 0b010, 0b011, 0b001, 0b101),
    "deg60": (0b111, 0b110, 0b100, 0b000, 0b001, 0b011),
}
HALL_FORWARD_SIGNS = {"sector_up": 1, "sector_down": -1}
HALL_IRQC_EITHER_EDGE = 11          # PORT_PCR.IRQC: interrupt on either edge (S32K1xx RM, PORT chapter)
HALL_PORT_LETTERS = "ABCDE"         # index of the GPIO/PORT tables of the family (GPIO_BASE_PTRS, io/ioPorts.h)
HALL_TIME_WRAP_US = 1 << 32         # getSystemTimeUs32Bit() wraps here (32-bit microseconds)
# IO-54: pin-interrupt dispatcher of the family port (bsp/pinIrq/pinIrqConfiguration.h). Edge names of the table
# (pinIrq/PinIrqTable.h Edge: PORT_PCR.IRQC 9/10/11) per physical edge of nxconfig; logical edge of the pin-event
# module (pinEvent/PinEvent.h Edge) per project edge; vectors named PORT<x> are routed per port by the hand-written
# ARMv7-M vector table (bsp/hall/HallVectors.h NX_PORT_PIN_VECTORS).
PIN_IRQ_EDGES = {"rising": "RISING", "falling": "FALLING", "both": "BOTH"}
PIN_EVENT_EDGES = {"activate": "ACTIVATE", "deactivate": "DEACTIVATE", "both": "BOTH"}
PIN_IRQ_PORT_VECTOR_RE = re.compile(r"^PORT[A-E]$")
PIN_IRQ_PINS_PER_PORT = 32
HALL_DEBOUNCE_SHARE = 4             # debounce_us must stay below a quarter of the shortest sector time
HALL_STANDSTILL_SECTORS = 2         # standstill must cover two sector times of the lowest speed
US_PER_MINUTE = 60 * 1000 * 1000
# SCG system clock select (SCG_RCCR.SCS) and PCC clock source (PCC PCS) codes per clock source
SCG_SOURCE_CODES = {"SOSC": 1, "SIRC": 2, "FIRC": 3, "SPLL": 6}
SCG_SOURCE_MACROS = {"SOSC": "OSC", "SIRC": "SIRC", "FIRC": "FIRC", "SPLL": "SPLL"}
# ARCH-22: SCG_<src>DIV[<src>DIV1/<src>DIV2] field code per divider (S32K1xx RM Rev. 13, 28.3.9/12/15/18
# SCG_SOSCDIV/SIRCDIV/FIRCDIV/SPLLDIV, PDF pages 598-611: 000 output disabled, 001 /1, 010 /2, 011 /4, 100 /8, 101 /16, 110 /32, 111 /64). The divider comes from the clock
# model (tools/nxconfig/clock.py, derivative clock.output_max_hz).
SCG_ASYNC_DIVIDER_CODES = {1: 1, 2: 2, 4: 3, 8: 4, 16: 5, 32: 6, 64: 7}
SCG_ASYNC_OUTPUT_SOURCES = ("SPLL", "FIRC", "SIRC", "SOSC")     # order of the macros in boardClock.h
# ARCH-18: PCC registers of the s32k1xx, written per used peripheral instance instead of relying on
# enablePeripheralClocks() of the OpenBSW clock driver (which knows the peripherals of the S32K148 reference
# board only). PCCn[PCS] bits 26..24 select the functional clock, PCCn[CGC] bit 30 switches the clock on
# (S32K148_PCC.h PCC_PCCn_PCS/CGC, external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp).
PCC_PCS_SHIFT = 24
PCC_CGC = 1 << 30
# Clock gates the driver switches on without a clock select, in the order of clockConfig.cpp
# (enablePeripheralClocks, lines 105..190): infrastructure every board needs, no configuration value of its own.
# `True`: the driver guards the gate with #ifdef PCC_<gate>_INDEX (the peripheral is missing on small derivatives),
# so the generated code does the same.
S32K1_INFRASTRUCTURE_GATES = (("FTFC", False), ("DMAMUX", False), ("PDB1", True), ("CRC", False), ("PDB0", False),
                              ("RTC", False), ("PORTA", False), ("PORTB", False), ("PORTC", False),
                              ("PORTD", False), ("PORTE", False), ("EWM", True), ("CMP0", False),
                              ("CMU0", True), ("CMU1", True))
# SYS-24 (finding F4, doc/safety/fmea.md 5.2): register list of SafeIo (bsp/safeIo/safeIoConfiguration.h) - which
# registers the safety layer locks at start and checks in every safety cycle, derived from the pins and peripherals of
# the safe outputs (PWM groups, safe_off outputs) and the role pin safety.locked_pin. Register facts of the s32k1xx
# family ([RM] S32K1xx Reference Manual Rev. 13; field masks as in the NXP device headers S32K1xx_PORT.h/_FTM.h/_PCC.h):
# PORT_PCR: PS bit 0, PE bit 1, DSE bit 6, MUX bits 10..8, LK bit 15 (freezes bits 15..0 until reset), ISF bit 24
# (write 1 to clear - kept out of the lock write). PFE (bit 4) is not judged: not every pin implements it.
SAFE_IO_PCR_MASK = 0x8743
SAFE_IO_PCR_LK = 1 << 15
SAFE_IO_PCR_ISF = 1 << 24
# The Io driver writes pinCfg into PCR as it is (external/openbsw/.../bspIo/src/io/Io.cpp setConfiguration); the
# attributes ioConfiguration.h adds to the function, as PCR bits (bspIo io/Io.h: STRENGTH_ON, PULLUP, PULLDOWN)
SAFE_IO_PCR_ATTRIBUTES = {"STRENGTH_ON": 1 << 6, "PULLUP": 3, "PULLDOWN": 2}
# FTM of a PWM group (bsp/io/outputPwm/ComplementaryPwm.h, ftmPwmConfiguration.h): the registers the driver writes
# once in init() and never again. Not listed: OUTMASK (first duty, disableOutputs), SWOCTRL (safe outputs walk and
# its release), CnV/SYNC (every duty) - their read-back belongs to SYS-30.
FTM_SC_CLKS_SYSTEM = 1 << 3          # SC.CLKS = 01 (bits 4..3)
FTM_SC_MASK = 0x00FF003F             # PWMEN7..0 (bits 23..16), CPWMS (5), CLKS (4..3), PS (2..0); not TOF/TOIE/RF/RIE
FTM_COMBINE_PAIR = 0x33              # COMBINE, COMP, DTEN, SYNCEN of one pair (shifted by 8 * pair)
FTM_COMBINE_PAIR_CENTER = 0x32       # IO-61 alignment center: COMP, DTEN, SYNCEN (up-down counting, no COMBINE)
FTM_SC_CPWMS = 1 << 5                # IO-61 alignment center: SC.CPWMS, up-down counting
FTM_COMBINE_MASK = 0x77777777        # per pair COMBINE, COMP, DECAPEN, DTEN, SYNCEN, FAULTEN; not DECAP, MCOMBINE
FTM_CONF_BDMMODE_MASK = 0xC0         # CONF.BDMMODE: 01 = outputs forced to their safe value in debug halt
FTM_CONF_BDMMODE_SAFE = 0x40
FTM_PAIR_DEADTIME_MASK = 0x000F00FF  # DTVALEX (19..16), DTPS (7..6), DTVAL (5..0)
FTM_INVCTRL_MASK = 0x0F              # INV3EN..INV0EN: an inverted pair swaps high and low side
PCC_CGC_MASK = PCC_CGC
# SYS-153 (rest of H9, doc/safety/hsi-s32k1xx.md; SYS-125): low-voltage detect reset of the PMC, written by boardInit()
# of the family port in every start (platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h:
# LVDSC1_START_VALUE, rule lowVoltageDetectResetOn()). PMC_LVDSC1 ([RM13] 41.7.1.2) is a byte register: LVDF bit 7
# (read-only, the supply state - not judged), LVDACK bit 6 (write-only, reads 0), LVDIE bit 5, LVDRE bit 4. Judged:
# LVDRE on, LVDIE off. Checked only (class SUPERVISION), never written by SafeIo; 8-bit access.
SAFE_IO_PMC_LVDSC1_ADDRESS = "IP_PMC_BASE + offsetof(PMC_Type, LVDSC1)"
SAFE_IO_PMC_LVDSC1_MASK = 0x30       # LVDIE | LVDRE
SAFE_IO_PMC_LVDSC1_EXPECTED = 0x10   # LVDRE
SAFE_IO_WIDTHS = (32, 8)             # access widths of the SafeIo engine (safeIo/RegisterCheck.h WIDTH_32, WIDTH_8)
# SYS-86: hardware threshold of the s32k1xx family - comparator CMP0 -> TRGMUX_FTMx -> FTMx fault input (checked
# plan: tools/nxconfig/hwthreshold.py, family code platforms/s32k1xx/bsp/bspHwThreshold). Register facts, [RM13]
# S32K1xx Series Reference Manual Rev. 13 (field masks as in the NXP device headers S32K148_CMP/_TRGMUX/_SIM/_FTM.h):
# CMP_C0 45.8.1 (S. 1290-1293): EN bit 8, INVT bit 11, PMODE bit 12 (1 = high-speed); FILTER_CNT, FPR, SE, WE 0 =
# COUT is the unfiltered comparison (FILTER_CNT 000b with SE = 0: COUT = COUTA). CMP_C1 45.8.2 (S. 1293-1296): VOSEL
# 7..0, PSEL 13..11 (plus mux), VRSEL bit 14 (0 = Vin1 = VDDA, 1 = Vin2 = bandgap, 45.1.3), DACEN bit 15, INNSEL
# 25..24 = 00 (minus input = DAC), INPSEL 28..27 = 01 (plus input = mux): COUT = 1 while the pin is above the DAC.
# CMP_C2 45.8.3: RRE/RRIE 0 (no round robin). TRGMUX 19.4 Table 19-1 (S. 425): source code 0x0E = CMP0_OUT;
# register TRGMUX_FTMx: SEL0 = hardware trigger 0, SEL1..SEL3 = fault inputs 0..2 (Figure 19-2, S. 421: out40 =
# FTM0_HWTRIG0, out41..43 = FTM0_FAULT0..2), SELn 7 bit at 8 * n, LK bit 31 (write once, until reset).
# SIM_FTMOPT0 11.3.1.3 (S. 185): FTMxFLTxSEL bit (4 * x + input) = 1: fault input from TRGMUX_FTMx instead of the pin.
# FTM 47.4.3.9 MODE[FAULTM] 10b = fault control on all channels, manual clearing (S. 1373); 47.4.3.19 FLTCTRL
# (S. 1392): FAULTnEN bit n, FFLTRnEN bit 4 + n, FFVAL 11..8, FSTATE bit 15 = 0 (safe values = POL, not tri-state);
# FLTPOL bit n = 0: high is the fault; COMBINE FAULTENm bit 6 of pair m (47.5.18: the pair is forced to POL).
CMP_C0_EN = 1 << 8
CMP_C0_INVT = 1 << 11
CMP_C0_PMODE = 1 << 12
CMP_C0_MASK = 0x58FFDF77             # every writable field of C0; not COUT (24, read only), CFF/CFR (25/26, w1c)
CMP_C1_VRSEL = 1 << 14
CMP_C1_DACEN = 1 << 15
CMP_C1_PSEL_SHIFT = 11
CMP_C1_INPSEL_MUX = 1 << 27
CMP_C1_MASK = 0x1BFFFFFF             # VOSEL, MSEL, PSEL, VRSEL, DACEN, CHN7..0, INNSEL, INPSEL
CMP_C2_RR_MASK = 0xC0000000          # RRE, RRIE
TRGMUX_CMP0_OUT = 0x0E
TRGMUX_SEL_MASK = 0x7F
TRGMUX_LK = 1 << 31
FTM_MODE_FAULTM_MASK = 0x60
FTM_MODE_FAULTM_MANUAL_ALL = 0x40    # FAULTM = 10b
FTM_FLTCTRL_MASK = 0x8FFF            # FSTATE, FFVAL, FFLTR3..0EN, FAULT3..0EN
FTM_FLTPOL_MASK = 0x0F
FTM_COMBINE_FAULTEN = 0x40           # FAULTEN of one pair (shifted by 8 * pair)
FTM_INSTANCE_RE = re.compile(r"^FTM(\d)$")
# IO-62: sample group of the s32k1xx family - FTM trigger -> TRGMUX_PDBx SEL0 -> PDBx channel 0 -> ADCx (plan
# checked by tools/nxconfig/samplegroup.py, family code platforms/s32k1xx/bsp/bspSampleGroup). Register facts, [RM13]
# S32K1xx Series Reference Manual Rev. 13 (field masks as in the NXP device headers S32K148_PDB/_ADC/_SIM.h):
# PDB_SC 46.3.1 (S. 1324-1327): PDBEN bit 7, TRGSEL 11..8 = 0 (trigger-in 0, from TRGMUX), PRESCALER/MULT 0 (the
# counter runs on SYS_CLK), CONT 0 (one-shot: one sequence per trigger), LDMOD 00 (buffers loaded at LDOK). MOD and IDLY
# 16 bit (46.3.2/46.3.4): the counter stops at MOD after the sequence, IDLY never reached. PDB_CHnC1 46.3.5 (S. 1329):
# EN 7..0, TOS 15..8, BB 23..16. SIM_ADCOPT 11.3.1.5 (S. 188-189): ADC0 TRGSEL bit 0, SWPRETRG 3..1, PRETRGSEL 5..4;
# ADC1 the same shifted by 8 - all 0 = trigger and pre-triggers from the PDB. ADC_SC1 ADCH 5..0 (external channel n
# < 16 = n, n >= 16 = 32 + n - 16 on the S32K146/148, OpenBSW adcPhysicalName.h), AIEN bit 6.
PDB_SC_PDBEN = 1 << 7
PDB_MOD_MAX = 0xFFFF
SIM_ADCOPT_ADC_FIELDS = 0x3F         # TRGSEL | SWPRETRG | PRETRGSEL of one ADC, shifted by 8 per ADC
ADC_SC1_AIEN = 1 << 6
TRGMUX_PDB_INDEX = {"PDB0": 14, "PDB1": 15}  # device header TRGMUX_PDB0_INDEX/TRGMUX_PDB1_INDEX
PDB_INSTANCE_RE = re.compile(r"^PDB(\d)$")
ADC_INSTANCE_RE = re.compile(r"^ADC(\d)$")
SAMPLE_GROUP_CALIBRATION_POLL_LIMIT = 0x00FFFFFF  # calibration (32 samples) never takes that many polls


def adc_sc1_channel(channel: str) -> int:
    """ADC_SC1[ADCH] of an external channel SE<n> (s32k1xx)."""
    m = ADC_SE_RE.match(channel)
    if m is None:
        raise ConfigError(f"ADC channel {channel}: no external channel SE<n>")
    n = int(m.group(1))
    return n if n < 16 else 32 + (n - 16)
LPUART_BOTHEDGE = 1 << 17           # LPUART BAUD.BOTHEDGE, required for oversampling 4..7
# LPUART (ARCH-15): instance number of the family. Instance pointer, receive interrupt and handler name of the
# derivative follow from it; the vector table of the board port must have a vector for every used instance.
LPUART_RE = re.compile(r"^LPUART(\d)$")
UINT32_MAX = 0xFFFFFFFF
# PLAT-21: derivative features (mcudb derivative.features) the generated integration switches on. The macro is
# always defined (1 or 0) so that the board port can use #if without knowing the derivative. Every feature of
# model.DerivativeInfo must be listed here (test_bswgen_interrupts.py).
FEATURE_MACROS = {"lmem_cache": ("NX_FEATURE_LMEM_CACHE", "LMEM processor code cache (cacheEnable)")}
# ARCH-16: NXP system MPU of the s32k1xx family. The derivative database holds the number of region descriptors
# (mcudb mpu.regions); how the hardware reports it is register knowledge of the family and belongs here.
# S32K1xx Reference Manual Rev. 14 (09/2021), 15.4.2 Control/Error Status Register, field CESR[NRGD]
# "Number Of Region Descriptors": 0000b = 8, 0001b = 12, 0010b = 16 region descriptors (no other code defined).
MPU_NRGD_CODES = {8: 0, 12: 1, 16: 2}
# PLAT-12: where the device header named by mcudb (device_header.file) is looked for, in this order: the family
# module of this repository, then the OpenBSW platform of the family (same order as stm32gen.py). A header with
# device_header.generated_by is not looked up but generated into the output directory.
HEADER_DIRS = ("platforms/{family}/bsp/bspMcu/include", "external/openbsw/platforms/{family}/bsp/bspMcu/include")
MCUDB_TOOLS = nxconfig.REPO / "mcudb" / "tools"
# Handler names of the vectors whose name differs per derivative. The generated vector list
# (startup/nxIrqVectors.inc) names the handler of every external vector; SYS-170: the startup routes every vector
# through its wrapper Safe_<handler> (safeLifecycle/IsrWrapper.cpp, list interrupts/nxSafeIsrVectors.h), everything
# without a key here is <IRQ>_IRQHandler.
# Key: the interrupt name of the device header without _IRQn - the same handler serves vectors that a derivative
# combines: the S32K11x has one FTFC vector (command complete and double bit fault), one WDOG vector (no EWM) and
# one ERM vector (one memory channel), where the S32K14x has FTFC_Fault, WDOG_EWM and ERM_double_fault.
FAMILY_IRQ_HANDLERS = {
    # S32K14x/S32K148 header: FTFM_Fault (vector 21, the ARMv7-M startup calls it FTFC_Fault); S32K11x: one FTFC
    "FTFM_Fault": "FTFC_Fault_IRQHandler", "FTFC_Fault": "FTFC_Fault_IRQHandler",
    "FTFC": "FTFC_Fault_IRQHandler",
    # IO-32: no SAFE_ISR wrapper behind Safe_WDOG_EWM_IRQHandler any more - the naked watchdog handler of the port
    # (os/isr/isr_wdg.cpp) that enters customHardFaultHandler with LR = EXC_RETURN (safe outputs first)
    "WDOG_EWM": "WDOG_EWM_IRQHandler", "WDOG": "WDOG_EWM_IRQHandler",
    "ERM_double_fault": "ERM_double_fault_IRQHandler", "ERM": "ERM_double_fault_IRQHandler",
    # IO-32: halt deadman of the debug halt guard on LPIT channel 1 (integration/DebugHaltGuardSystem.h). The
    # S32K14x/S32K148 header gives every channel its own vector (LPIT0_Ch1), the S32K11x one shared vector for the
    # whole module (LPIT, [RM] Rev. 13 48.1) - under one handler name, so the integration needs no derivative
    # switch. The handler checks MSR[TIF1] first, which a shared vector needs.
    "LPIT": "LPIT0_Ch1_IRQHandler",
}
# SYS-170: the safety handlers of the family port. Their Safe_<handler> is no generic wrapper: FTFC fault and ERM
# double fault are SAFE_ISR wrappers of their own in IsrWrapper.cpp (they react and reset), Safe_WDOG_EWM_IRQHandler
# is the naked watchdog path of isr_wdg.cpp, Safe_LPIT0_Ch1_IRQHandler the halt deadman of the debug halt guard
# itself (IO-32: power outputs off before any call - an alias in integration/Integration.cpp, weak DefaultISR in
# IsrWrapper.cpp without a guard). Every other external vector is wrapped generically
# (interrupts/nxSafeIsrVectors.h) - also the unused ones, which end in DefaultISR behind the wrapper.
SAFETY_IRQ_HANDLERS = ("FTFC_Fault_IRQHandler", "WDOG_EWM_IRQHandler", "ERM_double_fault_IRQHandler",
                       "LPIT0_Ch1_IRQHandler")
# Interrupt names the family drivers of OpenBSW use literally, and the names a derivative may use for the same
# vector instead. Emitted as a macro into mcu/mcuDerivative.h when the derivative has only the alternative
# (e.g. safeBspMcuWatchdog/Watchdog.cpp uses WDOG_EWM_IRQn, the S32K116 device header names it WDOG_IRQn).
FAMILY_IRQ_ALIASES = {"WDOG_EWM_IRQn": ("WDOG_IRQn",), "FTFC_Fault_IRQn": ("FTFC_IRQn",),
                      "ERM_double_fault_IRQn": ("ERM_IRQn",),
                      # IO-32: the S32K11x names the shared LPIT vector LPIT_IRQn (see FAMILY_IRQ_HANDLERS)
                      "LPIT0_Ch1_IRQn": ("LPIT_IRQn",)}
# Message buffer interrupt names of a FlexCAN instance, in the order the board port enables them. A derivative
# either splits them (S32K14x: buffers 0..15 and 16..31) or has one vector for all buffers (S32K11x).
FLEXCAN_MB_IRQS = ("CAN{n}_ORed_0_15_MB_IRQn", "CAN{n}_ORed_16_31_MB_IRQn", "CAN{n}_ORed_0_31_MB_IRQn")


class ConfigError(Exception):
    pass


def flexcan_mb_irqs(config: "nxconfig.Config", bus, irqs) -> list[str]:
    """Message buffer interrupts (FLEXCAN_MB_IRQS order) of the instance of a CAN bus that the derivative has.

    ARCH-42: the device header is not enough - the S32K144 uses the S32K148 header, which names
    CAN1/CAN2_ORed_16_31_MB_IRQn, but FlexCAN1/2 of the S32K144 have 16 buffers and no such vector ([RM13] attachment
    S32K1xx_DMA_Interrupt_mapping.xlsx, IRQ 89/96 "-"). A vector whose first buffer the instance does not have
    (mcudb message_buffers) is left out; without message_buffers the header decides as before.
    """
    n = FLEXCAN_RE.match(bus.instance).group(1)
    inst = config.derivative.instances.get(bus.instance)
    buffers = None if inst is None else inst.message_buffers
    found = []
    for pattern in FLEXCAN_MB_IRQS:
        name = pattern.format(n=n)
        first = int(re.search(r"_ORed_(\d+)_", pattern).group(1))
        if name in irqs and (buffers is None or first < buffers):
            found.append(name)
    return found


@dataclass(frozen=True)
class CanBufferSplit:
    """COM-26: message buffers of one FlexCAN instance as FlexCANDevice (family port) uses them."""
    buffers: int            # message buffers of the instance the driver uses (Config.numMessageBuffers)
    rx_std: int             # receive buffers 0..rx_std-1: standard ids
    rx_ext: int             # the next rx_ext receive buffers: extended ids
    tx_app: int             # application transmit buffers from first_transmit on

    @property
    def first_transmit(self) -> int:
        return self.buffers // 2

    @property
    def callback(self) -> int:
        return self.buffers - 1


def can_buffer_split(config: "nxconfig.Config", bus) -> CanBufferSplit:
    """COM-26: split of the message buffers of the instance of a CAN bus (rtegen uses it for the receive filter).

    The number comes from the derivative (mcudb instances.<FlexCANn>.message_buffers); an instance without the fact
    counts as 32, the split FlexCANDevice of OpenBSW assumes. The driver only uses IFLAG1/IMASK1, so a larger
    instance (none in the S32K1xx family) is used with its first 32 buffers.
    """
    inst = config.derivative.instances.get(bus.instance)
    buffers = getattr(inst, "message_buffers", None) or CAN_DEFAULT_MESSAGE_BUFFERS
    buffers = min(buffers, CAN_DEFAULT_MESSAGE_BUFFERS)
    if buffers % 2 or buffers < CAN_MIN_MESSAGE_BUFFERS:
        raise ConfigError(f"CAN {bus.name}: {bus.instance} has {buffers} message buffers; the FlexCAN driver splits an "
                          f"even number of at least {CAN_MIN_MESSAGE_BUFFERS} into receive and transmit halves")
    half = buffers // 2
    return CanBufferSplit(buffers, half - CAN_RX_BUFS_EXT, CAN_RX_BUFS_EXT, half - 1)


@dataclass
class Pin:
    name: str
    pin: str
    direction: str
    mux: int
    ext: str = "0"
    extra_cfg: str = ""

    @property
    def port(self) -> str:
        return PIN_RE.match(self.pin).group(1)

    @property
    def number(self) -> int:
        return int(PIN_RE.match(self.pin).group(2))

    @property
    def pin_cfg(self) -> str:
        cfg = f"(uint32_t) {ALT_NAMES[self.mux]}"
        return cfg + self.extra_cfg


identifier = modules.identifier     # C name of a board entry, shared with the module headers (NX_DEVICE_*_IO_*)


class Board:
    """Configuration of one project for the s32k1xx OpenBSW drivers."""

    def __init__(self, config: "nxconfig.Config"):
        self.config = config
        self.mcu = config.board.board.mcu
        family = config.derivative.derivative.family
        if family != FAMILY:
            raise ConfigError(f"{self.mcu}: family {family} is not supported by bswgen (only {FAMILY})")
        package = config.board.board.package
        self.mux = nxchecks.pinmux(config)
        if self.mux is None or not self.mux.complete:
            raise ConfigError(f"{self.mcu} {package}: no complete pin multiplexing table in mcudb "
                              "(derivative pinmux, needed for PORT_PCR.MUX)")
        self.pins: dict[str, Pin] = {}
        self.used_pads: dict[str, str] = {}
        self.spi_buses: list[dict] = []         # used SPI buses and their pins (collect, ARCH-38)
        self.safety = None
        self.plan = config.clock
        if self.plan is None:
            raise ConfigError(f"{self.mcu}: no clock plan (clock data of the derivative missing in mcudb)")
        # PLAT-12: interrupt numbers of the derivative's device header (IRQn_Type). The interrupt vectors of a
        # peripheral differ per derivative (the S32K11x has one FlexCAN message buffer vector, the S32K14x two),
        # so nothing in the generated integration spells out an interrupt name of one derivative.
        self.header_text, self.irqs = device_header_text(config)
        self.warnings: list[str] = []
        # (source, board name) -> (rank, NVIC level); filled by collect_interrupts(), read by the channels
        self.irq_levels: dict[tuple[str, str], tuple[int, int]] = {}
        # IO-54: pin-interrupt dispatcher table and pin events (collect_pin_irq)
        self.pin_irq: dict = {"pins": [], "vectors": []}
        self.pin_events: list[dict] = []
        self.hw_threshold: dict | None = None      # SYS-86: register values of the hardware threshold
        self.sample_groups: list[dict] = []         # IO-62: register values of the sample groups

    def _mux_of(self, signal: str, pin: str) -> int:
        if not PIN_RE.match(pin):
            raise ConfigError(f"{signal}: invalid pin name '{pin}'")
        selector = self.mux.selector(signal, pin)
        if selector is None:  # already rejected by nxconfig; kept as a guard for the generated table
            raise ConfigError(f"{self.mcu} has no function {signal} on {pin} "
                              f"(available: {', '.join(self.mux.functions(pin))})")
        if not isinstance(selector, int) or selector not in ALT_NAMES:
            raise ConfigError(f"{self.mcu}: selector {selector} of {signal} on {pin} is no PORT_PCR.MUX value")
        return selector

    def add(self, name: str, pin: str, signal: str, direction: str, ext: str = "0", extra_cfg: str = ""):
        ident = identifier(name)
        if ident in self.pins:
            raise ConfigError(f"duplicate signal name {ident}")
        if pin in self.used_pads:  # already rejected by nxconfig; kept as a guard for the generated table
            raise ConfigError(f"{pin} used by both {self.used_pads[pin]} and {ident}")
        self.used_pads[pin] = ident
        self.pins[ident] = Pin(ident, pin, direction, self._mux_of(signal, pin), ext, extra_cfg)

    def collect(self):
        c = self.config

        def bus_pin(kind, bus, role, direction, extra_cfg=""):
            self.add(f"{bus.name}_{role if kind == 'i2c' else role.upper()}", getattr(bus, role).pin,
                     nxchecks.bus_signal(c, kind, bus, role), direction, extra_cfg=extra_cfg)

        # ARCH-38: every used SPI bus with its four pins, in the order the board port muxes them
        # (bsp/spi/spiBusConfiguration.h). Unlike CAN and UART, whose OpenBSW drivers mux their own pins, the SPI
        # master of the family (bsp::spi::Lpspi) takes no pins: without this list a bus runs internally without
        # pins, reports no error and reads 0 (board test 2026-09-18, IO-25).
        self.spi_buses = []
        for bus in c.selected("spi"):
            bus_pin("spi", bus, "sck", "_OUT", " | (uint32_t) STRENGTH_ON")
            bus_pin("spi", bus, "sin", "_IN")
            bus_pin("spi", bus, "sout", "_OUT", " | (uint32_t) STRENGTH_ON")
            bus_pin("spi", bus, "pcs", "_OUT")
            self.spi_buses.append({"name": bus.name, "ident": identifier(bus.name), "instance": bus.instance,
                                   "pcs": bus.pcs.index, "device": bus.device,
                                   "pins": [identifier(f"{bus.name}_{role}") for role in SPI_PIN_ROLES]})
        for kind in ("can", "uart"):
            for bus in c.selected(kind):
                bus_pin(kind, bus, "rx", "_IN")
                bus_pin(kind, bus, "tx", "_OUT")
        for bus in c.selected("i2c"):
            problem = nxchecks.i2c_driver_problem(c, bus)     # TOOL-120: nxconfig reports it first
            if problem is not None:
                raise ConfigError(problem)
            bus_pin("i2c", bus, "sda", "_OUT")
            bus_pin("i2c", bus, "scl", "_OUT")
        for ch in c.selected("adc"):
            self.add(ch.name, ch.pin, nxchecks.adc_signal(ch.instance, ch.channel), "_IN")
        for grp, _ in c.selected_pwm():
            for ch in grp.channels:
                self.add(ch.name, ch.pin, nxchecks.timer_signal(grp.instance, ch.channel), "_OUT")
        for ch in c.selected("input_capture"):
            self.add(ch.name, ch.pin, nxchecks.timer_signal(ch.instance, ch.channel), "_IN")
        for ch in c.selected("dio.inputs"):
            extra = f" | (uint32_t) PULL{ch.pull.upper()}" if ch.pull else ""
            self.add(ch.name, ch.pin, nxchecks.GPIO, "_IN", ext="(uint8_t) FILTER_ACTIVE | (uint8_t) FILTER_TICK1",
                     extra_cfg=extra)
        for ch in c.selected("dio.outputs"):
            self.add(ch.name, ch.pin, nxchecks.GPIO, "_OUT")

        safety = c.project.safety
        if safety:
            self.add("SAFETY_TEST", safety.test_pin, nxchecks.GPIO, "_IN", extra_cfg=" | (uint32_t) PULLDOWN")
            self.safety = {"safe_state_output": identifier(safety.safe_state_output),
                           "locked_pin": identifier(safety.locked_pin)}

    def _scale(self, name: str, spec: "nxconfig.model.AdcScale") -> dict:
        """Returns num/den/offset for value = raw * num / den + offset (mV at the channel's source)."""
        if spec.divider is not None:
            div = spec.divider
            factor = (Fraction(str(div.r_top)) + Fraction(str(div.r_bottom))) / Fraction(str(div.r_bottom))
        else:
            factor = Fraction(str(spec.factor if spec.factor is not None else 1))
        offset = spec.offset_mv
        ratio = Fraction(self.adc_vref_mv) * factor / self.adc_max_raw
        num, den = ratio.numerator, ratio.denominator
        if self.adc_max_raw * num // den + max(offset, 0) > UINT32_MAX:
            raise ConfigError(f"ADC {name}: scaled full-scale value exceeds uint32")
        if num > UINT32_MAX or den > UINT32_MAX:
            raise ConfigError(f"ADC {name}: factor {factor} is too precise for 32-bit scaling (num {num}, den {den})")
        return {"num": num, "den": den, "offset": offset, "factor": factor}

    def collect_adc(self):
        """Builds the AnalogInput channel table: board channels first (board order), then internal channels."""
        c = self.config
        used = c.selected("adc")
        internal = c.project.adc_internal
        self.adc_vref_mv = c.board.board.adc_reference_mv or 0
        self.adc_max_raw = (1 << ADC_DRIVER_BITS) - 1
        channels = []
        entries = [(ch, False) for ch in used] + [(ch, True) for ch in internal]
        for ch, is_internal in entries:
            if ch.instance not in ADC_INSTANCES:
                raise ConfigError(f"ADC {ch.name}: instance {ch.instance} is not handled by the ADC driver "
                                  f"(supported: {', '.join(ADC_INSTANCES)})")
            bits = c.derivative.instances[ch.instance].resolution_bits
            if bits != ADC_DRIVER_BITS:
                raise ConfigError(f"ADC {ch.name}: the ADC driver is configured for {ADC_DRIVER_BITS} bit, "
                                  f"{ch.instance} of {self.mcu} has {bits} bit")
            ident = identifier(ch.name)
            if is_internal:
                if ch.channel not in ADC_INTERNAL:
                    raise ConfigError(f"ADC {ch.name}: internal channel '{ch.channel}' is not supported by the driver "
                                      f"(supported: {', '.join(ADC_INTERNAL)})")
                phys, pin, scale = ADC_INTERNAL[ch.channel], "PORT_UNAVAILABLE", nxconfig.model.AdcScale()
            else:
                phys, pin, scale = f"_ADCCHANNEL{ADC_SE_RE.match(ch.channel).group(1)}", ident, ch.scale
            channels.append({"name": ident, "instance": ch.instance, "adc": ADC_INSTANCES.index(ch.instance),
                             "channel": ch.channel, "phys": phys, "pin": pin, "internal": is_internal,
                             **self._scale(ch.name, scale)})

        # Board channels take SC1 slots from 0 upwards (slot 0 converts once in enableChannel), internal
        # channels the slots just below the last one (BANDGAP 13, ADCINTERNAL0 14 as in the OpenBSW original).
        for inst in ADC_INSTANCES:
            ext = [ch for ch in channels if ch["instance"] == inst and not ch["internal"]]
            intern = [ch for ch in channels if ch["instance"] == inst and ch["internal"]]
            if len(ext) + len(intern) > ADC_SLOTS - 1:
                raise ConfigError(f"{inst}: {len(ext) + len(intern)} channels, at most {ADC_SLOTS - 1} supported")
            for slot, ch in enumerate(ext):
                ch["slot"] = slot
            for i, ch in enumerate(intern):
                ch["slot"] = ADC_SLOTS - 1 - len(intern) + i
        self.adc = channels

    def collect_pwm(self):
        """FTM register values of the complementary PWM group the project uses, None without a group.

        A project may use no PWM group at all (a board without a motor bridge): the generated
        ftmPwmConfiguration.h then reports NX_PWM_GROUP_COUNT 0 and the integration leaves out the driver
        (ARCH-17). More than one group needs one driver instance per group (own FTM configuration and
        OutputPwm channels); until then the limit is an error naming the groups, not a silent choice.
        """
        groups = self.config.selected_pwm()
        if len(groups) > 1:
            used = ", ".join(f"{grp.name} on {grp.instance}" for grp, _ in groups)
            raise ConfigError(f"pwm: the project uses {len(groups)} groups ({used}), the driver ComplementaryPwm "
                              "supports one group per project (bsp/io/outputPwm): use one group in project.yaml pwm")
        self.pwm = self._pwm_group(*groups[0]) if groups else None

    def _pwm_group(self, grp: "nxconfig.model.PwmGroup", use: "nxconfig.model.PwmUse") -> dict:
        where = f"PWM {grp.name}"
        if not FTM_RE.match(grp.instance):
            raise ConfigError(f"{where}: instance {grp.instance} is no FlexTimer (ComplementaryPwm drives FTM)")
        counter = self.plan.counter_hz(grp.instance)  # FTM input clock with SC.CLKS = 01 (system clock)
        if counter.denominator != 1:
            raise ConfigError(f"{where}: FTM clock {float(counter)} Hz is no whole number")
        clock = int(counter)
        freq, deadtime_ns = use.frequency_hz, use.deadtime_ns
        active = {"high_side": grp.active.high_side, "low_side": grp.active.low_side}

        channels = {identifier(ch.name): {"channel": ch.channel, "pin": ch.pin} for ch in grp.channels}
        pairs = []
        for p in grp.pairs:
            pname, hs_name, ls_name = identifier(p.name), identifier(p.high_side), identifier(p.low_side)
            hs, ls = channels[hs_name]["channel"], channels[ls_name]["channel"]
            if hs % 2 != 0 or ls != hs + 1:
                raise ConfigError(f"{where}: pair {pname}: high side must be FTM channel 2k and low side 2k+1 "
                                  f"(COMBINE/COMP), not {hs}/{ls}")
            pairs.append({"name": pname, "index": hs // 2, "high": hs_name, "low": ls_name,
                          "high_channel": hs, "low_channel": ls})
        pairs.sort(key=lambda q: q["index"])

        # Period (smallest prescaler, 100 % uses CnV = MOD + 1) and dead time (FTM input clock divided by DTPS,
        # rounded up): one rule with nxconfig, which reports a violation with the YAML path pwm.<group>.<value>
        # before this generator runs. Here it is the last line of defence, with the same text (ARCH-33).
        center = use.alignment == "center"
        try:
            timing = nxchecks.pwm_timing(PWM_COUNTER, grp.name, grp.instance, clock, freq, deadtime_ns, center=center)
        except nxchecks.PwmTimingError as e:
            raise ConfigError(str(e)) from None
        ps, ticks, actual = timing.prescaler_shift, timing.period_ticks, timing.frequency_hz
        div, dt_ticks = timing.deadtime_divider, timing.deadtime_ticks
        dtps = DEADTIME_DTPS[div]
        dt_clock_ticks = dt_ticks * div            # in FTM input clock periods

        channel_mask = sum(1 << ch["channel"] for ch in channels.values())
        pol = sum(1 << q["high_channel"] for q in pairs if active["high_side"] == "low")
        pol |= sum(1 << q["low_channel"] for q in pairs if active["low_side"] == "low")
        trigger = self._pwm_trigger(grp, use, center, timing.counter_top, channel_mask)
        return {
            # IO-61: alignment and trigger; `io61` marks a configuration only the IO-61 aware driver may use
            "center": center, "counter_top": timing.counter_top, "trigger": trigger,
            "io61": center or (trigger is not None and trigger["kind"] == "channel"),
            "name": grp.name, "ident": identifier(grp.name),  # ident: macro token of NX_PWM_GROUP_INSTANCES
            "instance": grp.instance, "clock_hz": clock, "frequency_hz": freq,
            "frequency_actual": f"{float(actual):.3f}", "deadtime_ns": deadtime_ns,
            "period_ns": Fraction(10**9) / actual,             # realized period (IO-31: averaging window)
            "deadtime_actual_ns": f"{float(Fraction(dt_clock_ticks * 10**9, clock)):.1f}",
            "prescaler_shift": ps, "period_ticks": ticks, "deadtime_dtps": dtps, "deadtime_div": div,
            "deadtime_ticks": dt_ticks, "channel_mask": channel_mask, "pol": pol, "active": active,
            "pairs": pairs, "verify": grp.verify or use.verify,
        }

    def _pwm_trigger(self, grp, use, center: bool, top: int, channel_mask: int) -> dict | None:
        """IO-61: FTM trigger output (EXTTRIG) for the instant pwm.<group>.trigger.point, None without a trigger.

        The pulses of a pair are symmetric in both countings (combine edges around the period middle, up-down
        counting around the counter minimum), so the two instants of the project are counter values:

          alignment  zero_low (all high sides off)        zero_high (all high sides on)
          edge       counter restart CNTIN -> INITTRIG    period middle (MOD + 1)/2 -> channel match
          center     turning point top -> channel match   counter minimum CNTIN -> INITTRIG

        A channel match uses the lowest channel of the instance the group does not use (its pin stays unmuxed and
        its output masked); a group that occupies every channel cannot have that instant.
        """
        if use.trigger is None:
            return None
        where = f"PWM {grp.name}"
        point = use.trigger.point
        if (point == "zero_low") != center:
            return {"point": point, "kind": "init", "exttrig": "FTM_EXTTRIG_INITTRIGEN_MASK",
                    "where": "counter at CNTIN"}
        inst = self.config.derivative.instances.get(grp.instance)
        count = getattr(inst, "channels", None) or 8   # mcudb timer channels; FTM of the family: at most 8
        free = [ch for ch in range(count) if not channel_mask & (1 << ch)]
        if not free:
            raise ConfigError(f"{where}: trigger point {point} needs a channel match, but the group uses all "
                              f"{count} channels of {grp.instance} (pwm.{grp.name}.trigger)")
        channel = free[0]
        value = top if center else (top + 1) // 2
        return {"point": point, "kind": "channel", "channel": channel, "value": value,
                "exttrig": f"FTM_EXTTRIG_CH{channel}TRIG_MASK",
                "where": f"match of channel {channel} at counter {value}"}

    def collect_adc_sampling(self):
        """IO-31: channels the project averages over one period of its PWM group (project adc_sampling).

        Per channel the project names the kind (pwm_period_average), the PWM group and the number of conversions
        per read; derived here are the period (realized frequency of the FTM counter, not the nominal one) and the
        spacing of the conversions. Without the schema extension (platforms/s32k1xx/doc-proposal-IO-31.diff) or
        without an entry every channel converts once per read, as before. nxconfig checks the same rules with
        the YAML path (proposal); here they are the last line of defence of the generator.
        """
        specs = adc_sampling_specs(self.config)
        plans = [None] * len(self.adc)
        board_channels = {identifier(ch.name): ch for ch in self.config.selected("adc")}
        index = {ch["name"]: i for i, ch in enumerate(self.adc)}
        for name, spec in specs.items():
            where = f"adc_sampling {name}"
            ident = identifier(name)
            if ident not in index:
                used = ", ".join(ch["name"] for ch in self.adc if not ch["internal"]) or "-"
                raise ConfigError(f"{where}: no ADC channel {name} used by the project (use.adc: {used})")
            ch = self.adc[index[ident]]
            if ch["internal"]:
                raise ConfigError(f"{where}: {name} is an internal channel; it carries no PWM ripple and its "
                                  f"callers (safety checks) read it with the interrupts suspended")
            if not board_channels[ident].fitted:
                raise ConfigError(f"{where}: the circuit of {name} is not fitted on board "
                                  f"{self.config.board.board.name} (adc[].fitted: false)")
            kind = getattr(spec, "kind", None)
            if kind != ADC_SAMPLING_KIND:
                raise ConfigError(f"{where}: kind '{kind}' is not supported (supported: {ADC_SAMPLING_KIND})")
            group = getattr(spec, "pwm_group", None)
            if self.pwm is None:
                raise ConfigError(f"{where}: the project uses no PWM group, there is no period to average over "
                                  f"(pwm_group {group})")
            if group != self.pwm["name"]:
                raise ConfigError(f"{where}: PWM group {group} is not used by the project (used: "
                                  f"{self.pwm['name']})")
            samples = getattr(spec, "samples", None)
            if not isinstance(samples, int) or isinstance(samples, bool) or \
                    not ADC_SAMPLING_MIN_SAMPLES <= samples <= ADC_SAMPLING_MAX_SAMPLES:
                raise ConfigError(f"{where}: samples {samples} outside {ADC_SAMPLING_MIN_SAMPLES}.."
                                  f"{ADC_SAMPLING_MAX_SAMPLES}")
            access = self.config.adc_access
            if access is None:
                raise ConfigError(f"{where}: the conversion time is unknown (no ADC access plan, module "
                                  f"{nxadc.MODULE}), the samples cannot be checked against the period")
            plan = adc_sampling_plan(self.pwm["period_ns"], samples, access.conversion_ns)
            if plan.problem:
                raise ConfigError(f"{where}: {plan.problem}")
            if plan.warning:
                self.warnings.append(f"{where}: {plan.warning}")
            plans[index[ident]] = {"name": ident, "group": group, "samples": samples,
                                   "spacing_ns": plan.spacing_ns, "period_ns": f"{float(plan.period_ns):.1f}",
                                   "window_ns": plan.window_ns, "frequency": self.pwm["frequency_actual"],
                                   "conversion_ns": access.conversion_ns}
        self.adc_sampling = plans

    def collect_capture(self):
        """Input capture channels of the project with the FTM values derived from them, None without a channel.

        A project may use no input_capture entry at all: the generated inputCaptureConfiguration.h then reports
        NX_CAPTURE_CHANNEL_COUNT 0 and the driver leaves out every instance (ARCH-17). The counter clock comes
        from the clock model (ARCH-06), the expected signal and the supervision from the project
        (modules.capture: min_frequency_hz, timeout_ms, update_period_ms); everything else is derived here.
        """
        c = self.config
        channels = c.selected("input_capture")
        if not channels:
            self.capture = None
            return
        use = c.integration.modules.get(CAPTURE_MODULE) if c.integration is not None else None
        if use is None:
            raise ConfigError(f"input_capture {channels[0].name}: module {CAPTURE_MODULE} has no manifest or no "
                              f"parameters, the measurement cannot be configured (project modules.{CAPTURE_MODULE})")
        min_freq, timeout_ms = int(use.parameters["min_frequency_hz"]), int(use.parameters["timeout_ms"])
        update_ms = int(use.parameters["update_period_ms"])
        pwm = self.pwm
        timers: list[dict] = []
        timer_of: dict[str, int] = {}
        entries = []
        for ch in channels:
            where = f"input_capture {ch.name}"
            if not FTM_RE.match(ch.instance):
                raise ConfigError(f"{where}: instance {ch.instance} is no FlexTimer (the input capture of family "
                                  f"{FAMILY} measures with the dual edge capture of an FTM)")
            if pwm is not None and ch.instance == pwm["instance"]:
                # nxconfig already rejects this with the YAML path (ARCH-02); kept as a guard for the generator
                raise ConfigError(f"{where}: the {ch.instance} counter is also used by pwm {pwm['name']}, one "
                                  f"counter can only be used in one way")
            if ch.channel % 2 != 0:
                raise ConfigError(f"{where}: {ch.instance} channel {ch.channel} is odd, but the dual edge capture "
                                  f"measures on the even channel of a pair and takes the odd one for the second "
                                  f"edge: use channel {ch.channel - 1} (pin with function "
                                  f"{ch.instance}_CH{ch.channel - 1}) for this signal")
            if ch.channel + 1 >= CAPTURE_FTM_CHANNELS:
                raise ConfigError(f"{where}: {ch.instance} channel {ch.channel} has no partner channel "
                                  f"{ch.channel + 1} (an FTM has {CAPTURE_FTM_CHANNELS} channels)")
            # The odd partner channel cannot be taken by anything else: nxconfig rejects a second entry on the
            # same channel, a PWM group on the same counter is rejected above, and an odd channel is rejected
            # here - the generated header proves it once more with a static_assert (channelsValid).
            if ch.instance not in timer_of:
                timer_of[ch.instance] = len(timers)
                timers.append(self._capture_timer(ch.instance, min_freq, timeout_ms, update_ms))
            entries.append({"name": identifier(ch.name), "instance": ch.instance, "channel": ch.channel,
                            "timer": timer_of[ch.instance], "pin": ch.pin, "note": ch.note})
        for t in timers:
            pairs = sorted(e["channel"] // 2 for e in entries if e["instance"] == t["instance"])
            t["pairs"] = [f"{2 * p}/{2 * p + 1}" for p in pairs]
            t["combine_expression"] = " | ".join(f"(COMBINE_DECAPEN_PAIR << pairShift({2 * p}U))" for p in pairs)
        self.capture = {"module": CAPTURE_MODULE, "channels": entries, "timers": timers, "verify": use.verify,
                        "min_frequency_hz": min_freq, "timeout_ms": timeout_ms, "update_period_ms": update_ms}

    def _capture_timer(self, instance: str, min_freq: int, timeout_ms: int, update_ms: int) -> dict:
        """Prescaler, measurable range, timeout and update period of one capture counter in counter ticks."""
        where = f"input_capture on {instance}"
        counter = self.plan.counter_hz(instance)     # FTM input clock with SC.CLKS = 01 (system clock)
        if counter.denominator != 1:
            raise ConfigError(f"{where}: FTM clock {float(counter)} Hz is no whole number")
        base = int(counter)
        # smallest prescaler (best resolution) whose counter range covers one period of min_frequency_hz twice
        for ps in range(8):
            if Fraction(base, (1 << ps) * min_freq) <= CAPTURE_MAX_PERIOD_TICKS:
                break
        else:
            raise ConfigError(f"{where}: min_frequency_hz {min_freq} needs more than {CAPTURE_MAX_PERIOD_TICKS} "
                              f"counter ticks per period even with prescaler 128 (clock {base} Hz): raise "
                              f"min_frequency_hz (project modules.{CAPTURE_MODULE})")
        if base % (1 << ps) != 0:
            raise ConfigError(f"{where}: clock {base} Hz divided by the needed prescaler {1 << ps} is no whole "
                              f"number; choose another system clock or min_frequency_hz")
        clock = base >> ps
        max_period = clock // min_freq
        if max_period < CAPTURE_MIN_PERIOD_TICKS:
            raise ConfigError(f"{where}: min_frequency_hz {min_freq} leaves {max_period} counter ticks per period "
                              f"at clock {clock} Hz, at least {CAPTURE_MIN_PERIOD_TICKS} are needed for a duty "
                              f"resolution of 0.1 %: lower min_frequency_hz (project modules.{CAPTURE_MODULE})")
        update_ticks = math.ceil(Fraction(update_ms * clock, 1000))
        wrap_us = round(Fraction(CAPTURE_COUNTER_RANGE * 10**6, clock))
        if 2 * update_ticks >= CAPTURE_COUNTER_RANGE:
            raise ConfigError(f"{where}: the counter wraps after {wrap_us} us, update_period_ms {update_ms} is "
                              f"{update_ticks} ticks; a slow signal could be measured as a fast one. Lower "
                              f"min_frequency_hz (larger prescaler) or update_period_ms "
                              f"(project modules.{CAPTURE_MODULE})")
        timeout_ticks = math.ceil(Fraction(timeout_ms * clock, 1000))
        if timeout_ticks > CAPTURE_MAX_TICKS:
            raise ConfigError(f"{where}: timeout_ms {timeout_ms} is {timeout_ticks} counter ticks at clock "
                              f"{clock} Hz, more than 31 bit: lower timeout_ms")
        needed = 2 * max_period + update_ticks
        if timeout_ticks < needed:
            raise ConfigError(f"{where}: timeout_ms {timeout_ms} is shorter than two periods of min_frequency_hz "
                              f"{min_freq} plus one update cycle "
                              f"({math.ceil(Fraction(needed * 1000, clock))} ms): a healthy slow signal would be "
                              f"reported as missing. Raise timeout_ms (project modules.{CAPTURE_MODULE})")
        return {
            "instance": instance, "counter_hz": base, "prescaler_shift": ps, "clock_hz": clock,
            "tick_ns": round(Fraction(10**9, clock)), "wrap_us": wrap_us,
            "min_period_ticks": CAPTURE_MIN_PERIOD_TICKS, "max_period_ticks": max_period,
            "timeout_ticks": timeout_ticks, "update_ticks": update_ticks,
            "min_frequency_actual": f"{float(Fraction(clock, max_period)):.3f}".removesuffix(".000"),
            "max_frequency_actual": f"{float(Fraction(clock, CAPTURE_MIN_PERIOD_TICKS)):.3f}".removesuffix(".000"),
        }

    def collect_hall(self):
        """Hall sensor sets of the project with the pin interrupts derived from them, None without a set.

        A project may have no hall device at all: the generated hallSensorConfiguration.h then reports
        NX_HALL_SENSOR_COUNT 0 and the driver leaves out every instance (ARCH-17). The sector table follows
        from the arrangement, the inversion mask from the board (dio active), the pin masks and the interrupt
        vectors from the pins, and the NVIC level from the interrupt rank of the project (PLAT-21). The times
        are checked against the speed range of the motor, so a wrong combination is a message with numbers and
        not a measurement that silently misses edges.
        """
        c = self.config
        devices = c.integration.instances(HALL_MODULE) if c.integration is not None else []
        if not devices:
            self.hall = None
            return
        inputs = {ch.name: ch for ch in c.board.dio.inputs}
        ports: dict[int, dict] = {}
        sensors = []
        for dev in devices:
            sensor = self._hall_sensor(dev, inputs, ports)
            sensors.append(sensor)
        order = sorted(ports)
        for index, port in enumerate(order):
            ports[port]["index"] = port
            ports[port]["position"] = index
        for sensor in sensors:
            sensor["ports"] = sum(1 << ports[p]["position"] for p in sensor["port_indices"])
        self.hall = {
            "module": HALL_MODULE, "sensors": sensors, "irqc": HALL_IRQC_EITHER_EDGE,
            "ports": [ports[p] for p in order],
            "port_mask": sum(1 << p for p in order),
        }

    def _hall_sensor(self, dev, inputs: dict, ports: dict) -> dict:
        """One sensor set: pins, sector table, times - and the pin interrupt entries it needs."""
        where = f"hall {dev.name}"
        p = dev.parameters
        arrangement = str(p["arrangement"])
        sequence = HALL_SEQUENCES.get(arrangement)
        if sequence is None:      # the pattern of the manifest allows only known values; guard for the table
            raise ConfigError(f"{where}: arrangement {arrangement} has no sector sequence in family {FAMILY} "
                              f"(known: {', '.join(HALL_SEQUENCES)})")
        pole_pairs = int(p["pole_pairs"])
        min_rpm, max_rpm = int(p["min_speed_rpm"]), int(p["max_speed_rpm"])
        debounce_us, standstill_us = int(p["debounce_us"]), 1000 * int(p["standstill_ms"])
        pins, invert, signals, port_indices = [], 0, [], []
        rank_of: dict[str, int] = {}
        for i, role in enumerate(HALL_SIGNALS):
            name = dev.connect[role]        # complete and of kind digital_input (checked by nxconfig)
            entry = inputs[name]
            match = PIN_RE.match(entry.pin)
            if match is None:
                raise ConfigError(f"{where}: {role} on {name} has the invalid pin name '{entry.pin}'")
            letter, number = match.group(1), int(match.group(2))
            if letter not in HALL_PORT_LETTERS:
                raise ConfigError(f"{where}: {role} on {name} sits on port {letter} ({entry.pin}), which family "
                                  f"{FAMILY} has no pin interrupt table for (ports {HALL_PORT_LETTERS})")
            port = HALL_PORT_LETTERS.index(letter)
            if entry.active == "low":
                # the board inverts this signal; the driver mirrors the bit so the sector table still fits
                invert |= 4 >> i
            rank, level = self.irq_levels.get(("dio", name), (None, None))
            if rank is None:
                raise ConfigError(
                    f"{where}: the hall signal {name} ({role}, {entry.pin}) has no interrupt rank, but the "
                    f"measurement runs in the pin interrupt of {letter}: add "
                    f"{{source: dio, name: {name}, rank: <n>}} to the project (interrupts.ranks, PLAT-21)")
            rank_of[name] = rank
            # IO-54: the vector of the pin from the derivative fact (S32K14x PORT<x>, S32K11x one PORT for all)
            vector = self._pin_irq_vector(entry.pin, f"{where}: the hall signal {name}")
            slot = ports.setdefault(port, {"port": f"PORT{letter}", "letter": letter, "mask": 0, "rank": rank,
                                           "level": level, "irq": f"{vector}_IRQn",
                                           "isr": f"{vector}_IRQHandler", "signals": []})
            if slot["rank"] != rank:
                other = ", ".join(slot["signals"])
                raise ConfigError(
                    f"{where}: the hall signal {name} has interrupt rank {rank}, but {other} on the same port "
                    f"{letter} has rank {slot['rank']}; one port has one pin interrupt vector and therefore one "
                    f"NVIC level: give all hall signals of a port the same rank (project interrupts.ranks)")
            if slot["mask"] & (1 << number):
                raise ConfigError(f"{where}: two hall signals on {entry.pin}")
            slot["mask"] |= 1 << number
            slot["signals"].append(f"{name} ({entry.pin})")
            if port not in port_indices:
                port_indices.append(port)
            pins.append({"port_index": port, "pin_number": number, "name": name, "pin": entry.pin,
                         "pull": entry.pull, "active": entry.active})
            signals.append(f"{role.upper()} {name} on {entry.pin}")
        if len(set(rank_of.values())) > 1:
            # a sensor spread over two ports whose interrupts could preempt each other would tear its own state
            listed = ", ".join(f"{n} rank {r}" for n, r in rank_of.items())
            raise ConfigError(
                f"{where}: the hall signals have different interrupt ranks ({listed}); the interrupts of one "
                f"sensor must not interrupt each other, because each of them reads and steps the same state: "
                f"give all three signals the same rank (project interrupts.ranks)")

        sectors = [0] * 8
        for index, state in enumerate(sequence):
            sectors[state] = index + 1
        invalid = [f"{state:03b}" for state in range(8) if sectors[state] == 0]
        sector_us_min = int(Fraction(US_PER_MINUTE, max_rpm * pole_pairs * HALL_SECTORS))
        sector_us_max = int(Fraction(US_PER_MINUTE, min_rpm * pole_pairs * HALL_SECTORS))
        if sector_us_min < 1:
            raise ConfigError(
                f"{where}: max_speed_rpm {max_rpm} with {pole_pairs} pole pair(s) leaves less than one "
                f"microsecond per sector; the microsecond time base of the system timer cannot resolve it "
                f"(project modules.{dev.name})")
        if debounce_us * HALL_DEBOUNCE_SHARE > sector_us_min:
            raise ConfigError(
                f"{where}: debounce_us {debounce_us} is more than a {HALL_DEBOUNCE_SHARE}th of the shortest "
                f"sector time ({sector_us_min} us at max_speed_rpm {max_rpm} and {pole_pairs} pole pair(s)): "
                f"real edges would be swallowed at full speed. Lower debounce_us or max_speed_rpm "
                f"(project modules.{dev.name})")
        if standstill_us <= HALL_STANDSTILL_SECTORS * sector_us_max:
            raise ConfigError(
                f"{where}: standstill_ms {standstill_us // 1000} is not longer than "
                f"{HALL_STANDSTILL_SECTORS} sector times of min_speed_rpm {min_rpm} "
                f"({HALL_STANDSTILL_SECTORS * sector_us_max} us at {pole_pairs} pole pair(s)): a healthy slowly "
                f"turning motor would be reported as standing still. Raise standstill_ms or min_speed_rpm "
                f"(project modules.{dev.name})")
        if HALL_SECTORS * standstill_us >= HALL_TIME_WRAP_US // 2:
            raise ConfigError(
                f"{where}: {HALL_SECTORS} standstill times of standstill_ms {standstill_us // 1000} are "
                f"{HALL_SECTORS * standstill_us} us, which does not stay inside half the wrap of the 32-bit "
                f"microsecond time base ({HALL_TIME_WRAP_US // 2} us): a period could be measured across an "
                f"ambiguous time. Lower standstill_ms (project modules.{dev.name})")
        return {
            "name": identifier(dev.name), "part": dev.part, "verify": dev.verify,
            "arrangement": arrangement, "pole_pairs": pole_pairs,
            "forward": str(p["forward"]), "forward_sign": HALL_FORWARD_SIGNS[str(p["forward"])],
            "min_speed_rpm": min_rpm, "max_speed_rpm": max_rpm,
            "debounce_us": debounce_us, "standstill_us": standstill_us,
            "pins": pins, "signals": signals, "invert": invert, "sectors": sectors,
            "states": [f"{state:03b}" for state in sequence], "invalid": invalid,
            "sector_us_min": sector_us_min, "sector_us_max": sector_us_max,
            "electrical_us_min": sector_us_min * HALL_SECTORS, "electrical_us_max": sector_us_max * HALL_SECTORS,
            "port_indices": port_indices,
        }

    def _pin_irq_facts(self, what: str) -> list[dict]:
        """Vectors of the derivative fact pin_interrupts (IO-51) as this family uses them: every vector names the
        pins with an edge detector of their own (PORT_PCR.IRQC), and serves the ports those pins sit on."""
        c = self.config
        facts = c.derivative.pin_interrupts
        if facts is None:
            raise ConfigError(f"{what} needs a pin interrupt, but derivative {self.mcu} does not describe its pin "
                              f"interrupt vectors (mcudb pin_interrupts)")
        vectors = []
        for v in facts.vectors:
            if v.pins is None:
                raise ConfigError(f"{self.mcu}: pin interrupt vector {v.name} is reached through lines; family "
                                  f"{FAMILY} has an edge detector per pin (mcudb pin_interrupts vectors[].pins)")
            ports = [i for i, letter in enumerate(HALL_PORT_LETTERS)
                     if any(re.fullmatch(v.pins, f"PT{letter}{n}") for n in range(PIN_IRQ_PINS_PER_PORT))]
            vectors.append({"name": v.name, "pins": v.pins, "ports": ports})
        return vectors

    def _pin_irq_vector(self, pin: str, what: str) -> str:
        """Vector of a pin: the first vector of the derivative whose pins match it."""
        for vector in self._pin_irq_facts(what):
            if re.fullmatch(vector["pins"], pin):
                return vector["name"]
        raise ConfigError(f"{what} ({pin}): pin {pin} of {self.mcu} cannot raise an interrupt (mcudb pin_interrupts)")

    def collect_pin_irq(self):
        """IO-54: the table of the pin-interrupt dispatcher of the family port and the pin events of the project.

        Pins: the three signals of every Hall sensor set (consumer HALL, channel = sensor, both edges) and every
        pin event of project pin_events (consumer PIN_EVENT, channel = event; the edge the module needs: both while
        it debounces - the level confirmation needs every change, pinEvent detectorEdges() - else the physical edge
        nxconfig derived from `edge` and the active level of the board). Vectors: the vectors of the derivative fact
        pin_interrupts that serve a pin, in the order of the fact, with the ports they serve and the NVIC level of
        the rank; the pins of one vector have one rank (one NVIC level). This replaces the table the port derived
        from the Hall configuration (IO-52, hall/HallPinIrq.h) and the vector PORT<x> per port, which did not exist
        on the S32K11x (one vector PORT for all ports).
        """
        c = self.config
        pins: list[dict] = []
        for channel, sensor in enumerate((self.hall or {}).get("sensors", [])):
            for p in sensor["pins"]:
                pins.append({"port_index": p["port_index"], "pin_number": p["pin_number"], "edge": "BOTH",
                             "consumer": "HALL", "channel": channel, "name": p["name"], "pin": p["pin"],
                             "what": f"hall {sensor['name']}", "vector": None})
        events = []
        for channel, ev in enumerate(getattr(c, "pin_events", None) or []):
            match = PIN_RE.match(ev.pin)
            if match is None:
                raise ConfigError(f"pin event {ev.input}: invalid pin name '{ev.pin}'")
            port, number = HALL_PORT_LETTERS.index(match.group(1)), int(match.group(2))
            detector = "both" if ev.debounce_us > 0 else ev.physical_edge
            rank, level = self.irq_levels.get(("dio", ev.input), (None, None))
            if rank is None:        # nxconfig refuses the event without a rank; guard for the table
                raise ConfigError(f"pin event {ev.input}: no interrupt rank (interrupts.ranks, source dio)")
            events.append({
                "name": identifier(ev.input), "input": ev.input, "pin": ev.pin, "active": ev.active,
                "active_low": ev.active == "low", "edge": ev.edge, "edge_enum": PIN_EVENT_EDGES[ev.edge],
                "physical_edge": ev.physical_edge, "detector": detector, "port_index": port, "pin_number": number,
                "debounce_us": ev.debounce_us, "max_events": ev.max_events, "window_ms": ev.window_ms,
                "rearm_ms": ev.rearm_ms, "window_us": 1000 * ev.window_ms, "rearm_us": 1000 * ev.rearm_ms,
                "vector": ev.vector, "rank": rank, "level": level})
            pins.append({"port_index": port, "pin_number": number, "edge": PIN_IRQ_EDGES[detector],
                         "consumer": "PIN_EVENT", "channel": channel, "name": ev.input, "pin": ev.pin,
                         "what": "pin event", "vector": ev.vector})
        self.pin_events = events
        if not pins:
            self.pin_irq = {"pins": [], "vectors": []}
            return
        facts = self._pin_irq_facts(f"{pins[0]['what']} {pins[0]['name']}")
        by_name = {v["name"]: v for v in facts}
        used: dict[str, dict] = {}
        for p in pins:
            name = p["vector"] or self._pin_irq_vector(p["pin"], f"{p['what']}: {p['name']}")
            if name not in by_name:
                raise ConfigError(f"{p['what']} {p['name']} ({p['pin']}): vector {name} is no pin interrupt vector "
                                  f"of {self.mcu} (mcudb pin_interrupts)")
            rank, level = self.irq_levels[("dio", p["name"])]
            vector = used.setdefault(name, {"name": name, "rank": rank, "level": level, "members": []})
            if vector["rank"] != rank:
                others = ", ".join(vector["members"])
                raise ConfigError(
                    f"{p['what']}: {p['name']} ({p['pin']}) has interrupt rank {rank}, but {others} in the same pin "
                    f"interrupt vector {name} of {self.mcu} has rank {vector['rank']}; one vector has one NVIC "
                    f"level: give all inputs of the vector the same rank (project interrupts.ranks)")
            vector["members"].append(f"{p['name']} ({p['pin']})")
            p["vector"] = name
        vectors = []
        for index, fact in enumerate(v for v in facts if v["name"] in used):
            entry = used[fact["name"]]
            irq = f"{fact['name']}_IRQn"
            if self.irqs and irq not in self.irqs:
                raise ConfigError(f"{self.mcu}: pin interrupt vector {fact['name']} (mcudb pin_interrupts) has no "
                                  f"interrupt {irq} in the device header {c.derivative.device_header.file}")
            ports = sum(1 << i for i in fact["ports"])
            # bsp/hall/HallVectors.h states which PORT<x> vectors the port routes (since SYS-170 both startups take
            # them from the derivative's list startup/nxIrqVectors.inc); a vector of another name needs no check
            startup = ports if PIN_IRQ_PORT_VECTOR_RE.match(fact["name"]) else 0
            vectors.append({**entry, "index": index, "irq": irq, "isr": f"{fact['name']}_IRQHandler",
                            "ports": ports, "startup": startup})
        self.pin_irq = {"pins": pins, "vectors": vectors}

    def collect_clock(self):
        """Register values of the clock plan (nxconfig clock model) for boardClock.h, CAN and UART headers."""
        p, c = self.plan, self.config
        pll = p.pll
        # CLOCK_SOURCE_<gate> of boardClock.h: exactly the gates the OpenBSW clock driver consumes, in its order.
        # A gate outside that list (FTM6 of an S32K148) gets its PCC write from bsp/clock/peripheralClocks.h
        # instead (ARCH-18); a macro for it would be unused.
        gates = [{"name": gate, "macro": SCG_SOURCE_MACROS[src]} for gate, src in p.peripheral_source.items()
                 if gate in nxclock.S32K1_DRIVER_GATES]
        peripherals = []
        # PLAT-12: the clock gates of the derivative (mcudb clock.pcc, e.g. LPIT) get their frequency as well -
        # the ARMv6-M system timer counts LPIT ticks instead of core cycles (there is no DWT on a Cortex-M0+).
        for inst in list(c.derivative.instances) + [g for g in c.derivative.clock.pcc
                                                    if g not in c.derivative.instances]:
            hz = p.peripheral_hz.get(inst)
            if hz is None or hz.denominator != 1:
                continue
            gate = "FLEXIO" if inst == "FlexIO" else inst
            if gate in p.peripheral_output:
                source = p.peripheral_output[gate]      # e.g. FIRCDIV2 (ARCH-18: peripheral_output, no table)
                if p.peripheral_origin.get(gate) == "project":
                    source += " (peripheral_clocks)"    # chosen by the project, not the derivative's default
            else:
                source = "SOSCDIV2 (FlexCAN CTRL1.CLKSRC = 0)"
            peripherals.append({"name": inst.upper(), "hz": int(hz), "source": source})
        # ARCH-22: divider of every asynchronous output (clock model); an output of a source that is not started
        # (no crystal, no PLL) keeps divider 1 - the driver does not configure that source at all
        limits = c.derivative.clock.output_max_hz.max_hz if c.derivative.clock.output_max_hz is not None else {}
        outputs = []
        for src in SCG_ASYNC_OUTPUT_SOURCES:
            for n in nxclock.S32K1_ASYNC_OUTPUTS:
                name = f"{src}DIV{n}"
                divider = p.output_dividers.get(name, 1)
                hz = p.outputs.get(name)
                if hz is None:
                    comment = f"{src} not started"
                else:
                    comment = f"/ {divider} = {float(hz):.1f} Hz".replace(".0 Hz", " Hz")
                    comment += f", at most {limits[name]} Hz" if name in limits else ", no limit in the derivative"
                outputs.append({"name": name, "code": SCG_ASYNC_DIVIDER_CODES[divider], "comment": comment})
        self.clock = {
            "source": p.system_source, "scs": SCG_SOURCE_CODES[p.system_source],
            "core_hz": int(p.domains["core"]), "bus_hz": int(p.domains["bus"]), "slow_hz": int(p.domains["slow"]),
            "slow_exact": f"{float(p.domains['slow']):.1f} Hz",
            "divcore": p.dividers["core"], "divbus": p.dividers["bus"], "divslow": p.dividers["slow"],
            "firc_hz": int(p.sources["FIRC"]), "sirc_hz": int(p.sources["SIRC"]),
            "sosc_hz": p.oscillator_hz, "outputs": outputs,
            "pll": None if pll is None else {
                "input_divider": pll.input_divider, "multiplier": pll.multiplier, "output_divider": pll.output_divider,
                "input_hz": f"{float(pll.input_hz):.1f}".removesuffix(".0"), "vco_hz": int(pll.vco_hz),
                "output_hz": int(pll.output_hz)},
            "gates": gates, "peripherals": peripherals,
        }
        self.can = []
        phy_of = {b.name: b for b in (c.integration.can if c.integration is not None else [])}
        for index, bus in enumerate(c.selected("can")):
            t, use = p.can[bus.name], c.project.can[bus.name]
            ctrl1 = (((t.prescaler - 1) << 24) | ((t.sjw - 1) << 22) | ((t.phase_seg1 - 1) << 19)
                     | ((t.phase_seg2 - 1) << 16) | (t.prop_seg - 1))
            ident = identifier(bus.name)
            entry = {"name": bus.name, "ident": ident, "instance": bus.instance, "t": t,
                     "bitrate": use.bitrate, "quanta": t.quanta, "ctrl1": ctrl1,
                     "sample_point": f"{float(t.sample_point * 100):g} %",
                     "requested": f"{use.sample_point_percent:g} %",
                     "sample_point_bp": int(t.sample_point * 10000), "verify": use.verify,
                     # COM-37: project can.<bus>.bus_off, left out = the defaults of the schema (DEFAULT_CONFIG)
                     "bus_off": (use.bus_off or nxconfig.model.CanBusOff()).model_dump(exclude={"note"}),
                     "bus_off_set": use.bus_off is not None}
            entry.update(self._can_device(bus, ident, index, phy_of.get(bus.name)))
            self.can.append(entry)
        self.uart = []
        for index, bus in enumerate(c.selected("uart")):
            u, baud = p.uart[bus.name], c.project.uart[bus.name].baudrate
            bothedge = u.oversampling < 8
            register = ((u.oversampling - 1) << 24) | u.divider | (LPUART_BOTHEDGE if bothedge else 0)
            entry = {"name": bus.name, "ident": identifier(bus.name), "instance": bus.instance,
                     "baudrate": baud, "clock_hz": u.clock_hz, "osr": u.oversampling, "sbr": u.divider,
                     "bothedge": bothedge, "register": register, "actual": f"{float(u.baudrate):.1f}",
                     "error": f"{float(u.error(baud) * 100):+.2f}"}
            entry.update(self._uart_device(bus, entry["ident"], index))
            self.uart.append(entry)
        # Console of the ECU (stdin/stdout, Uart::Id::TERMINAL of the board port): the first used UART in board
        # order. The project chooses it through use.uart; there is no console flag in the project schema yet
        # (proposal in tools/nxconfig/uart.py, which also warns when the choice is not obvious).
        self.uart_console = self.uart[0] if self.uart else None

    def collect_peripheral_clocks(self):
        """ARCH-18: the PCC writes of bsp/clock/peripheralClocks.h - one entry per used peripheral instance.

        Three groups, in the order the OpenBSW clock driver writes them: the infrastructure gates without a clock
        select (S32K1_INFRASTRUCTURE_GATES), the clock gates of the derivative that are no instance (mcudb
        clock.pcc, s32k1xx LPIT/LPTMR0 - the system timer needs LPIT0) and the instances the project uses. An
        unused peripheral keeps its clock switched off; its CLOCK_SOURCE macro in boardClock.h stays there as
        long as the board port calls enablePeripheralClocks() of the reference driver.
        """
        p, c = self.plan, self.config
        used = nxclock.used_instances(c)
        gates = list(c.derivative.clock.pcc) if c.derivative.clock is not None else []
        entries, seen = [], set()

        def add(gate: str, guard: bool, comment: str):
            # Two spellings: the clock driver names the gate FLEXIO (CLOCK_SOURCE_FLEXIO), the device header
            # names the register index after the instance (PCC_FlexIO_INDEX) - clockConfig.cpp uses both.
            name = "FLEXIO" if gate == "FlexIO" else gate
            if name in seen:
                return                                 # an instance may be used by several board entries
            seen.add(name)
            pcs, output = None, None
            if name in p.peripheral_source:
                output = p.peripheral_output[name]
                pcs = SCG_SOURCE_CODES[p.peripheral_source[name]]
                origin = ("project.yaml peripheral_clocks" if p.peripheral_origin.get(name) == "project"
                          else "default of the derivative")
                comment += f", {output} ({origin})"
            elif gate in c.derivative.instances:
                # no clock select: FlexCAN (CTRL1.CLKSRC), an instance on the bus or the core clock
                comment += ", no clock select (PCS)"
            hz = p.peripheral_hz.get(gate)
            entries.append({
                "gate": name, "index": gate, "guard": guard, "pcs": pcs, "output": output, "comment": comment,
                "hz": int(hz) if hz is not None and hz.denominator == 1 else None,
                "value": "NX_PCC_CGC" if pcs is None else f"({pcs}UL << {PCC_PCS_SHIFT}) | NX_PCC_CGC"})

        for gate, guard in S32K1_INFRASTRUCTURE_GATES:
            add(gate, guard, "infrastructure of the family driver (clockConfig.cpp)")
        for gate in gates:
            add(gate, False, "clock gate without an instance (mcudb clock.pcc)")
        for gate, (what, _) in used.items():
            add(gate, False, f"used by {what}")
        self.peripheral_clocks = {
            "entries": entries,
            "driver_gates": [e["gate"] for e in entries if e["pcs"] is not None
                             and e["gate"] not in nxclock.S32K1_DRIVER_GATES],
        }
        # Until the board port calls nxEnablePeripheralClocks() instead of enablePeripheralClocks() of the
        # OpenBSW driver, a used instance outside the driver's gate list gets no clock at the board (ARCH-09 B5,
        # PLAT-22 warning). The generated configuration is complete; the integration is not.
        for gate in self.peripheral_clocks["driver_gates"]:
            self.warnings.append(
                f"{gate} is used and its clock is in the generated bsp/clock/peripheralClocks.h, but the s32k1xx "
                f"clock driver of OpenBSW (clockConfig.cpp enablePeripheralClocks) does not write PCC_{gate}: "
                f"the clock stays off until the board port calls nxEnablePeripheralClocks() (ARCH-18)")

    def _uart_device(self, bus, ident: str, index: int) -> dict:
        """LPUART device of one used UART: instance pointer, receive interrupt, handler and interrupt level."""
        match = LPUART_RE.match(bus.instance)
        if match is None:
            raise ConfigError(f"UART {bus.name}: instance {bus.instance} is no LPUART "
                              f"(the s32k1xx driver bsp::Uart drives LPUART)")
        n = int(match.group(1))
        # NVIC level of the interrupt rank of this UART (project interrupts.ranks, PLAT-21), resolved here so
        # that the UART configuration needs no further header. Without a rank the undefined macro
        # NX_IRQ_LEVEL_UART_<uart> is generated on purpose: the board port then stops at a name that says what
        # is missing, and tools/nxconfig/uart.py warns about the entry with its YAML path before that.
        rank, level = self.irq_levels.get(("uart", bus.name), (None, None))
        return {
            "index": index,
            "instance_index": n,
            "regs": f"IP_LPUART{n}",
            "irq": f"LPUART{n}_RxTx_IRQn",
            "isr": f"LPUART{n}_RxTx_IRQHandler",
            "rank": rank,
            "level": f"{level}U" if level is not None else f"NX_IRQ_LEVEL_UART_{ident}",
        }

    def _can_mb_vectors(self, bus, n: int, split_of: CanBufferSplit | None = None) -> list[dict]:
        """PLAT-12: message buffer interrupt vectors of one FlexCAN instance, as the derivative has them.

        The S32K14x/S32K148 split them (buffers 0..15 receive, 16..31 transmit), the S32K11x has **one** vector
        for all 32 buffers. `serves` says what the integration has to do in that vector (RX, TX or RX_TX); the
        board port defines one macro per kind, so a combined vector gets one handler doing both.
        """
        irqs = set(flexcan_mb_irqs(self.config, bus, self.irqs))
        split = [f"CAN{n}_ORed_0_15_MB", f"CAN{n}_ORed_16_31_MB"]
        combined = f"CAN{n}_ORed_0_31_MB"
        if all(f"{name}_IRQn" in irqs for name in split):
            # COM-26: the vectors only separate receive and transmit if the driver transmits from buffer 16 on
            if split_of is not None and split_of.first_transmit != 16:
                raise ConfigError(f"CAN {bus.name} ({bus.instance}): {split_of.buffers} message buffers transmit from "
                                  f"buffer {split_of.first_transmit}, but the derivative splits the interrupts at "
                                  f"buffer 16 ({split[0]}, {split[1]})")
            return [{"serves": "RX", "name": split[0]}, {"serves": "TX", "name": split[1]}]
        if f"{combined}_IRQn" in irqs:
            return [{"serves": "RX_TX", "name": combined}]
        if f"{split[0]}_IRQn" in irqs:
            # ARCH-42/COM-26: an instance with 16 buffers has only the vector of buffers 0..15 (S32K144 FlexCAN1/2),
            # it serves receive and transmit; FlexCANDevice of the family port only reports a sent frame when the
            # flag of its callback buffer is set, so the shared handler may call both parts.
            return [{"serves": "RX_TX", "name": split[0]}]
        raise ConfigError(f"CAN {bus.name} ({bus.instance}): the device header of "
                          f"{self.config.derivative.derivative.name} has neither the split message buffer "
                          f"interrupts {', '.join(split)} nor the combined {combined}")

    def _can_device(self, bus, ident: str, index: int, phy: "nxchecks.CanBusUse | None") -> dict:
        """FlexCAN device of one used CAN bus: instance, register base, interrupt vectors, pins, bus id."""
        match = FLEXCAN_RE.match(bus.instance)
        if match is None:
            raise ConfigError(f"CAN {bus.name}: instance {bus.instance} is no FlexCAN "
                              f"(the s32k1xx driver CanFlex2Transceiver drives FlexCAN)")
        n = int(match.group(1))
        if n > FLEXCAN_MAX_INSTANCE:
            raise ConfigError(f"CAN {bus.name}: FlexCAN instance {n} is outside the interrupt dispatch table "
                              f"of CanFlex2Transceiver (0..{FLEXCAN_MAX_INSTANCE})")
        # ARCH-42: an instance with fewer message buffers than the family driver needs; nxconfig rejects it with its
        # YAML path before this generator runs. Last line of defence, same text.
        too_few = nxchecks.can_message_buffers_problem(self.config, bus)
        if too_few is not None:
            raise ConfigError(too_few)
        split_of = can_buffer_split(self.config, bus)     # COM-26: per instance
        # Every bus needs the NVIC level of its message buffer interrupts (NX_IRQ_LEVEL_CAN_<bus>). nxconfig rejects
        # a used bus without rank with its YAML path before this generator runs; last line of defence, same text.
        core = getattr(self, "core", None)          # set by collect_interrupts(), which collect() runs first
        if core is not None and core["controller"] == "nvic" and ("can", bus.name) not in self.irq_levels:
            raise ConfigError(nxchecks.can_rank_missing(bus.name))
        if index >= CAN_WAKEUP_COUNT:
            raise ConfigError(f"CAN {bus.name}: bus index {index} has no wake-up bit "
                              f"(at most {CAN_WAKEUP_COUNT} CAN buses, ecuState/EcuState.h wakeup::canBus)")
        return {
            "index": index,
            "bus_id": BUSID_SELFDIAG + 1 + index,
            "instance_index": n,
            "base": f"IP_FLEXCAN{n}_BASE",
            "mb_vectors": [{"serves": v["serves"], "irq": f"{v['name']}_IRQn",
                            "isr": f"{v['name']}_IRQHandler"} for v in self._can_mb_vectors(bus, n, split_of)],
            "io_rx": f"{ident}_RX",
            "io_tx": f"{ident}_TX",
            "message_buffers": split_of.buffers,
            "rx_bufs_std": split_of.rx_std,
            "rx_bufs_ext": split_of.rx_ext,
            "tx_bufs_app": split_of.tx_app,
            "wakeup": 1 << (CAN_WAKEUP_SHIFT + index),
            "phy_bus": 0,   # index of this bus at its transceiver (board schema has no roles yet, ARCH-05)
            "phy": None if phy is None else phy.phy,
            "phy_module": None if phy is None else phy.module,
        }

    def collect_busids(self):
        """Bus ids of ecu/configuration: SELFDIAG, the CAN buses of the project in use order, Ethernet."""
        eth = {name: BUSID_SELFDIAG + 1 + len(self.can) + i for i, name in enumerate(BUSID_ETHERNET)}
        self.busid = {"selfdiag": BUSID_SELFDIAG, "can": self.can, "ethernet": eth,
                      "last": max([BUSID_SELFDIAG, *eth.values()])}
    def collect_interrupts(self):
        """PLAT-21: NVIC priority bits, RTOS syscall level and the level of every interrupt rank of the project.

        The rank -> level arithmetic and the checks live in nxconfig (checks.interrupt_priorities); here the values
        are only turned into macros for the board port (interrupts/interruptPriorities.h). ARCH-24: the core
        architecture and controller for mcu/derivativeFacts.h; a VIM core gets channels (vim_interrupt_context).
        """
        c = self.config
        self.core = core_context(c)
        self.irq_levels.clear()
        if self.core["controller"] == "vim":
            # ARCH-24: no levels on a VIM; the family drivers of s32k1xx (NVIC) find no entry in irq_levels
            self.irq = vim_interrupt_context(c)
            return
        prio = nxchecks.interrupt_priorities(c)
        if prio is None:
            raise ConfigError(f"{self.mcu}: derivative has no nvic_priority_bits in mcudb, the interrupt priorities "
                              "of the board port cannot be generated")
        entries = []
        for r in c.project.interrupts.ranks:
            name = f"{r.source.upper()}_{identifier(r.name)}"
            self.irq_levels[(r.source, r.name)] = (r.rank, prio.level(r.source, r.name))
            entries.append({"macro": f"NX_IRQ_LEVEL_{name}", "rank_macro": f"NX_IRQ_RANK_{name}",
                            "source": r.source, "name": r.name, "rank": r.rank,
                            "level": prio.level(r.source, r.name), "note": r.note})
        self.irq = {
            "derivative": self.mcu, "core": c.derivative.derivative.core, "bits": prio.bits,
            "max_syscall": prio.max_syscall_level, "levels": 1 << prio.bits, "rank_count": prio.rank_count,
            "default_syscall": c.project.interrupts.max_syscall_level is None, "entries": entries,
            "controller": "nvic",
        }

    def collect_safe_outputs(self):
        """IO-32: register writes that switch the power outputs off, first action of the fault handlers.

        The watchdog and hard fault handlers (platforms/s32k1xx/hardFaultHandler, customHardFaultHandler) work
        through this table before the dump and before any UART output, without stack, RAM or driver objects. Order:
        first every PWM group of the project (FTM software output control: all channels of the group forced to their
        inactive level, SWOCTRL = CHnOC of the group, CHnOCV 0 - [RM] 47.5.15; acts before dead time insertion,
        output mask and polarity, so an active-low channel goes to its inactive level as well), then every output a
        module connection declares safe_off (module.yaml connect.<name>.safe_off, e.g. DRV_EN of a gate driver):
        GPIO PCOR for an active-high output, PSOR for an active-low one. Each entry names the PCC register of its
        peripheral: the handler skips an entry whose clock is gated (an access would bus fault inside the fault
        handler, e.g. a fault before the BSP init). Empty without power outputs; the handler then writes nothing.
        """
        entries = []
        pwm_groups = 0
        # SYS-116: the safe states of every used PWM group (tools/nxconfig/safestate.py). Only a project in which a
        # group allows an active short gets the value sets; every other project gets exactly the table it had.
        plan = getattr(self.config, "safe_states", None)
        allowed = {g.name: g.allowed for g in plan.groups} if plan is not None else {}
        self.safe_select = plan is not None and plan.select
        group_of = {}                                       # PWM group -> (selection word + 1, timer, states)
        for grp, _ in self.config.selected_pwm():
            pwm_groups += 1
            if not FTM_RE.match(grp.instance):
                raise ConfigError(f"PWM {grp.name}: instance {grp.instance} is no FlexTimer (safe outputs use SWOCTRL)")
            mask = sum(1 << ch.channel for ch in grp.channels)
            entry = {"name": identifier(grp.name), "what": f"PWM group on {grp.instance}: SWOCTRL",
                     "address": f"IP_{grp.instance}_BASE + offsetof(FTM_Type, SWOCTRL)",
                     "value": f"0x{mask:02X}U", "gate": grp.instance,
                     # SYS-30: read back the output control itself - CHnOC and CHnOCV of the group's channels
                     "readback": {"address": f"IP_{grp.instance}_BASE + offsetof(FTM_Type, SWOCTRL)",
                                  "mask": f"0x{mask | (mask << 8):04X}U", "expected": f"0x{mask:02X}U"}}
            if self.safe_select:
                # SYS-116 (concept part C, [RM] 47.5.15): SWOCTRL = CHnOC of every channel of the group (bits 7..0)
                # and CHnOCV (bits 15..8) = 1 for the channels of one side. CHnOCV is the value before dead-time
                # insertion, output mask and polarity, so 1 is the active level of the channel whatever POL says,
                # and a low side comes on only after the dead time behind its high side.
                by_name = {ch.name: ch.channel for ch in grp.channels}
                low = sum(1 << by_name[p.low_side] for p in grp.pairs)
                high = sum(1 << by_name[p.high_side] for p in grp.pairs)
                states = allowed.get(grp.name, ("freewheel",))
                group_of[grp.name] = (pwm_groups, grp.instance, states)
                entry.update(group=pwm_groups, group_gate=grp.instance,
                             short_low=f"0x{mask | (low << 8):04X}U" if "active_short_low" in states
                             else entry["value"],
                             short_high=f"0x{mask | (high << 8):04X}U" if "active_short_high" in states
                             else entry["value"])
            entries.append(entry)
        outputs = {o.name: o for o in self.config.selected("dio.outputs")}
        manifests = self.config.modules.manifests
        seen = set()
        self.safe_off_pins: list[tuple[str, str]] = []     # SYS-24: (Io name, what) of every safe_off output
        for dev in self.config.integration.devices:
            manifest = manifests.get(dev.module)
            if manifest is None:
                continue
            for key, spec in manifest.connect.items():
                if not spec.safe_off or key not in dev.connect:
                    continue
                name = dev.connect[key]
                out = outputs.get(name)
                if out is None:  # rejected by nxconfig (connection to an unused output); guard for the table
                    raise ConfigError(f"{dev.name}.{key}: safe_off output {name} is not a used dio output")
                if name in seen:
                    continue
                seen.add(name)
                self.safe_off_pins.append((identifier(name), f"{dev.name}.{key} ({dev.module})"))
                pin = PIN_RE.match(out.pin)
                port, number = pin.group(1), int(pin.group(2))
                low = out.active == "low"
                entry = {"name": identifier(name),
                         "what": f"{dev.name}.{key} ({dev.module}) on {out.pin}, active "
                                 f"{'low: set' if low else 'high: clear'}",
                         "address": f"IP_PT{port}_BASE + offsetof(GPIO_Type, {'PSOR' if low else 'PCOR'})",
                         "value": f"(1UL << {number}U)", "gate": f"PORT{port}",
                         # SYS-30: read back the pin level (PDIR, input kept enabled by the SafeIo list, PIDR):
                         # it also sees a pin held by something else, which the data register would not
                         "readback": {"address": f"IP_PT{port}_BASE + offsetof(GPIO_Type, PDIR)",
                                      "mask": f"(1UL << {number}U)",
                                      "expected": f"(1UL << {number}U)" if low else "0U"}}
                if self.safe_select:
                    # SYS-116 (decision C2 (a)): the enable of a gate driver bound to a PWM group (IO-45,
                    # DeviceUse.pwm) follows the group's selection - off in freewheel, left as it is in a short. "Left
                    # as it is" is a store of 0 to the set/clear register: it changes no pin, and the walk keeps
                    # exactly one store per entry. nxconfig refuses an enable of a shorted group that another
                    # connection uses too (safestate.py).
                    bound = group_of.get(getattr(dev, "pwm", None))
                    if bound is None:
                        entry.update(group=0, group_gate=None, short_low=entry["value"], short_high=entry["value"])
                    else:
                        word, timer, states = bound
                        entry.update(group=word, group_gate=timer,
                                     short_low="0U" if "active_short_low" in states else entry["value"],
                                     short_high="0U" if "active_short_high" in states else entry["value"])
                entries.append(entry)
        self.safe_outputs = entries
        # SYS-116: per used PWM group its selection word, allowed states and start test (NX_SAFE_STATE_GROUPS)
        self.safe_state_groups = []
        if self.safe_select:
            bits = {"freewheel": 0, "active_short_low": 1, "active_short_high": 2}  # nxconfig safestate.STATE_BIT
            for g in plan.groups:
                self.safe_state_groups.append({
                    "name": identifier(g.name), "index": len(self.safe_state_groups),
                    "allowed": f"0x{sum(1 << bits[s] for s in g.allowed):X}U", "test": 1 if g.test else 0,
                    "test_us": g.test_us if g.test and g.test_us is not None else 0,
                    "states": ", ".join(g.allowed)})
        # IO-32 (user decision 2026-09-19, question 6): the outputs of the second block - every safe_off output of
        # a module instance. The halt deadman of the debug halt guard switches them off behind the back of their
        # driver, so each of them needs an integration::IPowerStageOutput adapter; the integration checks its list
        # of adapters against this count (NX_SAFE_OFF_OUTPUT_COUNT).
        self.safe_off_outputs = len(entries) - pwm_groups

    def collect_safe_io(self):
        """SYS-24 (finding F4, doc/safety/fmea.md 5.2): the register list SafeIo locks and checks.

        Everything is derived from what the configuration already has - no new configuration value:
        - PIN_FUNCTION (lock at start, read-back, cyclic check): the pin control register of every pin of a PWM group
          the project uses, of every safe_off output (the same derivation as the safe outputs table) and of the role
          pin safety.locked_pin. Expected: the function and attributes of the pin table (ioConfiguration.h) plus LK.
          A remuxed pin is out of reach of the safe outputs walk; the lock makes a later write ineffective.
        - GPIO_DIRECTION (cyclic): PDDR (output/input as in the pin table) and PIDR (input not disabled) of the GPIO
          pins of that list, one word per port and register.
        - CLOCK_GATE (cyclic): PCC CGC of the timer of every PWM group and of the port of every pin of that list (a
          gated clock makes the walk skip the peripheral, SM-23). The clock select is not judged: the PWM counts the
          system clock (SC.CLKS = 01) and a port has none.
        - PWM_TIMER (cyclic): the FTM registers ComplementaryPwm writes once in init() - SC, MOD, CNTIN, COMBINE, POL,
          INVCTRL, CONF.BDMMODE and PAIRnDEADTIME of the used pairs - with the values of ftmPwmConfiguration.h.
        - SUPERVISION (cyclic, SYS-153): PMC_LVDSC1 of the family port - low-voltage detect reset on, its interrupt
          off (mask 0x30, expected 0x10), read with an 8-bit access. In every project: boardInit() writes it in every
          start, whatever the configuration.
        Every entry has an access width ("width": 32 or 8 bits; 32 if not given).
        System clock (SCG) and write protection of the FTM are follow-ups; watchdog control and LPIT deadman are
        checked by SYS-41 (checkWatchdogControl, checkLpitDeadman).
        Must run after collect() (pin table), collect_pwm() and collect_safe_outputs().
        """
        entries: list[dict] = []
        locked: dict[str, str] = {}              # Io name -> why it is locked (first reason wins)
        for grp, _ in self.config.selected_pwm():
            for ch in grp.channels:
                locked.setdefault(identifier(ch.name), f"PWM group {grp.name} ({grp.instance} CH{ch.channel})")
        for ident, what in self.safe_off_pins:
            locked.setdefault(ident, f"safe_off output {what}")
        if self.safety is not None:
            locked.setdefault(self.safety["locked_pin"], "role pin safety.locked_pin")
            indicator = self.safety["safe_state_output"]
            if indicator in locked:
                raise ConfigError(f"safety.safe_state_output: {indicator} is switched by the safe state (its pin "
                                  f"control register is written), but it is locked as {locked[indicator]}; use "
                                  f"another output as indicator")

        def pcr_value(pin: Pin) -> int:
            value = pin.mux << 8
            for token in re.findall(r"\(uint32_t\)\s*(\w+)", pin.extra_cfg):
                if token not in SAFE_IO_PCR_ATTRIBUTES:
                    raise ConfigError(f"{pin.name}: pin attribute {token} has no PCR value for the register list of "
                                      f"SafeIo (known: {', '.join(SAFE_IO_PCR_ATTRIBUTES)})")
                value |= SAFE_IO_PCR_ATTRIBUTES[token]
            return value

        ports: dict[str, dict[str, list[int]]] = {}  # port -> {"out": bits, "in": bits}
        gates: list[str] = []
        for ident, why in locked.items():
            pin = self.pins.get(ident)
            if pin is None:  # every entry above is a used pin of the table; guard for the generated list
                raise ConfigError(f"register list of SafeIo: {ident} ({why}) is not in the pin table")
            value = pcr_value(pin) | SAFE_IO_PCR_LK
            entries.append({"cls": "PIN_FUNCTION", "name": ident,
                            "what": f"PCR {pin.pin} {ALT_NAMES[pin.mux]}: {why}",
                            "address": f"IP_PORT{pin.port}_BASE + offsetof(PORT_Type, PCR) + 4UL * {pin.number}U",
                            "mask": SAFE_IO_PCR_MASK, "expected": value & SAFE_IO_PCR_MASK,
                            "lock_bits": SAFE_IO_PCR_LK, "lock_clear": SAFE_IO_PCR_ISF})
            if pin.mux == 1:
                bits = ports.setdefault(pin.port, {"out": [], "in": []})
                bits["out" if pin.direction == "_OUT" else "in"].append(pin.number)
            if f"PORT{pin.port}" not in gates:
                gates.append(f"PORT{pin.port}")
        for port in sorted(ports):
            out, inp = ports[port]["out"], ports[port]["in"]
            mask = sum(1 << n for n in out + inp)
            names = ", ".join(f"PT{port}{n}" for n in sorted(out + inp))
            entries.append({"cls": "GPIO_DIRECTION", "name": f"PT{port}_PDDR", "what": f"PDDR of {names}",
                            "address": f"IP_PT{port}_BASE + offsetof(GPIO_Type, PDDR)",
                            "mask": mask, "expected": sum(1 << n for n in out), "lock_bits": 0, "lock_clear": 0})
            entries.append({"cls": "GPIO_DIRECTION", "name": f"PT{port}_PIDR", "what": f"PIDR of {names}",
                            "address": f"IP_PT{port}_BASE + offsetof(GPIO_Type, PIDR)",
                            "mask": mask, "expected": 0, "lock_bits": 0, "lock_clear": 0})
        pwm = self.pwm
        if pwm is not None:
            gates.insert(0, pwm["instance"])
        for g in gates:
            entries.append({"cls": "CLOCK_GATE", "name": f"PCC_{g}", "what": f"PCC {g}: CGC",
                            "address": f"IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_{g}_INDEX",
                            "mask": PCC_CGC_MASK, "expected": PCC_CGC_MASK, "lock_bits": 0, "lock_clear": 0})
        if pwm is not None:
            inst = pwm["instance"]
            dt = (pwm["deadtime_dtps"] << 6) | pwm["deadtime_ticks"]
            if pwm["center"]:           # IO-61: up-down counting, complementary pairs without COMBINE
                sc_extra, sc_what = FTM_SC_CPWMS, ", CPWMS"
                mod_what = f"turning point {pwm['counter_top']} (period {pwm['period_ticks']} ticks)"
                combine_pair, combine_what = FTM_COMBINE_PAIR_CENTER, "COMP, DTEN, SYNCEN of the used pairs"
            else:
                sc_extra, sc_what = 0, ""
                mod_what = f"period {pwm['period_ticks']} ticks"
                combine_pair, combine_what = FTM_COMBINE_PAIR, "COMBINE, COMP, DTEN, SYNCEN of the used pairs"
            ftm = [("SC", FTM_SC_MASK,
                    FTM_SC_CLKS_SYSTEM | sc_extra | pwm["prescaler_shift"] | (pwm["channel_mask"] << 16),
                    f"CLKS system clock{sc_what}, PS {pwm['prescaler_shift']}, PWMEN 0x{pwm['channel_mask']:02X}"),
                   ("MOD", 0xFFFF, pwm["counter_top"], mod_what),
                   ("CNTIN", 0xFFFF, 0, "counter start 0"),
                   ("COMBINE", FTM_COMBINE_MASK, sum(combine_pair << (8 * q["index"]) for q in pwm["pairs"]),
                    combine_what),
                   ("POL", 0xFF, pwm["pol"], "active-low channels"),
                   ("INVCTRL", FTM_INVCTRL_MASK, 0, "no inverted pair"),
                   ("CONF", FTM_CONF_BDMMODE_MASK, FTM_CONF_BDMMODE_SAFE, "BDMMODE: outputs safe in debug halt")]
            for q in pwm["pairs"]:
                ftm.append((f"PAIR{q['index']}DEADTIME", FTM_PAIR_DEADTIME_MASK, dt,
                            f"dead time {pwm['deadtime_actual_ns']} ns (DTPS {pwm['deadtime_dtps']}, "
                            f"DTVAL {pwm['deadtime_ticks']})"))
            for reg, mask, value, what in ftm:
                entries.append({"cls": "PWM_TIMER", "name": f"{pwm['ident']}_{reg}",
                                "what": f"{inst} {reg}: {what}",
                                "address": f"IP_{inst}_BASE + offsetof(FTM_Type, {reg})",
                                "mask": mask, "expected": value, "lock_bits": 0, "lock_clear": 0})
        # SYS-153: the low-voltage detect reset boardInit() switched on (SYS-125) - a bit flip that clears LVDRE (or
        # sets LVDIE) is seen within one safety cycle instead of at the next start
        entries.append({"cls": "SUPERVISION", "name": "PMC_LVDSC1",
                        "what": "PMC LVDSC1: low-voltage detect reset on (LVDRE), its interrupt off (LVDIE), 8 bit",
                        "address": SAFE_IO_PMC_LVDSC1_ADDRESS, "mask": SAFE_IO_PMC_LVDSC1_MASK,
                        "expected": SAFE_IO_PMC_LVDSC1_EXPECTED, "lock_bits": 0, "lock_clear": 0, "width": 8})
        for e in entries:
            if e["expected"] & ~e["mask"]:
                raise ConfigError(f"register list of SafeIo: {e['name']}: expected 0x{e['expected']:X} outside the "
                                  f"mask 0x{e['mask']:X}")
            width = e.setdefault("width", 32)
            if width not in SAFE_IO_WIDTHS or (e["mask"] | e["lock_clear"]) >> width:
                raise ConfigError(f"register list of SafeIo: {e['name']}: mask 0x{e['mask']:X} does not fit an access "
                                  f"of {width} bits (widths: {', '.join(map(str, SAFE_IO_WIDTHS))})")
        self.safe_io = entries

    def collect_hw_threshold(self):
        """SYS-86: register values of the hardware threshold (comparator -> fault input of the PWM timer).

        The plan is nxconfig's (tools/nxconfig/hwthreshold.py: DAC level, inversion, fault input, filter); here it
        becomes s32k1xx register values for bsp/hwThreshold/hwThresholdConfiguration.h (family code
        platforms/s32k1xx/bsp/bspHwThreshold) and for the FTM configuration of the group (ftmPwmConfiguration.h:
        MODE, COMBINE, FLTCTRL, FLTPOL). The registers join the register list of SafeIo (SM-22): the COMBINE entry of
        the group expects the FAULTEN bits, and the timer's fault control, the trigger mux, the fault source select
        and the comparator are checked in every safety cycle - a comparator switched off reads as "not exceeded".
        Must run after collect_pwm() and collect_safe_io(). Nothing changes for a project without a threshold.
        """
        plans = list(getattr(self.config, "hw_thresholds", None) or [])
        if not plans:
            return
        if len(plans) > 1:
            raise ConfigError(f"hw_thresholds: {len(plans)} thresholds ({', '.join(p.name for p in plans)}); the "
                              f"s32k1xx family has one comparator (CMP0)")
        t = plans[0]
        where = f"hardware threshold {t.name}"
        pwm = self.pwm
        if pwm is None or pwm["name"] != t.pwm:
            raise ConfigError(f"{where}: PWM group {t.pwm} is not the group of the ComplementaryPwm driver")
        timer = FTM_INSTANCE_RE.match(t.timer)
        if t.instance != "CMP0" or timer is None or not 0 <= t.fault_input <= 2:
            raise ConfigError(f"{where}: {t.instance} -> {t.timer} fault input {t.fault_input} is no route of the "
                              f"s32k1xx family (CMP0 -> TRGMUX_FTMx SEL1..3 -> fault input 0..2)")
        if t.dac_bits != 8 or not 0 <= t.dac_level <= 0xFF or not 0 <= t.input_index <= 7:
            raise ConfigError(f"{where}: DAC level {t.dac_level} / input IN{t.input_index} do not fit CMP0 "
                              f"(8 bit VOSEL, 3 bit PSEL)")
        if not 0 <= t.filter_ticks <= 15:
            raise ConfigError(f"{where}: filter {t.filter_ticks} clocks does not fit FLTCTRL[FFVAL] (0..15)")
        ftm_index = int(timer.group(1))
        f = t.fault_input
        c0 = CMP_C0_EN | CMP_C0_PMODE | (CMP_C0_INVT if t.pin_trip == "below" else 0)
        c1 = (CMP_C1_INPSEL_MUX | CMP_C1_DACEN | (CMP_C1_VRSEL if t.reference == "bandgap" else 0)
              | (t.input_index << CMP_C1_PSEL_SHIFT) | t.dac_level)
        sel_shift = 8 * (f + 1)
        trgmux_mask = TRGMUX_SEL_MASK << sel_shift
        trgmux_value = TRGMUX_CMP0_OUT << sel_shift
        sim_bit = 1 << (4 * ftm_index + f)
        fltctrl = (1 << f) | ((1 << (4 + f)) | (t.filter_ticks << 8) if t.filter_ticks else 0)
        faulten = sum(FTM_COMBINE_FAULTEN << (8 * q["index"]) for q in pwm["pairs"])
        self.hw_threshold = {
            "name": t.name, "ident": identifier(t.name), "input": t.input, "instance": t.instance,
            "input_index": t.input_index, "pin": t.pin, "signal": t.signal, "unit": t.unit, "trip": t.trip,
            "pin_trip": t.pin_trip, "threshold": t.threshold, "reference": t.reference,
            "vin_mv": f"{float(t.vin_mv):g}", "dac_level": t.dac_level, "dac_bits": t.dac_bits,
            "pin_mv": f"{float(t.pin_mv):.1f}", "dac_mv": f"{float(t.dac_mv):.1f}",
            "realised": f"{float(t.realised):.0f}", "band": [f"{float(v):.0f}" for v in t.band],
            "supply_open": t.supply_tolerance_open, "pwm": t.pwm, "timer": t.timer, "ftm_index": ftm_index,
            "fault_input": f, "filter_ns": t.filter_ns, "filter_ticks": t.filter_ticks,
            "reaction_ns": None if t.reaction_max_ns is None else f"{float(t.reaction_max_ns):.0f}",
            "propagation_ns": t.propagation_max_ns, "settle_us": t.settling_max_us, "confirmed": t.confirmed,
            "cmp_c0": c0, "cmp_c1": c1, "trgmux_mask": trgmux_mask, "trgmux_value": trgmux_value,
            "sim_bit": sim_bit, "ftm_mode": FTM_MODE_FAULTM_MANUAL_ALL, "ftm_fltctrl": fltctrl, "ftm_fltpol": 0,
            "ftm_faulten": faulten,
        }
        # register list of SafeIo (SM-22), same classes as the timer of the group
        ident, inst = pwm["ident"], pwm["instance"]
        combine = next((e for e in self.safe_io if e["name"] == f"{ident}_COMBINE"), None)
        if combine is None:
            raise ConfigError(f"{where}: register list of SafeIo has no COMBINE entry of {pwm['name']}")
        combine["expected"] |= faulten
        combine["what"] += "; FAULTEN of the used pairs (hardware threshold)"
        name = identifier(t.name)
        ftm = [("MODE", FTM_MODE_FAULTM_MASK, FTM_MODE_FAULTM_MANUAL_ALL, "FAULTM: fault control on all channels, "
                                                                          "manual clearing"),
               ("FLTCTRL", FTM_FLTCTRL_MASK, fltctrl, f"fault input {f} enabled, filter {t.filter_ticks} clocks, "
                                                      f"outputs to POL on a fault"),
               ("FLTPOL", FTM_FLTPOL_MASK, 0, "fault inputs active high")]
        for reg, mask, value, what in ftm:
            self.safe_io.append({"cls": "PWM_TIMER", "name": f"{ident}_{reg}",
                                 "what": f"{inst} {reg}: {what} ({where})",
                                 "address": f"IP_{inst}_BASE + offsetof(FTM_Type, {reg})",
                                 "mask": mask, "expected": value, "lock_bits": 0, "lock_clear": 0})
        route = [
            (f"{name}_TRGMUX", f"IP_TRGMUX_BASE + offsetof(TRGMUX_Type, TRGMUXn) + 4UL * TRGMUX_{inst}_INDEX",
             trgmux_mask | TRGMUX_LK, trgmux_value | TRGMUX_LK,
             f"TRGMUX_{inst} SEL{f + 1} = CMP0_OUT, locked"),
            (f"{name}_SIM_FTMOPT0", "IP_SIM_BASE + offsetof(SIM_Type, FTMOPT0)", sim_bit, sim_bit,
             f"SIM_FTMOPT0: {inst} fault input {f} from TRGMUX"),
            (f"{name}_CMP_C0", "IP_CMP0_BASE + offsetof(CMP_Type, C0)", CMP_C0_MASK, c0,
             "CMP0 C0: on, high-speed" + (", inverted" if c0 & CMP_C0_INVT else "")),
            (f"{name}_CMP_C1", "IP_CMP0_BASE + offsetof(CMP_Type, C1)", CMP_C1_MASK, c1,
             f"CMP0 C1: IN{t.input_index} against DAC level {t.dac_level} ({t.reference})"),
            (f"{name}_CMP_C2", "IP_CMP0_BASE + offsetof(CMP_Type, C2)", CMP_C2_RR_MASK, 0, "CMP0 C2: no round robin"),
        ]
        for reg_name, address, mask, value, what in route:
            self.safe_io.append({"cls": "PWM_TIMER", "name": reg_name, "what": f"{what} ({where})",
                                 "address": address, "mask": mask, "expected": value, "lock_bits": 0,
                                 "lock_clear": 0})
        for e in self.safe_io:
            if e["expected"] & ~e["mask"]:
                raise ConfigError(f"register list of SafeIo: {e['name']}: expected 0x{e['expected']:X} outside the "
                                  f"mask 0x{e['mask']:X}")

    def collect_sample_groups(self):
        """IO-62: register values of the sample groups (PWM trigger -> TRGMUX -> PDB -> ADC).

        The plan is nxconfig's (tools/nxconfig/samplegroup.py: sequencer and slot per channel, router outputs, delay
        in PDB clocks, completing sequencer); here it becomes s32k1xx register values for
        bsp/sampleGroup/sampleGroupConfiguration.h (family code platforms/s32k1xx/bsp/bspSampleGroup). The trigger
        output of the timer follows from the IO-61 trigger of the PWM group: INITTRIG -> the router input `counter`
        of the timer, a channel match -> `match`. Must run after collect_pwm() and collect_interrupts(). Nothing
        changes for a project without a sample group.
        """
        plans = list(getattr(self.config, "sample_groups", None) or [])
        if not plans:
            return
        pwm = self.pwm
        plan_adc = self.config.adc_access
        if plan_adc is None:
            raise ConfigError("sample groups: no converter settings (modules.bspAdcAccess), the ADCs cannot be set up")
        for g in plans:
            where = f"sample group {g.name}"
            if pwm is None or pwm["name"] != g.pwm or pwm["trigger"] is None:
                raise ConfigError(f"{where}: PWM group {g.pwm} is not the group of the PWM driver or has no trigger")
            source = g.timer_trigger["counter" if pwm["trigger"]["kind"] == "init" else "match"]
            if not 0 < source <= TRGMUX_SEL_MASK:
                raise ConfigError(f"{where}: router input code 0x{source:X} does not fit a TRGMUX SEL field")
            if not g.back_to_back:
                raise ConfigError(f"{where}: the s32k1xx family code chains the conversions back-to-back only")
            level = self.irq_levels.get(("sample_group", g.name))
            if level is None:
                raise ConfigError(f"{where}: no interrupt rank (interrupts.ranks, source sample_group)")
            index = {s.name: i for i, s in enumerate(g.sequencers)}
            sequencers = []
            for i, sq in enumerate(g.sequencers):
                pdb, adc = PDB_INSTANCE_RE.match(sq.name), ADC_INSTANCE_RE.match(sq.adc)
                if pdb is None or adc is None or pdb.group(1) != adc.group(1) or sq.name not in TRGMUX_PDB_INDEX:
                    raise ConfigError(f"{where}: {sq.name} -> {sq.adc} is no pair of the s32k1xx family (PDBn -> ADCn)")
                n = int(adc.group(1))
                if sq.register_index != TRGMUX_PDB_INDEX[sq.name] or sq.selector != 0:
                    raise ConfigError(f"{where}: router output {sq.target} (register {sq.register_index}, field "
                                      f"{sq.selector}) is not TRGMUX_{sq.name} SEL0")
                if g.delay_ticks > PDB_MOD_MAX:
                    raise ConfigError(f"{where}: delay {g.delay_ticks} clocks does not fit PDB DLY (16 bit)")
                members = [m for m in g.members if m.sequencer == sq.name]
                used = (1 << len(members)) - 1
                sc1 = []
                for m in sorted(members, key=lambda m: m.slot):
                    last = sq.name == g.completing and m.slot == len(members) - 1
                    sc1.append({"value": adc_sc1_channel(m.channel) | (ADC_SC1_AIEN if last else 0),
                                "channel": m.channel, "name": m.name, "aien": last,
                                "sample_ns": f"{float(m.sample_ns):.0f}"})
                sequencers.append({
                    "name": sq.name, "adc": sq.adc, "converter": n, "target": sq.target,
                    "trgmux_index": sq.register_index, "trgmux_mask": TRGMUX_SEL_MASK, "trgmux_sel": source,
                    "pdb_sc": PDB_SC_PDBEN, "pdb_mod": PDB_MOD_MAX, "pdb_idly": PDB_MOD_MAX,
                    "pdb_c1": used | (used << 8) | ((used & ~1) << 16), "pdb_dly0": g.delay_ticks,
                    "sim_mask": SIM_ADCOPT_ADC_FIELDS << (8 * n), "slots": len(members), "sc1": sc1,
                    "conversion_ns": sq.conversion_ns, "end_ns": f"{float(sq.end_ns):.0f}",
                })
            completing = index[g.completing]
            adc_irq = sequencers[completing]["converter"]
            self.sample_groups.append({
                "name": g.name, "ident": identifier(g.name), "pwm": g.pwm, "timer": g.timer,
                "trigger_point": g.trigger_point, "trigger_kind": pwm["trigger"]["kind"], "source": source,
                "delay_ns": g.delay_ns, "delay_ticks": g.delay_ticks,
                "sequencer_mhz": f"{float(g.sequencer_hz) / 1e6:g}",
                "realised_delay_ns": f"{float(g.realised_delay_ns):.0f}", "end_ns": f"{float(g.end_ns):.0f}",
                "period_ns": f"{float(g.period_ns):.0f}", "sequencers": sequencers,
                "members": [{"name": m.name, "ident": identifier(m.name), "sequencer": index[m.sequencer],
                             "slot": m.slot, "adc": m.adc, "channel": m.channel, "pin": m.pin,
                             "sample_ns": f"{float(m.sample_ns):.0f}"} for m in g.members],
                "completing": completing, "irq": f"ADC{adc_irq}_IRQn", "handler": f"ADC{adc_irq}_IRQHandler",
                "rank": level[0], "level": level[1], "missing_poll_limit": max(1, plan_adc.poll_cycles),
                "calibration_poll_limit": SAMPLE_GROUP_CALIBRATION_POLL_LIMIT, "confirmed": g.confirmed,
                "warnings": list(g.warnings),
            })

    def collect_features(self):
        """Derivative features as macros (1/0) for the generated integration, e.g. the LMEM cache of the S32K14x."""
        present = set(self.config.derivative.derivative.features)
        self.features = [{"name": name, "macro": macro, "description": text, "present": int(name in present)}
                         for name, (macro, text) in FEATURE_MACROS.items()]

    def dio_outputs(self):
        for ch in self.config.selected("dio.outputs"):
            low = ch.active == "low"
            yield {"name": identifier(ch.name), "default": "HIGH" if low else "LOW",
                   "inverted": "LOW_ACTIVE" if low else "HIGH_ACTIVE"}

    def dio_inputs(self):
        for ch in self.config.selected("dio.inputs"):
            yield {"name": identifier(ch.name), "inverted": "LOW_ACTIVE" if ch.active == "low" else "HIGH_ACTIVE"}


def _svd2header():
    """mcudb/tools/svd2header.py (PLAT-11): device header generator and interrupt list of a derivative."""
    import importlib.util
    name = "nx_svd2header"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MCUDB_TOOLS / "svd2header.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module              # dataclasses needs the module registered while it executes
    spec.loader.exec_module(module)
    return module


def _regdesc2header(family: str):
    """mcudb/<family>/tools/regdesc2header.py (PLAT-36, decision F3): header generator of a family without SVD."""
    import importlib.util
    name = f"nx_regdesc2header_{family}"
    if name in sys.modules:
        return sys.modules[name]
    path = nxconfig.REPO / "mcudb" / family / "tools" / "regdesc2header.py"
    if not path.is_file():
        raise ConfigError(f"family {family}: device_header.generated_by regdesc2header needs "
                          f"{path.relative_to(nxconfig.REPO).as_posix()}, which does not exist")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def device_header_text(config: "nxconfig.Config") -> tuple[str | None, dict[str, int]]:
    """PLAT-12: (text to write, interrupt numbers) of the derivative's vendor device header.

    A header in the repository (or in the OpenBSW submodule) is only read - the text is None, nothing is written.
    A header with `device_header.generated_by: regdesc2header` does not exist as a file: it is generated here from the
    checked-in register description, into the output directory under its own include path (no include path of the
    build has to change). The SVD generation of PLAT-11 was removed in PLAT-48.
    """
    d = config.derivative
    header = d.device_header
    family = d.derivative.family
    if header.generated_by == "regdesc2header":
        # PLAT-69: register description mcudb/<family>/registers/*.yaml, checked in, needs no workstation file.
        # A VIM family header has no IRQn_Type: its interrupt requests are mcudb vim.requests.
        regdesc = _regdesc2header(family)
        guard = re.sub(r"[^A-Za-z0-9]", "_", Path(header.file).name).upper()
        try:
            return regdesc.render(regdesc.validate(regdesc.load()), guard=guard), {}
        except regdesc.DescriptionError as e:
            raise ConfigError(f"{d.derivative.name}: the device header {header.file} is generated from the register "
                              f"description mcudb/{family}/registers and could not be produced: {e}") from None
    svd2header = _svd2header()
    if header.generated_by is not None:
        # PLAT-48: the SVD path of PLAT-11 is gone from the build. Every checked-in derivative header comes from the
        # vendor headers (PLAT-32, mcudb/tools/rtd_adapter.py); svd2header.py stays a workstation cross-check tool.
        raise ConfigError(f"{d.derivative.name}: device_header.generated_by {header.generated_by} is not supported by "
                          f"the build (supported: regdesc2header); check the derivative header in as a file instead")
    tried = [nxconfig.REPO / p.format(family=family) / header.file for p in HEADER_DIRS]
    for path in tried:
        if path.is_file():
            return None, svd2header.header_layout(svd2header.read_header(path))["irqs"]
    raise ConfigError(f"{d.derivative.name}: device header {header.file} not found (looked in "
                      + ", ".join(str(p.parent) for p in tried) + ")")


def device_header(config: "nxconfig.Config", source: str, irqs: dict[str, int]) -> str:
    """mcu/mcuDerivative.h: vendor device header of the derivative, minus peripherals the derivative lacks.

    The s32k1xx drivers enable clocks through PCC_<instance>_INDEX; a header written for a larger derivative
    (S32K148.h for S32K144) must not expose peripherals that are absent, e.g. to the clock driver.
    Interrupt names the family drivers use literally are mapped to this derivative's name (FAMILY_IRQ_ALIASES).
    """
    d = config.derivative
    if d.device_header is None:
        raise ConfigError(f"{d.derivative.name}: derivative has no device_header (mcudb)")
    described = nxchecks.described_derivative(config)
    if described is None:
        raise ConfigError(f"{d.derivative.name}: device header derivative {d.device_header.describes} not found")
    absent = sorted(name for name in described.instances if name not in d.instances)
    lines = [
        f"// Generated by tools/bswgen from {source} - do not edit.",
        f"// Device header of {d.derivative.name}: {d.device_header.file} (written for {described.derivative.name}).",
        "",
        "#pragma once",
        "",
        f'#include "{d.device_header.file}"',
    ]
    if absent:
        lines += ["", f"// peripherals of {described.derivative.name} that {d.derivative.name} does not have"]
        lines += [f"#undef PCC_{name}_INDEX" for name in absent]
    aliases = [(name, alt) for name, alternatives in sorted(FAMILY_IRQ_ALIASES.items())
               if name not in irqs for alt in alternatives if alt in irqs]
    if aliases:
        lines += ["", "// interrupt names the family drivers use, under the name this derivative gives the vector"]
        lines += [f"#define {name} {alt}" for name, alt in aliases]
    return "\n".join(lines) + "\n"


def irq_names(config: "nxconfig.Config", source: str, irqs: dict[str, int]) -> str:
    """interrupts/nxIrqNames.h: interrupt vectors of the used peripherals, under family neutral names.

    The board integration must not spell out the interrupt names of one derivative (finding of PLAT-11: the
    S32K116 has one FlexCAN mailbox vector, the S32K14x two). NX_CAN_MB_IRQS(X) expands X(<bus>, <IRQn>) for
    every message buffer interrupt of every used CAN bus, in the order the integration enables them.
    """
    lines = [f"// Generated by tools/bswgen from {source} - do not edit.",
             "// Interrupt vectors of the used peripherals under family neutral names (PLAT-12).", "",
             "#pragma once", ""]
    entries = []
    for bus in config.selected("can"):
        instance = FLEXCAN_RE.match(bus.instance)
        if instance is None:
            raise ConfigError(f"CAN {bus.name}: instance {bus.instance} is no FlexCAN of family {FAMILY}")
        found = flexcan_mb_irqs(config, bus, irqs)
        if not found:
            raise ConfigError(f"CAN {bus.name} ({bus.instance}): the device header of "
                              f"{config.derivative.derivative.name} has none of the message buffer interrupts "
                              + ", ".join(p.format(n=instance.group(1)) for p in FLEXCAN_MB_IRQS))
        entries += [(identifier(bus.name), irq) for irq in found]
    def emit(name: str, items: list[tuple[str, str]]):
        if items:
            lines.append(f"#define {name}(X) \\")
            lines.extend(f"    X({bus}, {irq}){'' if i == len(items) - 1 else ' \\'}"
                         for i, (bus, irq) in enumerate(items))
        else:
            lines.append(f"#define {name}(X)")

    lines.append("// message buffer interrupts of the used CAN buses: X(<board bus>, <IRQn of the derivative>)")
    emit("NX_CAN_MB_IRQS", entries)
    # per bus as well, for code that serves one bus only. Since ARCH-61 the family port has no such code any more
    # (main.cpp enables every bus through NX_CAN_MB_IRQS); the macros stay so that the generate of every existing
    # project stays byte identical - removing them is a change of the generate of its own
    for bus in dict.fromkeys(b for b, _ in entries):
        lines.append("")
        emit(f"NX_CAN_MB_IRQS_{bus}", [e for e in entries if e[0] == bus])
    return "\n".join(lines) + "\n"


def vector_handler(short: str) -> str:
    """Handler name of the external vector `short` (device header name without _IRQn) in startup/nxIrqVectors.inc."""
    return FAMILY_IRQ_HANDLERS.get(short, short + "_IRQHandler")


def safe_isr_vectors(source: str, irqs: dict[str, int], served: set[str]) -> str:
    """SYS-170 (SYS69-S1, finding SFP-F01): interrupts/nxSafeIsrVectors.h, the handlers the generic SAFE_ISR wrappers
    of safeLifecycle/IsrWrapper.cpp serve.

    The startup routes every external vector through Safe_<handler> (startup/nxIrqVectors.inc names the handler), so
    every vector of the derivative except the safety handlers (SAFETY_IRQ_HANDLERS) is listed here, used or not: the
    pre hook closes the MPU gate of the protected RAM before the handler runs and the post hook reopens it, so no
    interrupt that preempts the safety cycle runs with write access to that RAM. `served`: handlers the generated
    integration defines in an object that nothing else pulls from its archive (the CAN message buffer vectors of the
    used buses, os/isr/isr_can.cpp) - they get no weak default, the reference of the wrapper pulls them in.
    """
    handlers = list(dict.fromkeys(vector_handler(short)
                                  for _, short in sorted(_svd2header().external_irqs(irqs).items())))
    wrapped = [h for h in handlers if h not in SAFETY_IRQ_HANDLERS]
    lines = [f"// Generated by tools/bswgen from {source} - do not edit.",
             "// SYS-170: external interrupt vectors of the derivative that run through a generic SAFE_ISR wrapper",
             "// Safe_<handler> (safeLifecycle/IsrWrapper.cpp): every vector of startup/nxIrqVectors.inc except the",
             "// safety handlers (" + ", ".join(SAFETY_IRQ_HANDLERS) + ").",
             "// X(<handler>, <served>): served 1 = the generated integration defines the handler (no weak default),",
             "// 0 = weak default DefaultISR unless the integration defines it.", "",
             "#pragma once", "",
             f"#define NX_SAFE_ISR_VECTOR_COUNT {len(wrapped)}U", ""]
    if wrapped:
        lines.append("#define NX_SAFE_ISR_VECTORS(X) \\")
        lines.extend(f"    X({h}, {1 if h in served else 0})" + ("" if i == len(wrapped) - 1 else " \\")
                     for i, h in enumerate(wrapped))
    else:
        lines.append("#define NX_SAFE_ISR_VECTORS(X)")
    return "\n".join(lines) + "\n"


def template_env() -> Environment:
    """Jinja environment of the templates (also used by the tests to render single headers)."""
    return Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)


def core_context(config: "nxconfig.Config") -> dict:
    """ARCH-24: architecture and interrupt controller of the derivative's core as generated macros.

    One table, tools/nxconfig config.CORES (checked by nxconfig before this runs). The macro names are the contract of
    platforms/common/interruptPriority (nxInterruptPriority.h) and of every family template that differs between
    core profiles: NX_CORE_ARCHITECTURE_<ARCHITECTURE> and NX_CORE_INTERRUPT_CONTROLLER_<CONTROLLER>, never
    __ARM_ARCH (7 for Cortex-M4F and Cortex-R4F alike, finding ARCH-19).
    """
    name = config.derivative.derivative.core
    core = nxconfig.config.CORES.get(name)
    if core is None:
        raise ConfigError(f"{config.derivative.derivative.name}: unknown core {name} (tools/nxconfig config.CORES)")
    return {"name": name, "architecture": core.architecture, "controller": core.interrupt_controller,
            "architecture_macro": f"NX_CORE_ARCHITECTURE_{core.architecture.upper()}",
            "controller_macro": f"NX_CORE_INTERRUPT_CONTROLLER_{core.interrupt_controller.upper()}"}


VIM_MASK_WORDS = 4          # nxVimChannel.h: the fixed channels in four 32-bit words (at most 128 channels)


def vim_interrupt_context(config: "nxconfig.Config") -> dict:
    """ARCH-24: interrupt ranks of a project on a VIM core as channels (interrupts/interruptPriorities.h).

    nxconfig orders the ranked entries (checks.vim_channel_order: by rank, equal ranks in YAML order) and checks that
    the free channels suffice; here the n-th entry of that order gets the n-th free channel. One channel per entry:
    how many requests an instance really has (e.g. two per DCAN) knows only the family generator (PLAT-37).
    """
    order = nxchecks.vim_channel_order(config)
    name = config.derivative.derivative.name
    if order is None:
        raise ConfigError(f"{name}: core {config.derivative.derivative.core} has a VIM, but the derivative has no vim "
                          "entry in mcudb, the interrupt channels of the board port cannot be generated")
    if order.channels > 32 * VIM_MASK_WORDS:
        raise ConfigError(f"{name}: VIM with {order.channels} channels (mcudb vim.channels), the generated "
                          f"interrupt header describes at most {32 * VIM_MASK_WORDS}")
    free = order.free_channels
    if len(order.order) > len(free):
        raise ConfigError(f"{name}: {len(order.order)} ranked interrupts, but only {len(free)} free VIM channels "
                          "(nxconfig reports this with the YAML path)")
    masks = [0] * VIM_MASK_WORDS
    for channel in order.fixed:
        masks[channel // 32] |= 1 << (channel % 32)
    notes = {(r.source, r.name): r.note for r in config.project.interrupts.ranks}
    entries = []
    for position, (source, board_name) in enumerate(order.order):
        ident = f"{source.upper()}_{identifier(board_name)}"
        entries.append({"macro": f"NX_IRQ_CHANNEL_{ident}", "rank_macro": f"NX_IRQ_RANK_{ident}",
                        "source": source, "name": board_name, "rank": order.ranks[(source, board_name)],
                        "position": position, "channel": free[position], "note": notes[(source, board_name)]})
    return {"controller": "vim", "derivative": name, "core": config.derivative.derivative.core,
            "channels": order.channels, "fixed": list(order.fixed), "free": list(free), "rank_count": len(free),
            "fixed_masks": [f"0x{m:08X}UL" for m in masks], "entries": entries}


def mpu_context(config: "nxconfig.Config") -> dict | None:
    """ARCH-16: region descriptors of the derivative's MPU and the CESR[NRGD] code the hardware reports.

    None if the derivative describes no MPU (mcudb mpu): the generated header then defines no NX_MPU_REGIONS and
    code that needs the MPU stops with an #error instead of assuming a region count (finding ARCH-09 B3).
    """
    d = config.derivative
    if d.mpu is None:
        return None
    nrgd = MPU_NRGD_CODES.get(d.mpu.regions)
    if nrgd is None:
        raise ConfigError(f"{d.derivative.name}: MPU with {d.mpu.regions} region descriptors (mcudb mpu.regions) "
                          f"has no CESR[NRGD] code in family {FAMILY} (known: "
                          f"{', '.join(str(r) for r in MPU_NRGD_CODES)})")
    return {"regions": d.mpu.regions, "nrgd": nrgd, "source": d.mpu.source, "verify": d.mpu.verify}


def c_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def nvm_context(config: "nxconfig.Config") -> dict:
    """NVM-07: partition of the emulated EEPROM and the time limit of one EEPROM write (nvm/nvmPartition.h)."""
    plan = config.nvm
    p = plan.partition if plan is not None else None
    limit = nxnvm.write_limit_of(config)
    if p is not None and p.write_limit_us is not None:
        origin = f"{p.backup_bytes // 1024} KB, nvm.partition"
    else:
        emulation = config.derivative.eeprom_emulation
        largest = max((b.backup_bytes for b in emulation.timing.backups), default=0) if limit else 0
        origin = f"largest of {config.derivative.derivative.name}, {largest // 1024} KB, no nvm.partition"
    window = next((r for r in config.derivative.memory if r.kind == "eeprom_ram"), None)
    return {"p": p, "write_limit_us": limit, "write_limit_origin": origin, "window": window,
            "derivative": config.derivative.derivative.name,
            "lines": [c_string(line) for line in p.describe()] if p is not None else [],
            "confirm": p.confirm_word if p is not None else ""}


def source_label(config: "nxconfig.Config") -> str:
    """Where the generated file comes from: the project path, relative to its workspace resp. the tree (ARCH-57).

    A project of a workspace outside the neXtBSW tree (customer-project.md N4) is not below nxconfig.REPO; its
    label is then relative to the workspace root, so the generated header names the same path the customer sees.
    """
    roots = [nxconfig.REPO]
    workspace = getattr(config, "workspace", None)
    if workspace is not None:
        roots.insert(0, workspace.root)
    for root in roots:
        try:
            return f"{config.project_file.relative_to(root).as_posix()} (board {config.board.board.name})"
        except ValueError:
            continue
    return f"{config.project_file.name} (board {config.board.board.name})"


# --- serialized ADC access (IO-19): register encodings of the converter settings the project chose. The
# conversion time and the polling bound themselves are derived by the ADC model tools/nxconfig/adc.py.
ADC_MODE_CODES = {8: 0, 10: 2, 12: 1}       # CFG1[MODE] per resolution
ADC_ADIV_CODES = {1: 0, 2: 1, 4: 2, 8: 3}   # CFG1[ADIV] per clock divider
ADC_AVGS_CODES = {4: 0, 8: 1, 16: 2, 32: 3}  # SC3[AVGS] per averaging count (AVGE off for 1)


def adc_access_context(config: "nxconfig.Config") -> dict:
    """Template values of the serialized ADC access: register expressions and the derived timing."""
    plan = config.adc_access
    if plan is None:
        raise ConfigError(f"no ADC access configuration: module {nxadc.MODULE} has no manifest or no parameters "
                          f"(project modules.{nxadc.MODULE})")
    mode = ADC_MODE_CODES.get(plan.resolution_bits)
    if mode is None:
        raise ConfigError(f"ADC resolution {plan.resolution_bits} bit: no CFG1[MODE] code "
                          f"(supported: {', '.join(f'{b} bit' for b in ADC_MODE_CODES)})")
    averaging = plan.averages > 1
    return {
        "plan": plan,
        "cfg1": f"(ADC_CFG1_ADIV({ADC_ADIV_CODES[plan.clock_divider]}) | ADC_CFG1_MODE({mode}))",
        "cfg2": f"ADC_CFG2_SMPLTS({plan.sample_clocks - 1})",
        "sc3": f"(ADC_SC3_CAL(0) | ADC_SC3_ADCO(0) | ADC_SC3_AVGE({1 if averaging else 0})"
               f" | ADC_SC3_AVGS({ADC_AVGS_CODES[plan.averages] if averaging else 0}))",
        "instances": list(plan.instances),
    }


def validate(project_file: Path, mcudb: Path | None = None, module_dirs=None,
             running_tree: Path | None = None) -> list[nxconfig.Message]:
    """Everything a generator run would complain about, as messages - without writing a single file (TOOL-07).

    Same checks as generate(): the configuration model of tools/nxconfig (structure, references, pin functions,
    resource conflicts, module integration), the communication channels of this generator and the collection the
    templates are fed with. Every message carries severity, text, file, line and YAML path, so the Studio can show
    it at the line that causes it and the command line prints the same text. Never raises for a configuration
    problem; template rendering is not part of it (it needs the collected board anyway).

    TOOL-87 - `running_tree`: which neXtBSW tree this call runs as, handed down to `nxconfig.check()` (the rule
    is described there, TOOL-85). Default None keeps the behaviour of every call that exists today: the tree
    this package lies in, so a workspace pin that names another tree stays refused on the command line. The
    Studio switches the tree per opened project (TOOL-61) and says so here; without this parameter it had to
    move the module variable `nxconfig.config.REPO` under a lock (studio/core/tree.py `as_tool_of`, TOOL-60).

    `mcudb` and `module_dirs` carried the folders of that tree as **default values bound at import**; they are
    now taken from the running tree instead, which is the same thing whenever `running_tree` is not given
    (`nxconfig.MCUDB`, `modules.MODULE_DIRS`). Without that, a caller that names another tree would still be
    handed this tree's derivative database.
    """
    config, messages = _checked(project_file, mcudb, module_dirs, running_tree)
    if config is None:
        return messages
    board = None
    try:
        board = collect(Board(config))
    except ConfigError as e:
        # a family or driver problem of this generator: no YAML path, but the project file it belongs to
        messages.append(nxconfig.Message("error", str(e), config.project_file))
    messages += [nxconfig.Message("warning", w, config.project_file) for w in (board.warnings if board else ())]
    return messages


def _checked(project_file: Path, mcudb: Path | None, module_dirs,
             running_tree: Path | None = None) -> tuple["nxconfig.Config | None", list]:
    """Configuration and messages of a run; validate() and generate() use it, so they cannot drift apart.

    config is None if the configuration model already rejected the project (then the messages say why).
    `mcudb` / `module_dirs` None: the folders of `running_tree`, which without it is the tree this package
    lies in - the values the callers passed explicitly before (TOOL-87).
    """
    # nxconfig also checks the module integration (manifests, devices, connections, parameters)
    config, messages = nxconfig.check(project_file,
                                      mcudb if mcudb is not None else nxconfig.config.mcudb_dir(running_tree),
                                      module_dirs if module_dirs is not None
                                      else nxconfig.config.module_dirs_of(running_tree),
                                      running_tree=running_tree)
    if config is None:
        return None, list(messages)
    # CAN buses (ARCH-07) and UARTs (ARCH-15) of the project; both belong to nxconfig.check
    # (see tools/nxconfig/can.py and tools/nxconfig/uart.py)
    channel_reporter = Reporter(sources=dict(config.sources))
    nxcan.check_buses(config, channel_reporter)
    nxuart.check_uarts(config, channel_reporter)
    return config, list(messages) + list(channel_reporter.messages)


def collect(board: "Board") -> "Board":
    """Collects everything the templates need. Order matters: the interrupts before the channels (they resolve
    their level from the ranks). Raises ConfigError for a problem only this generator can see."""
    board.collect_interrupts()
    board.collect_clock()
    board.collect_peripheral_clocks()       # after the clock plan: the PCC writes use its sources (ARCH-18)
    board.collect_busids()
    board.collect_adc()
    board.collect_pwm()
    board.collect_capture()     # after the PWM group: a counter is used either way, not both (ARCH-02)
    board.collect_adc_sampling()  # IO-31: after the channels and the PWM group (its realized period)
    board.collect_hall()        # after the interrupts: the pin interrupt levels come from the ranks (PLAT-21)
    board.collect_pin_irq()     # IO-54: after the Hall sets, whose pins are entries of the dispatcher table
    board.collect_features()
    board.collect_safe_outputs()
    board.collect()
    board.collect_safe_io()     # SYS-24: after the pin table, the PWM group and the safe outputs
    board.collect_hw_threshold()  # SYS-86: after the PWM group and the register list of SafeIo
    board.collect_sample_groups()  # IO-62: after the PWM group (its trigger) and the interrupt ranks
    return board


def generate(project_file: Path, out_dir: Path, mcudb: Path = nxconfig.MCUDB, module_dirs=modules.MODULE_DIRS):
    config, messages = _checked(project_file, mcudb, module_dirs)
    if config is None:
        raise ConfigError("\n".join(str(m) for m in messages if m.severity == "error"))
    module_set, integration = config.modules, config.integration
    for m in messages:
        print(f"bswgen: {m}", file=sys.stderr)
    if any(m.severity == "error" for m in messages):
        raise ConfigError("communication configuration rejected (messages above)")
    board = collect(Board(config))
    for w in board.warnings:
        print(f"bswgen: warning: {w}", file=sys.stderr)
    env = template_env()
    ctx = {
        "source": source_label(config),
        "pins": list(board.pins.values()),
        "spi_buses": board.spi_buses,
        "outputs": list(board.dio_outputs()),
        "inputs": list(board.dio_inputs()),
        "safety": board.safety,
        "adc": board.adc,
        "adc_instances": [{"name": inst, "index": i, "count": sum(ch["instance"] == inst for ch in board.adc)}
                          for i, inst in enumerate(ADC_INSTANCES)],
        "adc_vref_mv": board.adc_vref_mv,
        # ARCH-32: nominal VREFH of the board for the reference check of the family code (None: no define)
        "adc_reference_mv": config.board.board.adc_reference_mv,
        "board_name": config.board.board.name,
        "adc_max_raw": board.adc_max_raw,
        "pwm": board.pwm,
        # SYS-86: register values of the hardware threshold, None without one
        "hw_threshold": board.hw_threshold,
        # IO-62: register values of the sample groups, empty without one
        "sample_groups": board.sample_groups,
        # SYS-123: the group lock of the PWM driver, only in a project with a degraded mapping (tools/nxconfig/degraded.py
        # derived the groups for the safe supervisor); every other project gets its header unchanged
        "pwm_lock": board.pwm is not None and config.integration is not None
                    and "degraded_groups" in getattr(config.integration.modules.get("safeSupervisor"), "derived", {}),
        # IO-31: None per channel that converts once per read, otherwise its averaging plan
        "adc_sampling": board.adc_sampling,
        "adc_sampling_count": sum(p is not None for p in board.adc_sampling),
        "safe_outputs": board.safe_outputs,
        # SYS-116: value sets of the safe states, only with an active short allowed somewhere
        "safe_select": board.safe_select,
        "safe_state_groups": board.safe_state_groups,
        "safe_off_outputs": board.safe_off_outputs,
        "safe_io": board.safe_io,
        "capture": board.capture,
        "hall": board.hall,
        "pin_irq": board.pin_irq,
        "pin_events": board.pin_events,
        "c": board.clock,
        "pcc": board.peripheral_clocks,
        "can": board.can,
        "busid": board.busid,
        "uart": board.uart,
        "uart_console": board.uart_console,
        "irq": board.irq,
        "core": board.core,
        "features": board.features,
        "mpu": mpu_context(config),
        "adc_access": adc_access_context(config),
        # IO-18: integer sensor characteristics of the used channels (tools/nxconfig/sensor.py)
        "sensors": [{**plan.__dict__, "ident": identifier(plan.name)} for plan in config.adc_sensors.values()],
        "quality_ok": nxsensor.QUALITY_OK,
        "quality_range": nxsensor.QUALITY_RANGE,
        # PLAT-12: ADC converters of the derivative the family driver knows (ADC_INSTANCES)
        "adc_converters": [i for i in ADC_INSTANCES if i in config.derivative.instances],
        # NVM-07: partition of the emulated EEPROM, time limit of one EEPROM write
        "nvm": nvm_context(config),
        # IO-28: used I2C buses with rate, divider and FlexIO resources (tools/nxconfig/i2c.py)
        "i2c": i2c_context(config),
    }
    files = {
        "bsp/io/io/ioConfiguration.h": "ioConfiguration.h.j2",
        "bsp/spi/spiBusConfiguration.h": "spiBusConfiguration.h.j2",
        "bsp/io/output/outputConfiguration.h": "outputConfiguration.h.j2",
        "bsp/io/output/outputConfigurationStrings.h": "outputConfigurationStrings.h.j2",
        "bsp/io/input/inputConfiguration.h": "inputConfiguration.h.j2",
        "bsp/io/input/inputConfigurationStrings.h": "inputConfigurationStrings.h.j2",
        "bsp/adc/analogInputConfiguration.h": "analogInputConfiguration.h.j2",
        "bsp/adc/analogInputConfigurationStrings.h": "analogInputConfigurationStrings.h.j2",
        "bsp/adc/analogInputScaleConfiguration.h": "analogInputScaleConfiguration.h.j2",
        "bsp/adc/analogSensorConfiguration.h": "analogSensorConfiguration.h.j2",
        "bsp/adc/analogInputSamplingConfiguration.h": "analogInputSamplingConfiguration.h.j2",
        "bsp/adc/adcAccessConfiguration.h": "adcAccessConfiguration.h.j2",
        "bsp/adc/adcAccessConfiguration.cmake": "adcAccessConfiguration.cmake.j2",
        "bsp/adc/adcReferenceConfiguration.h": "adcReferenceConfiguration.h.j2",
        "bsp/io/outputPwm/outputPwmConfiguration.h": "outputPwmConfiguration.h.j2",
        "bsp/io/outputPwm/outputPwmConfigurationStrings.h": "outputPwmConfigurationStrings.h.j2",
        "bsp/io/outputPwm/ftmPwmConfiguration.h": "ftmPwmConfiguration.h.j2",
        "bsp/hwThreshold/hwThresholdConfiguration.h": "hwThresholdConfiguration.h.j2",   # SYS-86
        "bsp/sampleGroup/sampleGroupConfiguration.h": "sampleGroupConfiguration.h.j2",   # IO-62
        "bsp/safeOutputs/safeOutputsConfiguration.h": "safeOutputsConfiguration.h.j2",
        "bsp/safeOutputs/safeOutputsConfiguration.cmake": "safeOutputsConfiguration.cmake.j2",   # SYS-116
        "bsp/safeIo/safeIoConfiguration.h": "safeIoConfiguration.h.j2",
        "bsp/capture/inputCaptureConfiguration.h": "inputCaptureConfiguration.h.j2",
        "bsp/hall/hallDecoderConfiguration.h": "hallDecoderConfiguration.h.j2",
        "bsp/hall/hallSensorConfiguration.h": "hallSensorConfiguration.h.j2",
        "bsp/pinIrq/pinIrqConfiguration.h": "pinIrqConfiguration.h.j2",
        "bsp/pinIrq/pinIrqConfiguration.cmake": "pinIrqConfiguration.cmake.j2",
        "bsp/pinEvent/pinEventConfiguration.h": "pinEventConfiguration.h.j2",
        "bsp/clock/boardClock.h": "boardClock.h.j2",
        "bsp/clock/peripheralClocks.h": "peripheralClocks.h.j2",
        "bsp/can/canBitTimingConfiguration.h": "canBitTimingConfiguration.h.j2",
        "bsp/can/canDeviceConfiguration.h": "canDeviceConfiguration.h.j2",
        "bsp/can/canBusOffConfiguration.h": "canBusOffConfiguration.h.j2",   # COM-37
        "busid/busIdConfiguration.h": "busIdConfiguration.h.j2",
        "bsp/uart/uartConfiguration.h": "uartConfiguration.h.j2",
        "bsp/i2c/i2cConfiguration.h": "i2cConfiguration.h.j2",
        "interrupts/interruptPriorities.h": "interruptPriorities.h.j2",
        "features/derivativeFeatures.h": "derivativeFeatures.h.j2",
        "mcu/derivativeFacts.h": "derivativeFacts.h.j2",
    }
    contents = {rel: env.get_template(template).render(ctx) for rel, template in files.items()}
    contents["nvm/nvmPartition.h"] = env.get_template("nvmPartition.h.j2").render({"source": ctx["source"],
                                                                                   **ctx["nvm"]})
    # NVM-12: areas of the data flash (nvm.data_flash), table for dataflash::AreaService
    contents["nvm/nvmDataFlash.h"] = env.get_template("nvmDataFlash.h.j2").render(
        {"source": ctx["source"], "derivative": config.derivative.derivative.name,
         "df": config.nvm.data_flash if config.nvm is not None else None})
    # PLAT-12: device header of the derivative (generated from an SVD when it is not in the repository) and
    # everything the interrupt vectors of that header decide: vector table of the startup template, family
    # neutral interrupt names of the integration, interrupt name aliases of the family drivers.
    header_text, irqs = board.header_text, board.irqs
    if header_text is not None:
        contents[config.derivative.device_header.file] = header_text
    contents["mcu/mcuDerivative.h"] = device_header(config, ctx["source"], irqs)
    contents["interrupts/nxIrqNames.h"] = irq_names(config, ctx["source"], irqs)
    contents["startup/nxIrqVectors.inc"] = _svd2header().irq_vectors_inc(
        irqs, f"{config.derivative.derivative.name} ({config.derivative.device_header.file})", FAMILY_IRQ_HANDLERS)
    # SYS-170: the generic SAFE_ISR wrappers of every non-safety vector of that list
    served = {v["isr"] for bus in board.can for v in bus["mb_vectors"]}
    contents["interrupts/nxSafeIsrVectors.h"] = safe_isr_vectors(ctx["source"], irqs, served)
    contents.update(blgen.memory_files(config, ctx["source"]))     # memory layout (ARCH-08), board part: blgen CLI
    contents.update(modules.render_all(integration, module_set, ctx["source"]))
    # PLAT-120: licence line of every generated file; the device header written from an SVD carries the licence
    # of the vendor data it is derived from and stays as it is
    contents = genlicense.stamp_all(contents, skip=(config.derivative.device_header.file,))
    # PLAT-79: configuration and generator identity of the image (console "version"/"info", DID 0xF1F1) and the
    # manifest of this run with the SHA-256 of every file written (tools/nxconfig/identity.py)
    identity = nxidentity.of(config)
    contents[nxidentity.FIELDS_HEADER] = genlicense.stamp(identity.fields_header(), nxidentity.FIELDS_HEADER)
    contents[nxidentity.manifest_path("bswgen")] = genlicense.stamp(identity.manifest("bswgen", contents),
                                                                     nxidentity.manifest_path("bswgen"))
    for rel, content in contents.items():
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")
    board.integration = integration
    return board


def i2c_context(config) -> list[dict]:
    """IO-28: the used I2C buses for bsp/i2c/i2cConfiguration.h, from the plans of tools/nxconfig/i2c.py.

    The s32k1xx port drives I2C only on a FlexIO (TOOL-120); a used bus without a complete FlexIO plan is refused
    here as the last line of defence - nxconfig has reported why with its YAML path before.
    """
    buses = {b.name: b for b in config.board.i2c}
    entries = []
    for plan in config.i2c_buses:
        if plan.scl_timer is None:
            raise ConfigError(f"I2C {plan.name}: no FlexIO plan (rate, time limit or resources rejected by "
                              f"tools/nxconfig, messages above)")
        bus = buses[plan.name]
        entries.append({**plan.__dict__, "timeout_us": plan.timeout_us, "ident": identifier(plan.name),
                        "regs": f"IP_{plan.instance.upper()}", "clock_macro": f"NX_CLOCK_{plan.instance.upper()}_HZ",
                        "sda": bus.sda.pin, "scl": bus.scl.pin})
    return entries


def show_nvm_partition(project_file: Path, mcudb: Path | None = None, out=sys.stdout,
                       running_tree: Path | None = None) -> int:
    """NVM-07: prints the partition of the emulated EEPROM before anyone writes it; 1 if the project has none or the
    configuration is rejected.

    `running_tree` as in validate() (TOOL-87); without it - and that is every call of today - the tree this
    package lies in, so `mcudb` defaults to `nxconfig.MCUDB` exactly as before.
    """
    config, messages = nxconfig.check(project_file,
                                      mcudb if mcudb is not None else nxconfig.config.mcudb_dir(running_tree),
                                      running_tree=running_tree)
    for m in messages:
        if m.severity == "error" or m.yaml_path.startswith(("nvm", "eeprom_emulation")):
            print(f"bswgen: {m}", file=sys.stderr)
    if config is None or any(m.severity == "error" for m in messages):
        return 1
    p = config.nvm.partition if config.nvm is not None else None
    if p is None:
        print("no nvm.partition in the project: nothing to write", file=out)
        return 1
    for line in p.describe():
        print(line, file=out)
    print(f"confirmation word at the board: flexnvm partition {p.confirm_word}", file=out)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="path to projects/<project>/project.yaml")
    parser.add_argument("out", type=Path, nargs="?", help="output directory for generated headers")
    parser.add_argument("--validate", action="store_true",
                        help="only check the configuration (no file is written), same messages as the Studio")
    parser.add_argument("--nvm-partition", action="store_true",
                        help="only show the partition of the emulated EEPROM the project wants (NVM-07), the values "
                             "the console command 'flexnvm partition' writes after confirmation")
    args = parser.parse_args()
    if args.nvm_partition:
        sys.exit(show_nvm_partition(args.project))
    if args.out is None and not args.validate:
        parser.error("the output directory is required unless --validate is given")
    if args.validate:
        # same function as the Studio live check (TOOL-07): messages with file, line and YAML path
        messages = validate(args.project)
        for m in messages:
            print(f"bswgen: {m}", file=sys.stderr)
        sys.exit(1 if any(m.severity == "error" for m in messages) else 0)
    try:
        generate(args.project, args.out)
    except ConfigError as e:
        print(f"bswgen: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
