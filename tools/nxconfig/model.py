"""Schemas of the three configuration levels (doc/architecture/README.md, section 4).

Derivative  mcudb/<family>/<derivative>.yaml   manufacturer facts, read only
Board       boards/<board>/board.yaml          what is fitted and wired
Project     projects/<project>/project.yaml    what one ECU software uses
            projects/<project>/rte.yaml        ECU description of the project (project.rte, ARCH-34)
            projects/<project>/diag.yaml       diagnostic description of the project (project.diag, ARCH-51)

Not a level: nextbsw.yaml at the root of a customer repository (workspace pin, ARCH-56, model WorkspacePin) only
says which neXtBSW tree and which extra search paths a workspace uses.

The models only check structure and types. Relations between entries and levels (references, resource
conflicts, derivative limits) are checked in checks.py so that all findings come with a YAML path.
"""

import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Annotated, Literal, get_args

from pydantic import (BaseModel, ConfigDict, Discriminator, Field, PrivateAttr, Tag, ValidationInfo, field_validator,
                      model_validator)
from pydantic_core import InitErrorDetails, PydanticCustomError, ValidationError

from . import bswdtc

SCHEMA_VERSION = 1

Name = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_]+$")]
PositiveInt = Annotated[int, Field(gt=0)]
Level = Literal["high", "low"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# Descriptions and units for the generated reference (tools/docgen -> doc/reference). A description is written for
# the reader of the reference: what the value means, where it comes from, what it does. Units: the naming convention
# <name>_<unit> where it applies, otherwise json_schema_extra; counts, indices, identifiers and register values are
# marked dimensionless with NO_UNIT, so that the reference tells them apart from a missing unit.
NO_UNIT = {"unit": "none"}
BYTES = {"unit": "byte"}
HZ = {"unit": "Hz"}
SOURCE = "Document the values of this entry come from (data sheet, reference manual, schematic), with section or page."
VERIFY = ("`true`: the entry is not confirmed by its source; checks that depend on it warn instead of trusting it.")
NOTE = "Free text for the reader; no tool evaluates it."
NAME = "Name of the entry, unique in its list; other entries and the project refer to it by this name."
PIN = "Pin name of the derivative (e.g. `PTC2`), matching `derivative.pin_pattern`."
PAD = ("Package pin (ball) number of the pin, for cross-checking with the schematic; no tool evaluates it. Left "
       "out: not stated.")


# ----------------------------------------------------------------------------------------------- derivative use
# ARCH-37: a `verify: true` in mcudb is reported for exactly the values a project uses. "Uses" is not a second list
# of values: every derivative entry that can carry `verify` is a Fact, and while checks.run() checks the project, a
# DerivativeUse records every value field of a Fact that is read, with the purpose (check phase) it was read for.
# Fields that only identify an entry (FACT_KEYS: name, class, kind - read when an entry is searched) and the
# bookkeeping keys are no values; a field holding another Fact is no value of its parent either (the nested entry has
# its own `verify`). Entries a check does not read field by field (peripheral instances the project switches on,
# pin multiplexing of the used pins) are recorded by checks.py as a whole with DerivativeUse.entry().

FACT_KEYS = frozenset({"name", "class_", "kind", "source", "verify", "note"})
_USE: ContextVar["DerivativeUse | None"] = ContextVar("nxconfig_derivative_use", default=None)
_VALUE_FIELDS: dict[type, frozenset[str]] = {}


def _holds_fact(annotation) -> bool:
    args = get_args(annotation)
    if not args:
        return isinstance(annotation, type) and issubclass(annotation, Fact)
    return any(_holds_fact(arg) for arg in args)


def value_fields(cls: type["Fact"]) -> frozenset[str]:
    """Fields of a Fact whose reading counts as use of its values (ARCH-37)."""
    fields = _VALUE_FIELDS.get(cls)
    if fields is None:
        fields = _VALUE_FIELDS[cls] = frozenset(
            name for name, info in cls.model_fields.items()
            if name not in FACT_KEYS and not _holds_fact(info.annotation))
    return fields


class Fact(Strict):
    """Derivative entry with `source`/`verify`: reads of its values are recorded while a project is checked."""

    def __getattribute__(self, name):
        use = _USE.get()
        if use is not None and name in value_fields(type(self)):
            use.read(self, name, sys._getframe(1))
        return super().__getattribute__(name)


@dataclass
class _EntryUse:
    entry: "Fact"
    fields: dict[str, None] = dataclass_field(default_factory=dict)      # YAML keys in the order of first use
    purposes: dict[str, None] = dataclass_field(default_factory=dict)


class DerivativeUse:
    """Derivative values a project uses (ARCH-37), recorded while `with use:` is active in this context.

    `phase(purpose)` names what the reads inside it are for; `ignore` are functions that read the derivative only to
    check the derivative itself (e.g. min <= max of every limit): their reads are no use by the project.
    """

    def __init__(self, ignore=()):
        self.entries: dict[int, _EntryUse] = {}
        self.purpose = "the project checks"
        self._ignore = {f.__code__ for f in ignore}
        self._token = None

    def __enter__(self):
        self._token = _USE.set(self)
        return self

    def __exit__(self, *exc):
        _USE.reset(self._token)

    @contextmanager
    def phase(self, purpose: str):
        before, self.purpose = self.purpose, purpose
        try:
            yield
        finally:
            self.purpose = before

    def read(self, entry: "Fact", name: str, frame):
        while frame is not None and frame.f_code.co_name.startswith("<"):   # generator expression, lambda
            frame = frame.f_back
        if frame is not None and frame.f_code in self._ignore:
            return
        info = type(entry).model_fields[name]
        self.entry(entry, self.purpose, info.alias or name)

    def entry(self, entry: "Fact", purpose: str, key: str | None = None):
        """Records use of `entry` for `purpose`; `key` the YAML key of the value, None: the entry as a whole."""
        use = self.entries.setdefault(id(entry), _EntryUse(entry))
        if key is not None:
            use.fields[key] = None
        use.purposes[purpose] = None


def fact_paths(root: BaseModel) -> list[tuple[tuple, "Fact"]]:
    """(YAML path, entry) of every Fact below `root` in document order, without recording reads."""
    found: list[tuple[tuple, Fact]] = []

    def walk(node, path):
        if isinstance(node, BaseModel):
            if isinstance(node, Fact):
                found.append((path, node))
            values = node.__dict__
            for name, info in type(node).model_fields.items():
                walk(values.get(name), path + (info.alias or name,))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, path + (i,))

    walk(root, ())
    return found


# ----------------------------------------------------------------------------------------------- derivative


NonNegativeInt = Annotated[int, Field(ge=0)]
# signal -> pin -> family-specific function selector: a number (s32k1xx PORT_PCR.MUX, stm32 GPIOx_AFR) or named
# register values where one number is not enough (e.g. s32k3xx {mscr: .., imcr: .., imcr_value: ..} for inputs).
# Signal names are "<instance>_<function>" with the instance names of the derivative (FlexCAN0_RX, FTM0_CH3,
# ADC0_SE4, USART2_TX), plus GPIO. The generators interpret the selector; nxconfig checks that the function exists.
Selector = NonNegativeInt | dict[Annotated[str, Field(pattern=r"^[a-z0-9_]+$")], NonNegativeInt]
SignalTable = dict[str, dict[str, Selector]]


# PLAT-22/ARCH-18: where the functional clock of a peripheral instance comes from. A clock source (s32k1xx: SOSC,
# SIRC, FIRC, SPLL) is selected in the peripheral's clock control (s32k1xx PCCn.PCS) and reaches the instance
# through an asynchronous output of that source (`divider` 1 or 2 for the DIV1/DIV2 output). "bus" and "core" are
# instances without a clock select, "none" instances whose clock is chosen in the peripheral itself (s32k1xx
# FlexCAN: CTRL1.CLKSRC between the oscillator clock and the bus clock).
ClockSource = Literal["SOSC", "SIRC", "FIRC", "SPLL"]
ClockDomain = Literal["bus", "core", "none"]        # clocks without a select and without an output divider
Divider = Literal[1, 2]


class ClockChoice(Strict):
    """One clock of a peripheral: source and the asynchronous output of that source it is wired to."""

    select: ClockSource = Field(description="Clock source that feeds the instance (s32k1xx: SOSC, SIRC, FIRC or "
                                            "SPLL, the value of PCCn.PCS).")
    divider: Divider | None = Field(None, description="Asynchronous output of the source the clock is taken from: "
                                                      "1 for DIV1, 2 for DIV2. In a project entry it may be left "
                                                      "out when the derivative allows only one output.")


# ARCH-18 (decision of the user, 2026-09-16) splits fact and decision; PLAT-22 finding 1: no invented defaults.
class InstanceClock(Fact):
    """Functional clock of one peripheral instance (s32k1xx: clock gate of the PCC, fields PCCn.PCS/CGC).

    Fact and decision are split (doc/architecture/README.md, section 4):

    - the derivative names what the hardware allows - the selectable sources (`sources`), the asynchronous
      outputs the instance is wired to (`divider`) and the limit of the functional clock (`max_hz`), each with
      its source like every mcudb value,
    - the project decides which of them is used (project.yaml peripheral_clocks.<instance>),
    - `default` is the choice that applies to a project which decides nothing. Without it an instance the project
      uses and does not choose a clock for is an error, so no value has to be invented for a derivative whose
      data sheet values are missing.

    An instance without a clock select (`select` bus, core or none) has no `sources`: there is nothing to decide.
    Neither has an instance that runs on a named clock domain of the global clock module (`domain`, rm4x: VCLK,
    VCLK2, VCLKA1): its frequency follows from the domain the family clock model sets up.
    """

    sources: list[ClockSource] = Field([], description="Clock sources the clock select of the instance offers. "
                                                       "Empty for an instance without clock select (then `select` "
                                                       "is set).")
    divider: list[Divider] = Field([], description="Asynchronous outputs DIVx of the sources the instance is wired "
                                                   "to (1: DIV1, 2: DIV2).")
    default: ClockChoice | None = Field(None, description="Clock choice that applies when the project names none. "
                                                          "Left out: a project that uses the instance must choose "
                                                          "its clock in `peripheral_clocks`.")
    max_hz: PositiveInt | None = Field(None, description="Maximum functional clock of the instance (limit of the "
                                                         "DIVx output) as the reference manual states it. Left "
                                                         "out: no limit stated.")
    select: ClockDomain | None = Field(None, description="For an instance without clock select: `bus` or `core` "
                                                         "(runs on that clock), `none` (the clock is chosen inside "
                                                         "the peripheral, e.g. s32k1xx FlexCAN CTRL1.CLKSRC).")
    # PLAT-69 (proposal of PLAT-36): peripheral on a named domain of the global clock module (rm4x)
    domain: Name | None = Field(None, description="For an instance without clock select of its own that runs on a "
                                                  "named clock domain of the global clock module (rm4x: `VCLK`, "
                                                  "`VCLK2`, `VCLKA1`): the name of that domain, a key of "
                                                  "`clock.max_hz`. Given alone, without `sources`, `select`, "
                                                  "`divider`, `default` and `max_hz`.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _sources_or_domain(self):
        if self.domain is not None:
            given = [key for key in ("sources", "select", "divider", "default", "max_hz")
                     if getattr(self, key) not in (None, [])]
            if given:
                raise ValueError(f"clock domain {self.domain} has no clock select: give domain alone, without "
                                 f"{', '.join(given)}")
            return self
        if bool(self.sources) == (self.select is not None):
            raise ValueError("clock entry needs either sources (with divider), select (bus, core or none) or domain")
        if self.select is not None:
            for key in ("divider", "default", "max_hz"):
                if getattr(self, key) not in (None, []):
                    raise ValueError(f"clock {self.select} has no clock select and therefore no {key}")
            return self
        if not self.divider:
            raise ValueError(f"clock sources {', '.join(self.sources)} need divider (the asynchronous outputs "
                             "DIV1/DIV2 the instance is wired to, e.g. [2])")
        if self.default is not None:
            if self.default.select not in self.sources:
                raise ValueError(f"default clock {self.default.select} is not one of the sources "
                                 f"{', '.join(self.sources)}")
            if self.default.divider is None:
                raise ValueError(f"default clock {self.default.select} needs divider "
                                 f"({' or '.join(str(d) for d in self.divider)})")
            if self.default.divider not in self.divider:
                raise ValueError(f"default divider {self.default.divider} is not one of the outputs "
                                 f"{', '.join(str(d) for d in self.divider)}")
        return self

    def only_divider(self) -> int | None:
        """The output a project does not have to name because it is the only one; None if there is a choice."""
        return self.divider[0] if len(self.divider) == 1 else None


class Instance(Fact):
    """One peripheral instance of the derivative. `class_` is family neutral (timer, not FTM or TIM)."""

    # PLAT-69: gpio for GPIO ports that are a peripheral instance of their own (rm4x GIOA/GIOB)
    class_: Literal["adc", "timer", "spi", "can", "uart", "i2c", "flexio", "ethernet", "qspi", "i2s", "gpio"] = \
        Field(alias="class", description="Family-neutral kind of the peripheral (`timer`, not FTM or TIM; `gpio` "
                                         "for a GPIO port that is an instance of its own, rm4x GIOA). Decides "
                                         "which board and project entries may use the instance.")
    channels: PositiveInt | None = Field(None, description="Timer: number of channels of the instance. GPIO port: "
                                                           "number of pins of the port (their pin function is "
                                                           "`GPIO` in the pin multiplexing table).",
                                         json_schema_extra=NO_UNIT)
    first_channel: NonNegativeInt = Field(0, description="Timer only: number of the first channel, 1 where the "
                                                         "manufacturer counts from 1 (TIM1_CH1).",
                                          json_schema_extra=NO_UNIT)
    resolution_bits: PositiveInt | None = Field(None, description="ADC only: resolution of the converter.")
    channel_pattern: str | None = Field(None, description="ADC only: regular expression of the external channel "
                                                          "names (e.g. `SE([0-9]|1[0-5])`), checked against "
                                                          "`adc[].channel` of the board.")
    internal_channels: list[str] = Field([], description="ADC only: names of the inputs without pin (bandgap, "
                                                         "reference, supply monitor) a project may use in "
                                                         "`adc_internal`.")
    message_buffers: PositiveInt | None = Field(None, description="CAN only: number of message buffers (mailboxes) "
                                                                  "of the instance. Left out: unknown.",
                                                json_schema_extra=NO_UNIT)
    clock: InstanceClock | None = Field(None, description="Functional clock of the instance. Left out: unknown; a "
                                                          "project that uses the instance gets an error.")
    verify: bool = Field(False, description=VERIFY)


class DerivativeInfo(Fact):
    """Identification and core of the derivative, packages and where its facts come from."""

    name: Name = Field(description="Derivative name as the manufacturer writes it (e.g. `S32K144`); boards refer "
                                   "to it in `board.mcu`.")
    family: Name = Field(description="Family of the derivative (e.g. `s32k1xx`, `stm32`): selects the family "
                                     "clock model, the generators and the platform code.")
    core: str = Field(description="Processor core, a key of the core table of tools/nxconfig (e.g. `cortex-m4f`).")
    fpu: Literal["none", "sp", "dp"] | None = Field(None, description="FPU variant: none, single precision (sp) or "
                                                                      "double precision (dp). Left out: the only "
                                                                      "variant the core has.")
    nvic_priority_bits: Annotated[int, Field(ge=2, le=8)] | None = Field(
        None, description="NVIC cores: number of implemented interrupt priority bits.",
        json_schema_extra={"unit": "bit"})
    features: list[Literal["lmem_cache"]] = Field([], description="Optional core features: `lmem_cache` is the LMEM "
                                                                  "processor code cache (S32K14x), switched on at "
                                                                  "start-up.")
    packages: list[str] = Field(min_length=1, description="Package names of the derivative (e.g. `LQFP100`); "
                                                          "`board.package` is one of them.")
    pin_pattern: str = Field(description="Regular expression of the pin names; every pin a board names must match "
                                         "it.")
    sources: list[str] = Field([], description="Documents and repository files the facts of this file come from.")
    verify: bool = Field(False, description="`true`: the entry as a whole is not confirmed by a source in the "
                                            "repository.")
    example: bool = Field(False, description="`true`: format example only, not for real projects; using it gives a "
                                             "warning.")
    jlink_device: str | None = Field(None, description="J-Link device name, if it differs from the derivative name.")
    pyocd_target: str | None = Field(None, description="pyOCD target type, if it differs from the derivative name in "
                                                       "lower case.")
    openocd_target: str | None = Field(None, description="OpenOCD target script, e.g. `target/stm32f4x.cfg`.")


class SectorGroup(Strict):
    """`count` consecutive flash sectors of `size` bytes (sector table of a flash with unequal sectors)."""

    size: PositiveInt = Field(description="Size of each sector of the group.", json_schema_extra=BYTES)
    count: PositiveInt = Field(description="Number of consecutive sectors of this size.", json_schema_extra=NO_UNIT)


class MemoryRegion(Fact):
    """Memory of the derivative. Partitioning (bootloader, application, NvM) is decided by the project."""

    name: Name = Field(description="Region name (e.g. `PFLASH`, `SRAM_L`); the project refers to it in `memory`.")
    kind: Literal["code_flash", "data_flash", "ram", "eeprom_ram"] = Field(
        description="Kind of memory: program flash, data flash, RAM or the RAM window of an emulated EEPROM.")
    start: NonNegativeInt = Field(description="Start address of the region.", json_schema_extra=NO_UNIT)
    size: PositiveInt = Field(description="Size of the region.", json_schema_extra=BYTES)
    sector_size: PositiveInt | None = Field(None, description="Flash: smallest erasable unit. A flash region needs "
                                                              "it or `sectors`.",
                                            json_schema_extra=BYTES)
    sectors: list[SectorGroup] | None = Field(None, description="Flash with unequal sectors (e.g. STM32F4): sector "
                                                                "table from the start of the region.")
    program_size: PositiveInt | None = Field(None, description="Flash: smallest programmable unit (S32K1 phrase: "
                                                               "8 bytes).",
                                             json_schema_extra=BYTES)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)

    def sector_table(self) -> list[tuple[int, int]]:
        """(start, size) of every sector; empty for regions without sector data."""
        if self.sectors is not None:
            groups = [(g.size, g.count) for g in self.sectors]
        elif self.sector_size is not None:
            groups = [(self.sector_size, self.size // self.sector_size)]
        else:
            return []
        table, address = [], self.start
        for size, count in groups:
            for _ in range(count):
                table.append((address, size))
                address += size
        return table


class PinmuxRef(Fact):
    """Pin multiplexing of one package: generated table file or (examples, tests) an inline table."""

    file: str | None = Field(None, description="Generated table `<part>.pinmux.yaml`, relative to the derivative "
                                               "file. Exactly one of `file` and `signals`.")
    signals: SignalTable | None = Field(None, description="Inline table (format examples, tests): signal -> pin -> "
                                                          "function selector, as in the pin multiplexing file.",
                                        json_schema_extra=NO_UNIT)
    complete: bool = Field(True, description="`false`: partial data; mismatches with the table are warnings instead "
                                             "of errors.")
    verify: bool = Field(False, description="`true`: the table is taken from another package or not confirmed.")
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _file_or_signals(self):
        if (self.file is None) == (self.signals is None):
            raise ValueError("pinmux entry needs exactly one of file and signals")
        return self


class DeviceHeader(Strict):
    """Vendor device header the family code includes for this derivative (mcu.h)."""

    file: str = Field(description="Include path of the header, e.g. `3rdparty/nxp/S32K148.h`.")
    describes: Name = Field(description="Derivative the header was written for; may be a superset of this one.")
    source: str | None = Field(None, description="Where the header comes from or which file adapts it.")
    # PLAT-48: the SVD generation of PLAT-12 is gone; svd2header.py stays a workstation cross-check tool only.
    # PLAT-69 (proposal of PLAT-36, decision F3): regdesc2header for a family without SVD (rm4x).
    generated_by: Literal["regdesc2header"] | None = Field(
        None, description="The header is not in the repository but generated at configure time. `regdesc2header`: "
                          "from the register description `mcudb/<family>/registers/*.yaml`, written from the "
                          "reference manual for a family without SVD, with `mcudb/<family>/tools/regdesc2header.py`. "
                          "Left out: the header must be in the include path.")


Bounds = Annotated[list[PositiveInt], Field(min_length=2, max_length=2)]     # [min, max]


class OscillatorLimits(Fact):
    """External oscillator input (crystal or clock) the derivative accepts."""

    min_hz: PositiveInt = Field(description="Lowest crystal or clock frequency the oscillator input accepts.")
    max_hz: PositiveInt = Field(description="Highest crystal or clock frequency the oscillator input accepts.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


class PllLimits(Fact):
    """PLL fed by the oscillator: output = oscillator / input_divider * multiplier / output_divider."""

    input_hz: Bounds = Field(description="[min, max] of the PLL reference after the input divider.")
    vco_hz: Bounds = Field(description="[min, max] of the VCO frequency (reference * multiplier).")
    input_divider: Bounds = Field(description="[min, max] of the input divider between oscillator and PLL "
                                              "reference.",
                                  json_schema_extra=NO_UNIT)
    multiplier: Bounds = Field(description="[min, max] of the feedback multiplier.", json_schema_extra=NO_UNIT)
    output_dividers: list[PositiveInt] = Field(min_length=1, description="Dividers between VCO and PLL output the "
                                                                         "hardware offers (s32k1xx: fixed 2). With "
                                                                         "`output_divider_stages`: every product of "
                                                                         "the stages, ascending.",
                                               json_schema_extra=NO_UNIT)
    # PLAT-69 (proposal of PLAT-36): rm4x divides in two stages, OD then R, with a limit between them (post-ODCLK)
    output_divider_stages: list[Bounds] = Field(
        [], description="Output divider in stages, in signal order: [min, max] of each stage (rm4x: OD 1..8, then R "
                        "1..32). The PLL output is VCO / (product of the stages). Left out: one stage with the "
                        "dividers of `output_dividers`.",
        json_schema_extra=NO_UNIT)
    post_divider_input_max_hz: PositiveInt | None = Field(
        None, description="Maximum of the clock between the first and the second output divider stage (rm4x: "
                          "post-ODCLK = VCO / OD). Needs `output_divider_stages`. Left out: no limit stated.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)

    @model_validator(mode="after")
    def _stages(self):
        stages = self.output_divider_stages
        if self.post_divider_input_max_hz is not None and len(stages) < 2:
            raise ValueError("post_divider_input_max_hz limits the clock between two output divider stages: "
                             "output_divider_stages needs at least two stages")
        if not stages:
            return self
        if any(low > high for low, high in stages):
            raise ValueError("output_divider_stages: minimum above maximum")
        products = {1}
        for low, high in stages:
            products = {p * d for p in products for d in range(low, high + 1)}
        if sorted(products) != self.output_dividers:
            raise ValueError(f"output_dividers must list every product of output_divider_stages in ascending order "
                             f"({len(products)} values from {min(products)} to {max(products)})")
        return self


# ARCH-22
class ClockLimits(Fact):
    """Maximum frequencies of named clocks with their evidence (fact of the derivative)."""

    max_hz: dict[Name, PositiveInt] = Field(min_length=1, description="Maximum frequency per clock; key: clock or "
                                                                      "clock output name of the family clock model.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)


# SYS-53 (open point of SYS-25): frequency range of the low power oscillator, a fact of the data sheet
class LowPowerOscillator(Fact):
    """Low power oscillator (LPO) of the derivative: always-on clock of the MCU watchdog and of other supervision
    timers (s32k1xx: the 128 kHz LPO of the PMC feeds the WDOG and, as LPO1K_CLK, the LPTMR of the period check).
    Timing that has to hold at every LPO of the data sheet (watchdog service interval, bounds of the period check) is
    derived from `min_hz` and `max_hz` by tools/nxconfig, never from the nominal value alone."""

    nominal_hz: PositiveInt = Field(description="Typical frequency of the data sheet, the value the drivers load "
                                                "their counters for (s32k1xx: 128 kHz).",
                                    json_schema_extra=HZ)
    min_hz: PositiveInt = Field(description="Lowest frequency over process, voltage and temperature (data sheet "
                                            "minimum).",
                                json_schema_extra=HZ)
    max_hz: PositiveInt = Field(description="Highest frequency over process, voltage and temperature (data sheet "
                                            "maximum).",
                                json_schema_extra=HZ)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _ordered(self):
        if not self.min_hz <= self.nominal_hz <= self.max_hz:
            raise ValueError(f"needs min_hz <= nominal_hz <= max_hz, got {self.min_hz}, {self.nominal_hz}, "
                             f"{self.max_hz}")
        return self


class FlashWaitBand(Strict):
    """Flash read wait states that apply up to one frequency of the flash clock."""

    max_hz: PositiveInt = Field(description="Highest frequency of the flash clock this band covers; the next band "
                                            "starts above it.")
    data: NonNegativeInt = Field(description="Data (random read) wait states (rm4x: FRDCNTL.RWAIT, for the EEPROM "
                                             "emulation bank EEPROM_CONFIG.EWAIT).",
                                 json_schema_extra=NO_UNIT)
    address: NonNegativeInt = Field(0, description="Address setup wait states (rm4x: FRDCNTL.ASWSTEN).",
                                    json_schema_extra=NO_UNIT)
    pipeline: bool = Field(False, description="`true`: the flash pipeline mode is required in this band (rm4x: "
                                              "FRDCNTL.ENPIPE).")


# PLAT-69 (proposal of PLAT-36): the start-up programs the wait states before it raises the system clock. One entry
# with one source for the whole table (like vim.fixed), not a source per band: the bands come from one figure.
class FlashWaitStates(Fact):
    """Flash read wait states per band of the flash clock. The family code picks the band of the configured
    frequency and writes its values; how they are encoded in registers is family knowledge."""

    clock: Name = Field(description="Clock the bands refer to, a key of `clock.max_hz` (rm4x: `HCLK`).")
    bands: list[FlashWaitBand] = Field(min_length=1, description="Bands in strictly ascending `max_hz`; the last "
                                                                 "one covers the maximum of `clock`.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _ascending(self):
        limits = [b.max_hz for b in self.bands]
        if any(low >= high for low, high in zip(limits, limits[1:])):
            raise ValueError(f"bands need strictly ascending max_hz, got {', '.join(str(x) for x in limits)}")
        return self


# PLAT-70: inputs of the rm4x clock model (global clock module with numbered sources and named domains). Former
# comments of mcudb/rm4x/RM46L852.yaml; each entry with its own source like every derivative value.
class ClockSourceNumbers(Fact):
    """Number of every clock source in the source select fields of the clock module (rm4x: the value of GHVSRC,
    VCLKASRC and RCLKSRC and the bit in CSDISx). The family generator writes these numbers; the clock model uses
    the names."""

    numbers: dict[Name, NonNegativeInt] = Field(
        min_length=1, description="Source name -> number (rm4x: `OSCIN: 0`, `PLL1: 1`, `HFLPO: 5`). Numbers the "
                                  "document marks reserved are left out.",
        json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _unique(self):
        owner: dict[int, str] = {}
        for name, number in self.numbers.items():
            if number in owner:
                raise ValueError(f"numbers.{name}: number {number} is already the number of {owner[number]}")
            owner[number] = name
        return self


class OscillatorMonitor(Fact):
    """Clock monitor of the oscillator input: the oscillator counts as failed outside the window
    reference / factor < f < reference * factor (rm4x CLKDET: all domains fed by OSCIN switch to HFLPO)."""

    reference: Name = Field(description="Internal clock the monitor compares with, a key of `clock.internal` (rm4x: "
                                        "`HFLPO`).")
    factor: PositiveInt = Field(description="Width of the window: the oscillator has to lie above reference / factor "
                                            "and below reference * factor (both limits excluded).",
                                json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)


def _ascending_unique(values: list[int], what: str):
    if any(low >= high for low, high in zip(values, values[1:])):
        raise ValueError(f"{what} need strictly ascending values, got {', '.join(str(x) for x in values)}")


class OtherSourceDivider(Strict):
    """Divider and limit of a clock domain while it runs from a source other than its `parent`."""

    dividers: list[PositiveInt] = Field(min_length=1, description="Dividers that apply to the other source, "
                                                                  "ascending (rm4x RTICLK: 1, 2, 4, 8).",
                                        json_schema_extra=NO_UNIT)
    max_of: Name = Field(description="Clock whose fraction limits the domain from another source (rm4x RTICLK: "
                                     "`VCLK`).")
    divisor: PositiveInt = Field(description="The domain from another source runs at most `max_of` / divisor (rm4x "
                                             "RTICLK: 3).",
                                 json_schema_extra=NO_UNIT)

    @model_validator(mode="after")
    def _ascending(self):
        _ascending_unique(self.dividers, "dividers")
        return self


class DomainDivider(Fact):
    """How a named clock domain is derived from its parent clock (rm4x: VCLK = HCLK / 1..16). The family clock
    model takes the smallest divider that keeps the domain within `clock.max_hz.<domain>`."""

    parent: Name = Field(description="Clock the domain is divided from: a system clock of the family model (rm4x "
                                     "`HCLK`), another key of `domain_dividers` or a clock source of "
                                     "`source_numbers`. Where the domain has a source select, the source the "
                                     "family model uses (its reset default).")
    dividers: list[PositiveInt] = Field(min_length=1, description="Dividers between parent and domain, ascending; "
                                                                  "`[1]` for a domain that follows its parent "
                                                                  "undivided.",
                                        json_schema_extra=NO_UNIT)
    multiple_of: Name | None = Field(None, description="The frequency of the domain has to be an integer multiple of "
                                                       "the frequency of this other domain (rm4x: VCLK2 of VCLK). "
                                                       "Left out: no such rule.")
    other_source: OtherSourceDivider | None = Field(
        None, description="Dividers and limit that apply only while the domain runs from a source other than "
                          "`parent` (rm4x RTICLK: divider 1/2/4/8, at most VCLK/3; from VCLK the divider is "
                          "bypassed). Left out: no other source described.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _ascending(self):
        _ascending_unique(self.dividers, "dividers")
        return self


class DerivativeClock(Fact):
    """Clock facts of the derivative. Which source feeds what is the family clock model (tools/nxconfig/clock.py)."""

    internal: dict[Name, PositiveInt] = Field({}, description="Frequency of each internal oscillator; key: its name "
                                                              "(e.g. `FIRC: 48000000`).",
                                              json_schema_extra=HZ)
    oscillator: OscillatorLimits | None = Field(None, description="External oscillator input. Left out: the "
                                                                  "derivative has none.")
    pll: PllLimits | None = Field(None, description="PLL limits. Left out: the derivative has no PLL.")
    # SYS-53: the LPO range replaces the per-mille constants of the family header (LptmrCycleTimer.h, SYS-40)
    lpo: LowPowerOscillator | None = Field(
        None, description="Frequency range of the low power oscillator that clocks the MCU watchdog. Left out: not "
                          "described; a family whose watchdog runs on it (s32k1xx) cannot derive its safety timing "
                          "then (error at `clock.lpo`).")
    max_hz: dict[Name, PositiveInt] = Field({}, description="Maximum frequency per clock domain (`core`, `bus`, "
                                                            "...) in the run mode the drivers use.")
    # ARCH-22: stricter domain maxima that apply only while a given source feeds the system clock.
    system_source_max_hz: dict[Name, ClockLimits] = Field(
        {}, description="Stricter domain maxima that apply only while a given source feeds the system clock (s32k14x: "
                        "BUS_CLK 40 MHz with the SPLL as system clock). Key: system clock source of the family clock "
                        "model.")
    # ARCH-22: maximum of every asynchronous clock output in the supported run mode.
    output_max_hz: ClockLimits | None = Field(
        None, description="Maximum of every asynchronous clock output in the run mode the drivers use (s32k1xx: "
                          "SPLLDIV1, FIRCDIV2, ...). The family clock model chooses the output dividers from it (the "
                          "smallest divider within the limit). Left out: the outputs stay undivided.")
    # PLAT-22: clock gates without an instance of `instances` because no generator configures the peripheral yet.
    pcc: dict[Name, InstanceClock] = Field(
        {}, description="Clock gates of peripherals that are not listed in `instances` because no generator "
                        "configures them yet, but whose clock the clock driver switches on (s32k1xx: LPIT, LPTMR0). "
                        "Key: name of the gate.")
    flash_wait_states: FlashWaitStates | None = Field(
        None, description="Flash read wait states per band of the flash clock. Left out: not described (the flash "
                          "needs none, or the family code does not configure them).")
    # PLAT-70: inputs of the rm4x clock model
    source_numbers: ClockSourceNumbers | None = Field(
        None, description="Number of every clock source in the source select fields (rm4x GHVSRC). Needed by a "
                          "family clock model with numbered sources (rm4x). Left out: the family has none.")
    external_max_hz: ClockLimits | None = Field(
        None, description="Maximum of every external clock input (rm4x `EXTCLKIN1`, `EXTCLKIN2`); keys are names of "
                          "`source_numbers`. Left out: no external clock input described.")
    oscillator_monitor: OscillatorMonitor | None = Field(
        None, description="Window of the oscillator clock monitor. Left out: no monitor described; the board's "
                          "oscillator is only checked against `oscillator`.")
    domain_dividers: dict[Name, DomainDivider] = Field(
        {}, description="How each named clock domain is derived (rm4x: VCLK, VCLK2, VCLKA1, RTICLK); key: domain "
                        "name, which is also its key in `max_hz`. Peripheral instances name their domain in "
                        "`instances.<x>.clock.domain`.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


# ARCH-16
class DerivativeMpu(Fact):
    """Memory protection unit of the derivative: how many region descriptors the hardware implements.

    Which unit it is follows from the family (s32k1xx: NXP system MPU at the crossbar, no Arm core MPU), and so
    does the register encoding of the count (MPU_CESR[NRGD]); that knowledge belongs to the generator.
    """

    regions: PositiveInt = Field(description="Number of region descriptors the hardware implements (RGD[0] to "
                                             "RGD[regions-1]).",
                                 json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


# BL-03
class DerivativeUid(Fact):
    """Unique identification of the chip: the bootloader derives nothing from it itself, it reports it (DID
    0xF18C) and the tester derives the device key of the chip from it (HMAC-SHA256 of the project's master key over
    these bytes). `size` bytes of memory mapped 32-bit words from `address`, read in address order, each word most
    significant byte first (s32k1xx: SIM_UIDH..SIM_UIDL, UID127..UID0; stm32: UID[31:0] first)."""

    address: NonNegativeInt = Field(description="Address of the first 32-bit word of the UID.",
                                    json_schema_extra=NO_UNIT)
    size: Annotated[int, Field(ge=4, le=32, multiple_of=4)] = Field(
        description="Length of the UID, whole 32-bit words.", json_schema_extra=BYTES)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


# NVM-07: partition of an emulated EEPROM whose split is programmed once into the chip (s32k1xx FlexNVM: FTFC command
# Program Partition, PGMPART, with the EEPROM data set size code EEESIZE and the FlexNVM partition code DEPART). The
# codes are the derivative's: the same split has other codes on another derivative, and the register encoding
# of the command (FCCOB layout, which code goes where) belongs to the family code.
Code4 = Annotated[int, Field(ge=0, le=0xF)]


class EepromSizeCode(Strict):
    """One EEPROM size the partition command accepts (s32k1xx: EEPROM data set size code, EEESIZE)."""

    code: Code4 = Field(description="Code of the size in the partition command (s32k1xx: FCCOB4[3:0]).",
                        json_schema_extra=NO_UNIT)
    eeprom_bytes: NonNegativeInt = Field(description="Bytes of emulated EEPROM the code gives (0: none).",
                                         json_schema_extra=BYTES)


class PartitionCode(Strict):
    """One split of the partitionable flash the partition command accepts (s32k1xx: FlexNVM partition code,
    DEPART). Several codes may give the same split; the project then names the one it wants."""

    code: Code4 = Field(description="Code of the split in the partition command (s32k1xx: FCCOB5[3:0]).",
                        json_schema_extra=NO_UNIT)
    data_flash_bytes: NonNegativeInt = Field(description="Bytes that stay data flash.", json_schema_extra=BYTES)
    backup_bytes: NonNegativeInt = Field(description="Bytes that back up the emulated EEPROM (EEPROM backup, not "
                                                     "addressable any more).",
                                         json_schema_extra=BYTES)


class KeySpaceCode(Strict):
    """NVM-11: one size of the key space of a hardware security module the partition command reserves in the
    emulated EEPROM (s32k1xx: CSEc key size, FCCOB1[1:0]). The bytes come off the end of the emulated EEPROM."""

    code: Annotated[int, Field(ge=0, le=0xFF)] = Field(description="Code of the key space in the partition command "
                                                                   "(s32k1xx: FCCOB1).",
                                                       json_schema_extra=NO_UNIT)
    slots: NonNegativeInt = Field(description="Key slots reserved (0: none).", json_schema_extra=NO_UNIT)
    system_slots: NonNegativeInt = Field(0, description="Slots of these the module itself occupies once any slot is "
                                                        "reserved (s32k1xx: MASTER_ECU_KEY, BOOT_MAC_KEY, BOOT_MAC).",
                                         json_schema_extra=NO_UNIT)
    eeprom_bytes: NonNegativeInt = Field(description="Bytes of emulated EEPROM the key space takes.",
                                         json_schema_extra=BYTES)


class EnduranceStep(Strict):
    """NVM-11: guaranteed writes of the emulated EEPROM at one ratio of EEPROM backup to the EEPROM in use (data
    sheet). Below the smallest ratio the data sheet states nothing."""

    ratio: PositiveInt = Field(description="EEPROM backup bytes per byte of emulated EEPROM in use.",
                               json_schema_extra=NO_UNIT)
    writes: PositiveInt = Field(description="Guaranteed writes at this ratio (data sheet, min).",
                                json_schema_extra=NO_UNIT)


class BackupTime(Strict):
    """Longest write to the emulated EEPROM for one backup size (data sheet)."""

    backup_bytes: PositiveInt = Field(description="EEPROM backup size the time applies to.", json_schema_extra=BYTES)
    write_max_us: PositiveInt = Field(description="Longest time of one 32-bit write to the emulated EEPROM "
                                                  "(s32k1xx data sheet: teewr32b, max).")
    partition_typ_ms: PositiveInt | None = Field(None, description="Typical time of the partition command (s32k1xx "
                                                                   "data sheet: tpgmpart). Left out: not stated.")


class EepromEmulationTiming(Fact):
    """Write times of the emulated EEPROM. The generator derives the time limit of one write from them (NVM-07:
    the limit of the EEPROM driver in time, not in polls)."""

    backups: list[BackupTime] = Field(min_length=1, description="Times per EEPROM backup size; a split without its "
                                                                "own entry uses the next larger backup.")
    first_write_factor: PositiveInt = Field(1, description="Factor on the longest write for the first writes after a "
                                                           "reset (s32k1xx data sheet: up to 2x for EEE clean-up).",
                                            json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


class DerivativeEepromEmulation(Fact):
    """Partition of the emulated EEPROM (NVM-07): the codes of the one-time partition command of the derivative.

    The emulated EEPROM is read and written through the RAM window of kind `eeprom_ram` in `memory`; its backup and
    the data flash share the region of kind `data_flash`. `data_flash_bytes + backup_bytes` of every code is that
    region's size. What the project chooses is `nvm.partition` of project.yaml.
    """

    sizes: list[EepromSizeCode] = Field(min_length=1, description="EEPROM sizes the command accepts.")
    partitions: list[PartitionCode] = Field(min_length=1, description="Splits of the partitionable flash the command "
                                                                      "accepts.")
    timing: EepromEmulationTiming | None = Field(None, description="Write times. Left out: the EEPROM driver keeps "
                                                                   "its own conservative time limit.")
    keys: list[KeySpaceCode] = Field([], description="NVM-11: key space sizes of the security module the partition "
                                                     "command accepts (s32k1xx CSEc: FCCOB1). Empty: the derivative "
                                                     "has no key space; the project must not ask for one.")
    endurance: list[EnduranceStep] = Field([], description="NVM-11: guaranteed writes per ratio of backup to EEPROM "
                                                           "in use (data sheet). Empty: not stated.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)


class VimFixedChannel(Strict):
    """VIM channel whose request the hardware fixes (e.g. an FIQ-only channel); interrupt ranks never get it."""

    channel: NonNegativeInt = Field(description="VIM channel number.", json_schema_extra=NO_UNIT)
    request: str = Field(description="What the channel serves, as the source document names it.")
    kind: Literal["irq", "fiq"] = Field(description="Interrupt kind of the channel; `fiq` never calls the RTOS.")
    note: str | None = Field(None, description=NOTE)


# ARCH-23
class DerivativeVim(Fact):
    """Vectored interrupt manager of the derivative (core with interrupt controller `vim`).

    The priority of a request is its channel number (0 highest). The project gives ranks as with an NVIC; checks.py
    turns them into a channel order over the channels that are not fixed. How many channels an instance needs and
    how the requests are moved to their channels is known only to the family generator.
    """

    channels: Annotated[int, Field(ge=2)] = Field(description="Number of VIM channels (channel 0 to channels-1).",
                                                  json_schema_extra=NO_UNIT)
    fixed: list[VimFixedChannel] = Field([], description="Channels the hardware fixes to a request; they are "
                                                         "never given to a ranked interrupt.")
    # PLAT-69 (proposal of PLAT-36): input of the family generator that writes the channel mapping
    requests: dict[Name, NonNegativeInt] = Field(
        {}, description="Default channel of every interrupt request (request name -> channel after reset). The "
                        "family generator moves a request to the channel of its rank with the channel mapping "
                        "(rm4x: CHANCTRL) and needs to know where each request starts. Key: request name as the "
                        "family code uses it (e.g. `DCAN1_LEVEL0`).",
        json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)

    @model_validator(mode="after")
    def _request_channels(self):
        owner: dict[int, str] = {}
        for request, channel in self.requests.items():
            if channel >= self.channels:
                raise ValueError(f"requests.{request}: channel {channel} does not exist (channels 0.."
                                 f"{self.channels - 1})")
            if channel in owner:
                raise ValueError(f"requests.{request}: channel {channel} is already the default channel of "
                                 f"{owner[channel]}")
            owner[channel] = request
        return self


# IO-51 (concept doc/architecture/pin-interrupts.md, section 2: the capability is a fact of the derivative): which
# pins can raise an interrupt and how the edge detectors reach the interrupt vectors. The model is family neutral
# and knows two building blocks, so that no family case shapes it:
#   - a vector with `pins`: every matching pin has an edge detector of its own, all of them end in this vector
#     (s32k1xx S32K14x: PORTA..PORTE, one per port; S32K11x: one vector PORT for all ports; rm4x: GIOA/GIOB pins)
#   - a line: ONE edge detector that serves one pin at a time, chosen from its `pins` or from the pins of a pin
#     multiplexing `signal` (stm32 EXTI n: pin n of one port; s32k3xx SIUL2 EIRQ n: pins of signal EIRQn); vectors
#     then list the lines they serve (stm32 EXTI9_5, s32k3xx SIUL2_0 = EIRQ0..7)
# A pin that matches several vectors can be routed to either of them (rm4x: GIO level A or B per pin); the family
# generator chooses by rank. Register codes (IRQC, EXTICR, GIOLVLSET) stay in the family code.
PinEdge = Literal["rising", "falling", "both"]


def _pin_regex(value: str | None, what: str):
    if value is not None:
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"{what}: invalid regular expression '{value}' ({e})") from None


class PinInterruptLine(Strict):
    """Edge detector that serves one pin at a time (stm32 EXTI line, s32k3xx SIUL2 EIRQ)."""

    name: Name = Field(description="Line name as the reference manual writes it (e.g. `EXTI3`, `EIRQ5`); vectors "
                                   "refer to it in `lines`.")
    pins: str | None = Field(None, description="Regular expression of the pins the line can be connected to "
                                               "(e.g. `P[A-H]3`). Exactly one of `pins` and `signal`.")
    signal: str | None = Field(None, description="Signal of the pin multiplexing table whose pins the line can be "
                                                 "connected to (e.g. `EIRQ5`); the pins then depend on the package. "
                                                 "Exactly one of `pins` and `signal`.")
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _pins_or_signal(self):
        if (self.pins is None) == (self.signal is None):
            raise ValueError(f"line {self.name} needs exactly one of pins and signal")
        _pin_regex(self.pins, f"line {self.name}")
        return self


class PinInterruptVector(Strict):
    """Interrupt vector (NVIC) or request (VIM) that pin edges arrive at."""

    name: Name = Field(description="Vector name as the family code uses it: the interrupt of the device header "
                                   "without `_IRQn` on an NVIC core (e.g. `PORTC`, `EXTI9_5`), a key of "
                                   "`vim.requests` on a VIM core (e.g. `GIO_A`).")
    pins: str | None = Field(None, description="Regular expression of the pins with an edge detector of their own "
                                               "that end in this vector (e.g. `PTC([0-9]|1[0-7])`). Exactly one of "
                                               "`pins` and `lines`.")
    lines: list[Name] = Field([], description="Lines (`lines[].name`) that end in this vector. Exactly one of `pins` "
                                              "and `lines`.")
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _pins_or_lines(self):
        if (self.pins is None) == (not self.lines):
            raise ValueError(f"vector {self.name} needs exactly one of pins and lines")
        _pin_regex(self.pins, f"vector {self.name}")
        return self


@dataclass(frozen=True)
class PinInterruptRoute:
    """One way a pin reaches a vector: through its own edge detector (line None) or through a line."""

    vector: str
    line: str | None


class DerivativePinInterrupts(Fact):
    """Pins that can raise an interrupt on an edge, their lines and vectors (IO-51).

    What the project decides (which input raises events, edge, debounce, rate limit, rank) is `pin_events` of
    project.yaml; nxconfig checks it against these facts: the pin must be reachable, pins that share a vector share
    its priority (same rank), and pins that need the same line exclude each other.
    """

    edges: list[PinEdge] = Field(["rising", "falling", "both"], description="Edges the edge detectors can react to: "
                                                                            "`rising`, `falling`, `both` (both "
                                                                            "edges at once).")
    lines: list[PinInterruptLine] = Field([], description="Edge detectors that serve one pin at a time. Empty: every "
                                                          "interrupt-capable pin has its own detector.")
    vectors: list[PinInterruptVector] = Field(min_length=1, description="Vectors the edges arrive at.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _references(self):
        lines = [line.name for line in self.lines]
        vectors = [vector.name for vector in self.vectors]
        for kind, names in (("line", lines), ("vector", vectors)):
            twice = sorted({n for n in names if names.count(n) > 1})
            if twice:
                raise ValueError(f"{kind} {', '.join(twice)} named twice")
        served: dict[str, str] = {}
        for vector in self.vectors:
            for line in vector.lines:
                if line not in lines:
                    raise ValueError(f"vector {vector.name}: unknown line {line} (lines: {', '.join(lines) or '-'})")
                if line in served:
                    raise ValueError(f"line {line} ends in two vectors, {served[line]} and {vector.name}")
                served[line] = vector.name
        orphans = [n for n in lines if n not in served]
        if orphans:
            raise ValueError(f"line {', '.join(orphans)} ends in no vector")
        return self

    def routes(self, pin: str, signal_pins=None) -> list[PinInterruptRoute]:
        """Every (vector, line) through which `pin` can raise an interrupt; empty: the pin cannot.

        `signal_pins(signal)` returns the pins of a pin multiplexing signal (lines with `signal`), None if the
        package has no table; such lines are then left out.
        """
        found: list[PinInterruptRoute] = []
        lines = {line.name: line for line in self.lines}
        for vector in self.vectors:
            if vector.pins is not None:
                if re.fullmatch(vector.pins, pin):
                    found.append(PinInterruptRoute(vector.name, None))
                continue
            for name in vector.lines:
                line = lines[name]
                if line.pins is not None:
                    reachable = re.fullmatch(line.pins, pin) is not None
                else:
                    pins = signal_pins(line.signal) if signal_pins is not None else None
                    reachable = pins is not None and pin in pins
                if reachable:
                    found.append(PinInterruptRoute(vector.name, name))
        return found


# SYS-86: analogue comparator with a reference DAC whose output can reach a fault input of a PWM timer without the
# core (s32k1xx: CMP0 -> TRGMUX -> FTMx fault input). What the derivative states: the resolution of the reference,
# the references the ladder can use, the electrical limits the threshold band is computed from and which fault
# inputs of which timers the output reaches. The inputs' pins come from the pin multiplexing table
# (`<name>_IN<n>`); the route itself (TRGMUX codes, SIM fault source select) is register knowledge of the family
# generator. Checks with YAML path: tools/nxconfig/hwthreshold.py.
ComparatorMv = Annotated[int, Field(ge=0, le=(1 << 31) - 1)]


class DerivativeComparator(Fact):
    """Analogue comparator with an internal reference DAC (hardware threshold, SYS-86)."""

    name: Name = Field(description="Comparator instance as in the reference manual (e.g. `CMP0`); the board names "
                                   "it in `comparators.inputs[].instance`, its inputs are the pin multiplexing "
                                   "signals `<name>_IN<n>`.")
    inputs: PositiveInt = Field(description="Number of analogue inputs of the input multiplexer (IN0 .. IN<n-1>).",
                                json_schema_extra=NO_UNIT)
    dac_bits: PositiveInt = Field(description="Resolution of the reference DAC: 2^bits levels, level k gives "
                                              "V_in * (k + 1) / 2^bits.", json_schema_extra=NO_UNIT)
    supply_reference: Name | None = Field(None, description="Supply pin of the MCU the DAC can use as V_in (e.g. "
                                                            "`VDDA`); the board states its voltage "
                                                            "(`comparators.supply`). Left out: not offered.")
    bandgap_mv: ComparatorMv | None = Field(None, description="Nominal internal bandgap reference the DAC can use as V_in. "
                                                    "Left out: not offered.")
    bandgap_min_mv: ComparatorMv | None = Field(None, description="Lowest bandgap voltage of the data sheet.")
    bandgap_max_mv: ComparatorMv | None = Field(None, description="Highest bandgap voltage of the data sheet.")
    offset_max_mv: ComparatorMv = Field(description="Largest input offset voltage of the comparator in the mode the family "
                                          "code uses (s32k1xx: high-speed), magnitude.")
    dac_inl_max_millilsb: Annotated[int, Field(ge=0)] = Field(
        description="Largest integral non-linearity of the DAC in 1/1000 LSB, magnitude.",
        json_schema_extra={"unit": "0.001 LSB"})
    propagation_max_ns: PositiveInt = Field(description="Largest propagation delay of the comparator in the mode the "
                                                        "family code uses, over the whole temperature range.")
    settling_max_us: PositiveInt = Field(description="Longest settling time of comparator and DAC after they are "
                                                     "switched on; the family code waits this long before the "
                                                     "output is connected to a fault input.")
    fault_inputs: dict[Name, list[Annotated[int, Field(ge=0, le=7)]]] = Field(
        description="Fault inputs of PWM timers the comparator output reaches without software; key: timer "
                    "instance (e.g. `FTM0`), value: numbers of its fault inputs.", json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _references(self):
        if self.supply_reference is None and self.bandgap_mv is None:
            raise ValueError(f"comparator {self.name}: no reference for the DAC (supply_reference or bandgap_mv)")
        band = (self.bandgap_min_mv, self.bandgap_max_mv)
        if self.bandgap_mv is None and any(v is not None for v in band):
            raise ValueError(f"comparator {self.name}: bandgap_min_mv/bandgap_max_mv without bandgap_mv")
        if self.bandgap_mv is not None:
            if None in band:
                raise ValueError(f"comparator {self.name}: bandgap_mv needs bandgap_min_mv and bandgap_max_mv")
            if not band[0] <= self.bandgap_mv <= band[1]:
                raise ValueError(f"comparator {self.name}: bandgap_min_mv <= bandgap_mv <= bandgap_max_mv")
        return self


# IO-62: hardware trigger chain PWM -> trigger router -> sample sequencer -> ADC (doc/architecture/fast-control-loop.md
# section 3). What the derivative states, family neutral: the trigger router (s32k1xx TRGMUX, s32k3xx TRGMUX/BCTU) with
# its input codes and outputs, which router input the trigger outputs of each PWM timer are, which outputs lead to
# the fault inputs of the timers (SYS-86 uses them), and the sample sequencers (s32k1xx PDB) that convert a list of
# channels on one ADC after one trigger. Register encodings beyond these numbers stay in the family generator; the
# checks with YAML path are in tools/nxconfig/samplegroup.py.
class RouterTarget(Strict):
    """One output of the trigger router: which register and which selector field of it selects its source."""

    name: Name = Field(description="Name of the output as in the reference manual (e.g. `PDB0_TRIGGER_IN0`).")
    register_index: Annotated[int, Field(ge=0, le=255)] = Field(
        description="Index of the router register that holds the selector of this output (s32k1xx: TRGMUXn).",
        json_schema_extra=NO_UNIT)
    selector: Annotated[int, Field(ge=0, le=7)] = Field(description="Selector field of the output inside its "
                                                                    "register (s32k1xx: SEL0..SEL3).",
                                                        json_schema_extra=NO_UNIT)


class TimerTrigger(Strict):
    """Router inputs driven by the trigger outputs of one PWM timer."""

    counter: Name = Field(description="Router input (`sources` key) of the trigger the timer raises at the counter "
                                      "reload/initialisation (s32k1xx: FTMx_INIT_TRIG).")
    match: Name = Field(description="Router input of the trigger the timer raises on a channel match (s32k1xx: "
                                    "FTMx_EXT_TRIG).")


class DerivativeTriggerRouter(Fact):
    """Trigger router between peripherals (IO-62): inputs, outputs, timer triggers and the lock of a register."""

    name: Name = Field(description="Router as in the reference manual (e.g. `TRGMUX`); used in messages.")
    sources: dict[Name, Annotated[int, Field(ge=0, le=255)]] = Field(
        description="Router inputs and the code that selects each of them in a selector field; key: name as in the "
                    "reference manual (e.g. `FTM0_INIT_TRIG`).", json_schema_extra=NO_UNIT)
    targets: list[RouterTarget] = Field(description="Router outputs the tools use (sample sequencer inputs, fault "
                                                    "inputs of PWM timers).")
    timer_triggers: dict[Name, TimerTrigger] = Field(
        {}, description="Trigger outputs of the PWM timers as router inputs; key: timer instance (e.g. `FTM0`). A "
                        "timer without an entry cannot start a sample group.")
    fault_inputs: dict[Name, list[Name]] = Field(
        {}, description="Router outputs that lead to the fault inputs of a PWM timer, in the order of the fault "
                        "inputs; key: timer instance. Lets the tools see a hardware threshold (SYS-86) as a user "
                        "of the router.", json_schema_extra=NO_UNIT)
    register_lock: bool = Field(False, description="`true`: a register of the router can be locked until reset, and "
                                                   "the lock freezes every selector field of it (s32k1xx TRGMUX "
                                                   "LK). A route that locks its register forbids every other route "
                                                   "through that register.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _references(self):
        names = [t.name for t in self.targets]
        if len(set(names)) != len(names):
            raise ValueError(f"trigger router {self.name}: target names are not unique")
        places = [(t.register_index, t.selector) for t in self.targets]
        if len(set(places)) != len(places):
            raise ValueError(f"trigger router {self.name}: two targets share one selector field")
        for timer, trig in self.timer_triggers.items():
            for role in ("counter", "match"):
                if getattr(trig, role) not in self.sources:
                    raise ValueError(f"trigger router {self.name}: timer_triggers.{timer}.{role} "
                                     f"{getattr(trig, role)} is no router input (sources)")
        for timer, outputs in self.fault_inputs.items():
            for out in outputs:
                if out not in names:
                    raise ValueError(f"trigger router {self.name}: fault_inputs.{timer} {out} is no target")
        return self


class DerivativeSampleSequencer(Fact):
    """Sample sequencer of one ADC (IO-62): converts a list of channels after one trigger (s32k1xx: PDB)."""

    name: Name = Field(description="Sequencer instance as in the reference manual (e.g. `PDB0`).")
    adc: Name = Field(description="ADC instance (`instances`, class adc) whose conversions the sequencer starts.")
    trigger_target: Name = Field(description="Router output (`trigger_router.targets[].name`) that starts the "
                                             "sequencer.")
    slots: PositiveInt = Field(description="Conversions one trigger can start on the ADC (s32k1xx: pre-triggers of "
                                           "PDB channel 0, each with its own ADC configuration and result "
                                           "register).", json_schema_extra=NO_UNIT)
    back_to_back: bool = Field(description="`true`: the end of one conversion starts the next one (s32k1xx: "
                                           "back-to-back mode), no delay has to be planned between them.")
    counter_bits: PositiveInt = Field(description="Width of the delay counter from the trigger to the first "
                                                  "conversion.", json_schema_extra=NO_UNIT)
    clock: Literal["core", "bus"] = Field(description="Clock domain the delay counter runs on (s32k1xx: SYS_CLK = "
                                                      "core clock).")
    start_clocks: Annotated[int, Field(ge=0, le=255)] = Field(
        description="Clocks of the sequencer between the trigger (or the end of the previous conversion) and the "
                    "start of a conversion, on top of the programmed delay.", json_schema_extra=NO_UNIT)
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)


class Derivative(Strict):
    """Derivative file `mcudb/<family>/<derivative>.yaml`: manufacturer facts of one MCU derivative."""

    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    derivative: DerivativeInfo = Field(description="Identification, core and packages of the derivative.")
    memory: list[MemoryRegion] = Field([], description="Memory regions (flash, RAM, EEPROM window).")
    instances: dict[str, Instance] = Field(description="Peripheral instances; key: instance name as in the "
                                                       "reference manual (e.g. `FlexCAN0`, `LPSPI1`), which boards "
                                                       "use in `instance`.")
    pinmux: dict[str, PinmuxRef] = Field({}, description="Pin multiplexing per package; key: package name from "
                                                         "`derivative.packages`.")
    device_header: DeviceHeader | None = Field(None, description="Vendor device header of the derivative.")
    clock: DerivativeClock | None = Field(None, description="Clock facts: oscillators, PLL and frequency limits.")
    mpu: DerivativeMpu | None = Field(None, description="Memory protection unit. Left out: no MPU described; code "
                                                        "that needs it stops with an error.")
    uid: DerivativeUid | None = Field(None, description="Unique chip identification. Left out: unknown; a project "
                                                        "with a bootloader needs it.")
    vim: DerivativeVim | None = Field(None, description="Vectored interrupt manager, VIM cores only. Left out: not "
                                                        "described, no interrupt ranks possible.")
    # NVM-07
    eeprom_emulation: DerivativeEepromEmulation | None = Field(
        None, description="Codes of the one-time partition of the emulated EEPROM (s32k1xx FlexNVM). Left out: the "
                          "derivative has no partitionable emulated EEPROM; a project with `nvm.partition` is "
                          "rejected.")
    # IO-51
    pin_interrupts: DerivativePinInterrupts | None = Field(
        None, description="Pins that can raise an interrupt on an edge, their lines and vectors. Left out: not "
                          "described; a project with `pin_events` is rejected.")
    # SYS-86
    comparators: list[DerivativeComparator] = Field(
        [], description="Analogue comparators with reference DAC whose output reaches a fault input of a PWM timer "
                        "(hardware threshold). Empty: not described; a project with `hw_thresholds` is rejected.")
    # IO-62
    trigger_router: DerivativeTriggerRouter | None = Field(
        None, description="Trigger router between peripherals (PWM timer triggers to sample sequencers and fault "
                          "inputs). Left out: not described; a project with `sample_groups` is rejected.")
    sample_sequencers: list[DerivativeSampleSequencer] = Field(
        [], description="Sample sequencers that convert a list of channels on one ADC after one hardware trigger. "
                        "Empty: not described; a project with `sample_groups` is rejected.")


class PinmuxTable(Strict):
    """Generated pin multiplexing file of one part (mcudb/<family>/<part>.pinmux.yaml)."""

    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    part: Name = Field(description="Part (derivative and package) the table describes.")
    selector: str = Field(description="Meaning of the numbers in the table, e.g. `PORT_PCR.MUX`.")
    source: str = Field(description="Source file the table was generated from, relative to `mcudb/`.")
    signals: SignalTable = Field(description="Signal -> pin -> function selector. Signal names are "
                                             "`<instance>_<function>` (e.g. `FlexCAN0_RX`, `FTM0_CH3`) or `GPIO`; "
                                             "the selector is a register value (s32k1xx PORT_PCR.MUX, stm32 "
                                             "GPIOx_AFR) or, where one number is not enough, named register "
                                             "values.",
                                 json_schema_extra=NO_UNIT)


# ----------------------------------------------------------------------------------------------- board


class Pin(Strict):
    """A pin of the derivative a board signal is wired to."""

    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)


class SpiPcs(Pin):
    """Chip select pin of an SPI bus."""

    index: Annotated[int, Field(ge=0)] = Field(description="Number of the peripheral chip select signal (PCS<n> of "
                                                           "the instance) the pin carries.",
                                               json_schema_extra=NO_UNIT)


class FlexioPin(Pin):
    """Pin of a bus that may run on FlexIO."""

    # TOOL-120: optional in the schema, because only a FlexIO instance has data pins. Whether it is needed depends
    # on the class of the instance in the derivative, which the schema cannot see - checks._board asks for it there.
    flexio_pin: Annotated[int, Field(ge=0)] | None = Field(None, description="FlexIO data pin number D<n> the pin "
                                                                             "is connected to (pin function "
                                                                             "`<instance>_D<n>`). Required when the "
                                                                             "bus instance is a FlexIO; left out "
                                                                             "on an I2C instance, which has no "
                                                                             "data pins.",
                                                           json_schema_extra=NO_UNIT)


class BoardClock(Strict):
    """Clock sources fitted on the board."""

    sosc_hz: PositiveInt | None = Field(None, description="Frequency of the fitted crystal or external oscillator; "
                                                          "the clock model derives PLL and dividers from it. Left "
                                                          "out: none fitted.")


class Debug(Strict):
    """Debug connection of the board: interface, probe and clock of the flash targets."""

    interface: str = Field(description="Debug interface of the connector: `JTAG`, `SWD` or `cJTAG`.")
    probe: str | None = Field(None, description="Debug probe (e.g. `J-Link EDU Mini`, `ST-LINK/V2-1`, `CMSIS-DAP`, "
                                                "`XDS110`); selects the flash tools. Left out: J-Link.")
    speed_khz: PositiveInt | None = Field(None, description="Clock of the debug interface. Left out: the safe "
                                                            "default of the flash targets (1000 kHz).")


class BoardInfo(Strict):
    """Identification of the board: derivative, package, supply, clock and debug connection."""

    name: Name = Field(description="Board name; by convention the same as the folder `boards/<board>`.")
    mcu: Name = Field(description="Derivative fitted on the board (`derivative.name` of a file in `mcudb/`).")
    package: str = Field(description="Package of the fitted derivative, one of its `derivative.packages`; selects "
                                     "the pin multiplexing table.")
    supply_v: float | None = Field(None, description="Supply voltage of the MCU. The STM32 generator chooses the "
                                                     "flash wait states for it. Left out: not stated.")
    clock: BoardClock = Field(BoardClock(), description="Fitted crystal or oscillator.")
    debug: Debug | None = Field(None, description="Debug connection. Left out: the build has no flash targets.")
    adc_reference_mv: PositiveInt | None = Field(None, description="Voltage at the ADC reference input (VREFH): "
                                                                   "scaling base of all ADC channels.")
    description: str | None = Field(None, description="Short description of the board for the reader.")


class DevicePin(Strict):
    """IO-33: how the board wires one pin of a device (board.yaml devices[].pins.<pin>)."""

    use: Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")] = Field(
        description="What the pin does on this board, one of the uses the module lists for it (module.yaml "
                    "`pins.<pin>.uses`, e.g. `none` for a pin that is not wired, `wake_input` for a wake-up input).")
    note: str | None = Field(None, description=NOTE)
    verify: bool = Field(False, description="`true`: the wiring is not confirmed by the source.")


class Device(Strict):
    """Fitted component and, if a module drives it, its wiring to the module's connections (module.yaml connect)."""

    name: Name = Field(description="Device name, unique on the board; buses (`spi[].device`, `can[].phy`) and the "
                                   "project (`modules.<device>`) refer to it.")
    part: str = Field(description="Part number of the fitted component (e.g. `TMC6200-TA`); matched against the "
                                  "`parts` patterns of the module manifests.")
    module: Name | None = Field(None, description="Module (driver) that drives the device. Left out: the module "
                                                  "whose manifest lists the part.")
    connect: dict[Name, Name] = Field({}, description="Wiring of the module's connections: module connection -> "
                                                      "board entry (digital output or input, ADC channel).")
    note: str | None = Field(None, description=NOTE)
    source: str | None = Field(None, description="Document the part facts come from (data sheet).")
    verify: bool = Field(False, description="`true`: fitting or wiring is not confirmed by the source.")
    pins: dict[Name, DevicePin] = Field(
        {}, description="IO-33: wiring of pins of the part itself that are no MCU connection (e.g. the HVIO and EN "
                        "pins of a system basis chip): pin -> use. The module manifest lists the pins and the uses "
                        "it serves (module.yaml `pins`). Left out: not described, the module leaves such pins as "
                        "it found them.")


class SpiBus(Strict):
    """SPI bus of the board: instance, pins and the device on the bus."""

    name: Name = Field(description=NAME)
    instance: str = Field(description="SPI instance of the derivative (e.g. `LPSPI1`).")
    sck: Pin = Field(description="Clock pin.")
    sin: Pin = Field(description="Data input of the MCU (MISO).")
    sout: Pin = Field(description="Data output of the MCU (MOSI).")
    pcs: SpiPcs = Field(description="Chip select pin.")
    device: Name | None = Field(None, description="Board device on the bus (`devices[].name`). Left out: none "
                                                  "described.")


class CanBus(Strict):
    """CAN connection of the board: controller instance, pins and transceiver."""

    name: Name = Field(description="Bus name, free (e.g. `VEH`, `DIAG`, `CAN0`); the project refers to it.")
    instance: str = Field(description="CAN controller instance of the derivative (e.g. `FlexCAN0`).")
    rx: Pin = Field(description="Receive pin.")
    tx: Pin = Field(description="Transmit pin.")
    phy: Name | None = Field(None, description="Board device acting as transceiver (`devices[].name`). Left out: "
                                               "none described.")


class Uart(Strict):
    """UART of the board: instance and pins."""

    name: Name = Field(description=NAME)
    instance: str = Field(description="UART instance of the derivative (e.g. `LPUART2`).")
    rx: Pin = Field(description="Receive pin.")
    tx: Pin = Field(description="Transmit pin.")
    note: str | None = Field(None, description=NOTE)


class I2cBus(Strict):
    """I2C bus of the board, on an I2C instance or a FlexIO."""

    name: Name = Field(description=NAME)
    instance: str = Field(description="I2C or FlexIO instance of the derivative that runs the bus.")
    sda: FlexioPin = Field(description="Data pin; `flexio_pin` only on a FlexIO instance.")
    scl: FlexioPin = Field(description="Clock pin; `flexio_pin` only on a FlexIO instance.")


class Divider(Strict):
    """Resistor divider in front of an ADC pin; only the ratio of the two resistors counts."""

    r_top: Annotated[float, Field(ge=0)] = Field(description="Resistor between the measured voltage and the pin.",
                                                 json_schema_extra={"unit": "Ω"})
    r_bottom: Annotated[float, Field(gt=0)] = Field(description="Resistor between the pin and ground.",
                                                    json_schema_extra={"unit": "Ω"})


class AdcScale(Strict):
    """Value in mV = pin voltage * factor + offset_mv; divider: factor = (r_top + r_bottom) / r_bottom."""

    factor: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = Field(
        None, description="Ratio of the measured voltage to the pin voltage. Give either `factor` or `divider`; "
                          "neither: 1.", json_schema_extra={"unit": "V/V"})
    divider: Divider | None = Field(None, description="Resistor divider the factor is calculated from.")
    offset_mv: Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)] = Field(
        0, description="Offset added after the factor.")
    verify: bool = Field(False, description=VERIFY)

    @model_validator(mode="after")
    def _factor_or_divider(self):
        if self.factor is not None and self.divider is not None:
            raise ValueError("scale must not have both factor and divider")
        return self


# IO-18 (decisions of the user 2026-09-16): the board describes the circuit in front of an ADC pin and delivers the
# quantity at the connector (sensor), the project the load connected there (adc_loads, e.g. NTC -> temperature).
# Every fact is an integer in a fixed unit; the generated conversion is integer only (S32K116 has no FPU), rounding
# and int32/int64 ranges are checked by tools/nxconfig/sensor.py. Formulas: doc/architecture/README.md section 4.
Mv = Annotated[int, Field(ge=0, le=(1 << 31) - 1)]
SensorUnit = Literal["mV", "mA", "ohm", "ddegC", "permille"]


class SensorFacts(Strict):
    source: str | None = Field(None, description="Data sheet or schematic the values come from.")
    verify: bool = Field(False, description="`true`: a value is not confirmed by the source.")
    note: str | None = Field(None, description=NOTE)


class RailVoltage(Strict):
    """A voltage of the board a sensor circuit refers to: a named rail (board.rails) or a fixed value."""

    rail: Name | None = Field(None, description="Supply rail of the board (`rails[].name`). Give either `rail` or "
                                                "`mv`.")
    mv: Mv | None = Field(None, description="Fixed voltage, instead of a rail.")
    ratio_permille: Annotated[int, Field(ge=0, le=1000)] = Field(
        1000, description="Share of the rail that applies (e.g. 500 for an amplifier reference halfway between GND "
                          "and the rail). Only with `rail`.")

    @model_validator(mode="after")
    def _rail_or_mv(self):
        if (self.rail is None) == (self.mv is None):
            raise ValueError("give either rail or mv")
        if self.mv is not None and self.ratio_permille != 1000:
            raise ValueError("ratio_permille only applies to a rail")
        return self


class Rail(Strict):
    """Supply rail of the board (e.g. +5V of the SBC) that pull-ups and amplifier references use."""

    name: Name = Field(description="Rail name (e.g. `SUPPLY_5V`); sensor circuits refer to it in `rail`.")
    mv: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Voltage of the rail, measured or from the "
                                                                          "schematic.")
    source: str | None = Field(None, description=SOURCE)
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)


LINEAR_UNIT = {"unit": "unit of `unit`"}


class LinearSensor(SensorFacts):
    """value = pin_mV * gain_num / gain_den + offset (unit)."""

    kind: Literal["linear"] = Field(description="Circuit type: linear characteristic of the pin voltage.")
    unit: SensorUnit = Field(description="Unit of the value the circuit delivers (`adc.phys` of the channel).")
    gain_num: Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)] = Field(
        description="Numerator of the gain; not 0.", json_schema_extra={"unit": "unit of `unit` per mV"})
    gain_den: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(
        1, description="Denominator of the gain, for gains that are no integer.", json_schema_extra=NO_UNIT)
    offset: Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)] = Field(
        0, description="Offset added after the gain.", json_schema_extra=LINEAR_UNIT)

    @model_validator(mode="after")
    def _gain(self):
        if self.gain_num == 0:
            raise ValueError("gain_num must not be 0")
        return self


class ShuntAmplifierSensor(SensorFacts):
    """Current in mA = (pin_mV - reference_mV) * gain_den / gain / shunt (shunt amplifier with output offset)."""

    kind: Literal["shunt_amplifier"] = Field(description="Circuit type: shunt resistor with current sense "
                                                         "amplifier; delivers the current in mA.")
    shunt_uohm: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Resistance of the shunt.")
    gain: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(
        description="Voltage gain of the amplifier; for a gain that is no integer, gain / gain_den.",
        json_schema_extra={"unit": "V/V"})
    gain_den: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(
        1, description="Denominator of the gain, for gains that are no integer.", json_schema_extra=NO_UNIT)
    reference: RailVoltage = Field(description="Output voltage of the amplifier at 0 A (its REF pins).")
    output_min_mv: Mv | None = Field(None, description="Lower end of the linear output range (swing to GND); below "
                                                       "it the channel reports a range error. Left out: no limit.")
    output_max_mv: Mv | None = Field(None, description="Upper end of the linear output range (swing to the supply); "
                                                       "above it the channel reports a range error. Left out: no "
                                                       "limit.")


class DividerSensor(SensorFacts):
    """Voltage in mV at the connector = pin_mV * (r_top + r_bottom) / r_bottom."""

    kind: Literal["divider"] = Field(description="Circuit type: resistor divider; delivers the voltage at the "
                                                 "connector in mV.")
    r_top_ohm: Annotated[int, Field(ge=0, le=(1 << 31) - 1)] = Field(description="Resistor between the connector "
                                                                                 "and the pin.")
    r_bottom_ohm: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Resistor between the pin and "
                                                                                    "ground.")


class PullupSensor(SensorFacts):
    """Resistance in ohm to GND = pullup * U / (supply - diode - U), U = pin voltage."""

    kind: Literal["pullup"] = Field(description="Circuit type: pull-up resistor to a supply; delivers the "
                                                "resistance to GND at the connector in ohm.")
    pullup_ohm: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Pull-up resistor.")
    supply: RailVoltage = Field(description="Voltage the pull-up is connected to.")
    diode_mv: Mv = Field(0, description="Forward voltage of a diode in series to the pull-up; 0: none.")


class HighSideIsSensor(SensorFacts):
    """Load current in mA of a smart high-side switch = k_ILIS * U_IS / R_IS (U_IS in mV, R_IS in ohm)."""

    kind: Literal["highside_is"] = Field(description="Circuit type: current sense output (IS) of a smart high-side "
                                                     "switch; delivers the load current in mA.")
    device: Name = Field(description="Board device of the switch (`devices[].name`); its `connect.is` is this "
                                     "channel.")
    k_ilis: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(
        description="Current sense ratio I_L / I_IS of the switch (data sheet k_ILIS).",
        json_schema_extra={"unit": "A/A"})
    r_is_ohm: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Sense resistor at the IS pin.")


AdcSensor = Annotated[LinearSensor | ShuntAmplifierSensor | DividerSensor | PullupSensor | HighSideIsSensor,
                      Field(discriminator="kind")]


class AdcChannel(Strict):
    """Analog input of the board: converter channel, pin and the circuit in front of the pin."""

    name: Name = Field(description="Channel name (e.g. the net name); the project and the RTE signals "
                                   "`adc.*.<NAME>` refer to it.")
    instance: str = Field(description="ADC instance of the derivative (e.g. `ADC0`).")
    channel: str = Field(description="External channel of the instance (e.g. `SE4`), matching its "
                                     "`channel_pattern`.")
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)
    scale: AdcScale = Field(AdcScale(), description="Conversion of the pin voltage to the value in mV (`adc.mv`).")
    # IO-18: circuit in front of the pin -> adc.phys.<NAME>
    sensor: AdcSensor | None = Field(None, description="Circuit in front of the pin that turns the pin voltage into "
                                                       "the quantity at the connector (`adc.phys.<NAME>`). Left "
                                                       "out: no physical value.")
    fitted: bool = Field(True, description="`false`: the circuit is not fitted, the pin reads nothing useful.")
    note: str | None = Field(None, description=NOTE)


class PwmChannel(Strict):
    """One timer channel of a PWM group and its pin."""

    name: Name = Field(description="Channel name, unique in the group; `pairs` refer to it.")
    channel: Annotated[int, Field(ge=0)] = Field(description="Channel number of the timer instance.",
                                                 json_schema_extra=NO_UNIT)
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)


class PwmPair(Strict):
    """Half bridge of a PWM group: its high-side and low-side channel."""

    name: Name = Field(description="Pair name (e.g. `U`); the RTE target `pwm.<PAIR>` refers to it.")
    high_side: Name = Field(description="Channel (`channels[].name`) that drives the high-side switch.")
    low_side: Name = Field(description="Channel (`channels[].name`) that drives the low-side switch.")


class PwmActive(Strict):
    """Pin level that switches the transistor on, per side."""

    high_side: Level = Field(description="Pin level that switches a high-side switch on.")
    low_side: Level = Field(description="Pin level that switches a low-side switch on.")


# IO-24: facts of the power stage a PWM group drives (decision of the user 2026-09-16: generic instead of one
# transistor). Fixed units in int32 (ns, nC, mOhm, uOhm, mV), no floats. Every group of facts carries its source; without
# `source` or with `verify: true` nothing of it counts as confirmed. Missing facts are warnings (powerstage.py), the
# derived minimum dead time is checked against pwm.<group>.deadtime_ns of the project.
Fixed32 = Annotated[int, Field(ge=0, le=(1 << 31) - 1)]


class StageFacts(Strict):
    part: str | None = Field(None, description="Part number of the component, for the reader; no tool evaluates "
                                               "it.")
    source: str | None = Field(None, description="Data sheet, table or measurement the facts come from.")
    verify: bool = Field(False, description="`true`: the facts are not confirmed by the source; a dead time below "
                                            "the derived minimum is then a warning instead of an error.")


class SwitchFacts(StageFacts):
    """Switching transistor of every half bridge of the group (high and low side of the same type).

    Times as the data sheet gives them for the switching test circuit, the maximum where one is given, otherwise the
    typical value; the spread belongs into the project's dead time margin.
    """

    turn_on_delay_ns: Fixed32 | None = Field(None, description="Turn-on delay time t_d(on).")
    rise_time_ns: Fixed32 | None = Field(None, description="Rise time t_r.")
    turn_off_delay_ns: Fixed32 | None = Field(None, description="Turn-off delay time t_d(off).")
    fall_time_ns: Fixed32 | None = Field(None, description="Fall time t_f.")
    test_gate_resistance_mohm: Fixed32 | None = Field(None, description="External gate resistance R_G of the "
                                                                        "switching test the times were measured "
                                                                        "with.")
    internal_gate_resistance_mohm: Fixed32 | None = Field(
        None, description="Internal gate resistance R_G(int). Left out: counted as 0 (conservative).")
    gate_charge_nc: Fixed32 | None = Field(None, description="Total gate charge Q_G at the gate drive voltage of the "
                                                             "board.")
    # IO-35: whole milliohms are too coarse for modern switches (5.1 mOhm), the on-resistance is given in micro-ohms.
    # The milliohm fields stay for existing boards and are taken as 1000 micro-ohms each; one of both, not both.
    r_ds_on_uohm: Fixed32 | None = Field(None, description="Maximum on-resistance at 25 °C and the gate drive "
                                                           "voltage of the data sheet table.")
    r_ds_on_hot_uohm: Fixed32 | None = Field(None, description="Maximum on-resistance at the highest junction "
                                                               "temperature. Left out: not known, only the current "
                                                               "at 25 °C is derived.")
    r_ds_on_mohm: Fixed32 | None = Field(None, description="Older form of `r_ds_on_uohm` in whole milliohms (taken "
                                                           "as 1000 µΩ each); not together with `r_ds_on_uohm`.")
    r_ds_on_hot_mohm: Fixed32 | None = Field(None, description="Older form of `r_ds_on_hot_uohm` in whole milliohms "
                                                               "(taken as 1000 µΩ each); not together with "
                                                               "`r_ds_on_hot_uohm`.")

    @model_validator(mode="after")
    def _r_ds_on_once(self):
        for new, old in (("r_ds_on_uohm", "r_ds_on_mohm"), ("r_ds_on_hot_uohm", "r_ds_on_hot_mohm")):
            if getattr(self, new) is not None and getattr(self, old) is not None:
                raise ValueError(f"{new} and {old} both given: state the on-resistance once, in {new}")
            if getattr(self, old) is not None and getattr(self, old) * 1000 > (1 << 31) - 1:
                raise ValueError(f"{old} {getattr(self, old)} does not fit in {new} (int32 micro-ohms)")
        return self

    @property
    def r_ds_on(self) -> int | None:
        """On-resistance at 25 °C in micro-ohms, from either field; None: not known."""
        return self.r_ds_on_uohm if self.r_ds_on_mohm is None else self.r_ds_on_mohm * 1000

    @property
    def r_ds_on_hot(self) -> int | None:
        """On-resistance at the highest junction temperature in micro-ohms; None: not known."""
        return self.r_ds_on_hot_uohm if self.r_ds_on_hot_mohm is None else self.r_ds_on_hot_mohm * 1000


class GateDriverFacts(StageFacts):
    """Gate driver between the timer pins and the gates: propagation delay of each switching direction."""

    turn_on_delay_ns: Fixed32 | None = Field(None, description="Delay from the input edge until the gate output "
                                                               "starts to turn the switch on.")
    turn_off_delay_ns: Fixed32 | None = Field(None, description="Delay from the input edge until the gate output "
                                                                "starts to turn the switch off.")


class GateCircuitFacts(StageFacts):
    """External gate resistance of the board in each direction (turn-off path e.g. with a bypass diode)."""

    turn_on_resistance_mohm: Fixed32 | None = Field(None, description="External gate resistance in the turn-on "
                                                                      "path. Left out: unknown.")
    turn_off_resistance_mohm: Fixed32 | None = Field(None, description="External gate resistance in the turn-off "
                                                                       "path (e.g. with a bypass diode). Left out: "
                                                                       "unknown.")


class ShortCircuitFacts(StageFacts):
    """Drain-source voltage monitoring of the switches (desaturation / V_DS short-circuit detection) that is fixed by
    the circuit of the board. A detection whose threshold is a setting of the gate driver is a decision of the project
    instead: a parameter of the driver module (`power_stage` in its manifest), not stated here.
    """

    vds_threshold_mv: Fixed32 | None = Field(None, description="Detection threshold of the voltage across the "
                                                               "conducting switch.")
    blanking_ns: Fixed32 | None = Field(None, description="Time after turn-on during which the detection is "
                                                          "suppressed.")


class PowerStage(Strict):
    """Facts of the power stage a PWM group drives; the minimum dead time is derived from them."""

    switch: SwitchFacts | None = Field(None, description="Switching transistors of the half bridges.")
    driver: GateDriverFacts | None = Field(None, description="Gate driver between timer pins and gates.")
    gate: GateCircuitFacts | None = Field(None, description="External gate resistors of the board.")
    short_circuit: ShortCircuitFacts | None = Field(None, description="Short-circuit (desaturation) detection of "
                                                                      "the switches.")
    note: str | None = Field(None, description=NOTE)


class PwmGroup(Strict):
    """Wiring of a PWM group; frequency and dead time are chosen by the project."""

    name: Name = Field(description="Group name; the project sets frequency and dead time under `pwm.<name>`.")
    instance: str = Field(description="Timer instance of the derivative that generates the group (e.g. `FTM0`).")
    mode: Literal["complementary"] = Field(description="Operating mode: `complementary` drives each pair as a half "
                                                       "bridge, the low side inverted to the high side with dead "
                                                       "time.")
    active: PwmActive = Field(description="Pin levels that switch the transistors on.")
    verify: bool = Field(False, description=VERIFY)
    pairs: list[PwmPair] = Field(min_length=1, description="Half bridges of the group.")
    channels: list[PwmChannel] = Field(min_length=1, description="Timer channels of the group and their pins.")
    power_stage: PowerStage | None = Field(None, description="Facts of the power stage. Left out: nothing known "
                                                             "about the stage, the dead time is not checked "
                                                             "(warning when the group is used).")
    # IO-25: board device whose gate driver inputs the channels of this group feed.
    gate_driver: Name | None = Field(
        None, description="Board device (`devices[].name`) whose gate driver inputs the channels of this group feed. "
                          "Its module must provide the role `gate_driver`; the driver's input mode is derived from "
                          "`mode`. Left out: the group drives no described gate driver.")
    # SYS-116 (doc/safety/partner-and-degraded.md part C, decisions C1-C6 of 2026-09-29): board facts of the bridge
    # that decide whether an active short can be offered and what the bridge does while the MCU is in reset.
    bridge_reset_state: Literal["freewheel", "active_short_low", "active_short_high"] | None = Field(
        None, description="What the bridge does while the MCU is in reset and its pins follow the board (pull "
                          "resistors of the gate driver inputs and of its enable): `freewheel` - every switch off; "
                          "`active_short_low`/`active_short_high` - the board designer pulled every low-side resp. "
                          "high-side gate on. Left out: not stated; a project that allows an active short then gets a "
                          "warning, like for `freewheel`.")
    high_side_hold: bool = Field(False, description="`true`: the high-side switches can stay on without switching "
                                                    "(isolated or charge-pumped gate supply). `false`: a "
                                                    "bootstrap-fed high side discharges and drifts into a partial "
                                                    "short; `active_short_high` is then refused.")


class InputCapture(Strict):
    """Timer channel of the board used to capture edges of an input signal."""

    name: Name = Field(description=NAME)
    instance: str = Field(description="Timer instance of the derivative (e.g. `FTM1`).")
    channel: Annotated[int, Field(ge=0)] = Field(description="Channel number of the timer instance.",
                                                 json_schema_extra=NO_UNIT)
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)
    note: str | None = Field(None, description=NOTE)


class DigitalInput(Strict):
    """Digital input of the board."""

    name: Name = Field(description="Input name (e.g. the net name); the RTE signal `dio.in.<NAME>` refers to it.")
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)
    active: Level = Field("high", description="Pin level at which the input counts as active (true).")
    pull: Literal["up", "down"] | None = Field(None, description="Pull resistor the MCU must switch on. Left out: "
                                                                 "none (the board provides the level).")
    verify: bool = Field(False, description=VERIFY)


class DigitalOutput(Strict):
    """Digital output of the board."""

    name: Name = Field(description="Output name (e.g. the net name); the RTE target `dio.out.<NAME>` and the "
                                   "project refer to it.")
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)
    active: Level = Field("high", description="Pin level of the active (switched on) state. Outputs start "
                                              "inactive.")
    verify: bool = Field(False, description=VERIFY)


class Dio(Strict):
    """Digital inputs and outputs of the board."""

    inputs: list[DigitalInput] = Field([], description="Digital inputs.")
    outputs: list[DigitalOutput] = Field([], description="Digital outputs.")


# SYS-86: which signal reaches an input of an analogue comparator of the MCU. The circuit in front of the pin is
# described once, by the board ADC entry named in `signal` (its scale or sensor): a threshold of the project is in
# the unit of that signal and nxconfig derives the pin voltage from it.
class ComparatorInput(Strict):
    """Pin of the board wired to an input of an analogue comparator of the MCU (hardware threshold, SYS-86)."""

    name: Name = Field(description=NAME)
    instance: Name = Field(description="Comparator of the derivative (`comparators[].name`, e.g. `CMP0`).")
    input: Annotated[int, Field(ge=0)] = Field(description="Input of the comparator's multiplexer the pin is "
                                                           "(IN<n>, pin multiplexing signal `<instance>_IN<n>`).",
                                               json_schema_extra=NO_UNIT)
    pin: str = Field(description=PIN)
    pad: PositiveInt | None = Field(None, description=PAD, json_schema_extra=NO_UNIT)
    signal: Name = Field(description="Board ADC entry (`adc[].name`) whose circuit (scale or sensor) is in front of "
                                     "this pin - the same pin, or another pin on the same node. Its characteristic "
                                     "must be linear; the project's threshold is in its unit.")
    verify: bool = Field(False, description=VERIFY)
    note: str | None = Field(None, description=NOTE)


class BoardComparators(Strict):
    """Signals at comparator inputs and the supply the comparator's DAC can refer to (SYS-86)."""

    supply: RailVoltage | None = Field(None, description="Voltage at the supply pin the DAC can use as reference "
                                                         "(derivative `comparators[].supply_reference`, s32k1xx "
                                                         "VDDA). Left out: only the bandgap reference is possible.")
    inputs: list[ComparatorInput] = Field([], description="Pins wired to comparator inputs.")


class Board(Strict):
    """Board file `boards/<board>/board.yaml`: what is fitted and wired on one printed circuit board."""

    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    board: BoardInfo = Field(description="Identification of the board: derivative, package, clock, debug.")
    devices: list[Device] = Field([], description="Fitted components the software talks to.")
    spi: list[SpiBus] = Field([], description="SPI buses.")
    can: list[CanBus] = Field([], description="CAN connections.")
    uart: list[Uart] = Field([], description="UARTs.")
    i2c: list[I2cBus] = Field([], description="I2C buses.")
    rails: list[Rail] = Field([], description="Supply rails that sensor circuits refer to.")
    adc: list[AdcChannel] = Field([], description="Analog inputs with the circuit in front of the pin.")
    pwm: list[PwmGroup] = Field([], description="PWM groups with their power stage.")
    input_capture: list[InputCapture] = Field([], description="Input capture channels.")
    dio: Dio = Field(Dio(), description="Digital inputs and outputs.")
    # SYS-86
    comparators: BoardComparators | None = Field(None, description="Signals at inputs of an analogue comparator of "
                                                                   "the MCU (hardware threshold). Left out: none.")


# ----------------------------------------------------------------------------------------------- project

Selection = Literal["all"] | list[Name]


# PLAT-107 (safety review 2026-10-01, PLAT-26/S3): the highest integrity the ECU software of a project carries; a
# check that is stricter for ASIL projects reads it (tools/nxconfig/bswtasks.py asil_project)
ProjectIntegrity = Literal["qm", "asil_a", "asil_b", "asil_c", "asil_d"]


class ProjectInfo(Strict):
    """Identification of the project and the files it is built from."""

    name: Name = Field(description="Project name; by convention the same as the folder `projects/<project>`.")
    board: str = Field(description="Board folder or its `board.yaml`, relative to `project.yaml`.")
    # TOOL-114 (decision of the user 2026-09-25): the RTE is required. Optional in the schema only because the
    # configuration-only test profiles have none; tools/nxconfig/checks.py rte_required says where it is enforced.
    rte: str | None = Field(None, description="ECU description (`rte.yaml`) for tools/rtegen, relative to "
                                              "`project.yaml`. Required: the build runs rtegen at every configure, "
                                              "and nxconfig refuses a project that is built without it (TOOL-114). "
                                              "An ECU without software components still has one: one task, "
                                              "`components: []`.")
    # ARCH-51 (decision of the user 2026-09-21): a file of its own, named here like the ECU description
    diag: str | None = Field(None, description="Diagnostic description (`diag.yaml`): data identifiers, "
                                               "routines, sessions, security levels and DTCs of this ECU "
                                               "software, relative to `project.yaml`. Left out: the project "
                                               "describes no diagnostic jobs and no fault memory.")
    description: str | None = Field(None, description="Short description of the project for the reader.")
    # PLAT-107
    integrity: ProjectIntegrity | None = Field(
        None, description="Highest safety integrity the ECU software of this project carries (ASIL of its safety "
                          "requirements). `qm`: no safety requirement; the checks that are stricter for ASIL "
                          "projects then only warn (e.g. a BSW task stack below its default without a proof note, "
                          "bsw_tasks.<task>.evidence). Not `qm` while an NvM block has an ASIL integrity. Left out: "
                          "treated as an ASIL project - the safety part of neXtBSW (safety task, safe supervisor) is "
                          "in every image.")


class ProjectClock(Strict):
    """Clock the project runs at; sources, dividers and PLL settings are derived by the family clock model."""

    system_hz: PositiveInt = Field(description="Core clock. Sources, dividers and PLL settings follow from it and "
                                               "from the board crystal.")


# ARCH-18
class PeripheralClockUse(ClockChoice):
    """The functional clock the project chooses for one peripheral instance.

    `select` must be one of the sources the derivative allows (instances.<x>.clock.sources), `divider` one of its
    asynchronous outputs - it may be left out when the derivative allows only one. Without an entry the default
    of the derivative applies, so a project that decides nothing keeps the values it had.
    """

    verify: bool = Field(False, description="`true`: the choice is not confirmed (e.g. not measured on a board).")
    note: str | None = Field(None, description=NOTE)


# COM-37: behaviour of a CAN bus after bus off (libs/bsw/canSystem, can/CanBusOffRecovery.h, COM-35), per bus. The
# defaults are CanBusOffRecovery::DEFAULT_CONFIG (tools/bswgen test_bswgen.py compares both); limits are the C types
# of CanBusOffRecovery::Config (uint16_t, discard_after uint8_t). Checks across the values: checks._can_bus_off.
class CanBusOff(Strict):
    """What the bus does when a bus off keeps coming back: log series, doubling pause, dropping pending frames."""

    first_pause_ms: Annotated[int, Field(ge=0, le=65535)] = Field(
        10, description="Pause before the first release of a series of bus offs: the controller stays in bus off "
                        "this long before it is let back onto the bus. Default: one supervision cycle (10 ms).")
    max_pause_ms: Annotated[int, Field(ge=0, le=65535)] = Field(
        1000, description="Upper limit of the pause, which doubles with every bus off of a series. `0`: the "
                          "controller is not held and leaves bus off by itself (automatic recovery, as before "
                          "COM-35); logging and dropping stay.")
    discard_after: Annotated[int, Field(ge=0, le=255)] = Field(
        3, description="From this bus off of a series on, the frames waiting in the transmit buffers are dropped "
                       "before the release (a frame that already cost this many bus offs will cost the next one "
                       "as well). `0`: never.", json_schema_extra=NO_UNIT)
    quiet_ms: Annotated[int, Field(ge=1, le=65535)] = Field(
        2000, description="A series ends after this long without bus off; its end is logged in one line. Should be "
                          "longer than `max_pause_ms`, so a lasting fault stays one series.")
    summary_ms: Annotated[int, Field(ge=1, le=65535)] = Field(
        10000, description="While a series lasts, its further bus offs are counted and summarised in at most one "
                           "line per this period.")
    note: str | None = Field(None, description=NOTE)


class CanUse(Strict):
    """Bit timing request of a used board CAN bus; prescaler and segments are calculated (clock.py)."""

    bitrate: PositiveInt = Field(description="Nominal bit rate of the bus (e.g. 500000).",
                                 json_schema_extra={"unit": "bit/s"})
    sample_point_percent: Annotated[float, Field(ge=50, le=95, allow_inf_nan=False)] = Field(
        87.5, description="Wanted sample point as a share of the bit time. The calculated bit timing may deviate "
                          "slightly; nxconfig warns or reports an error when it deviates too far.")
    sjw_tq: PositiveInt | None = Field(None, description="Synchronization jump width. Left out: as large as the "
                                                         "segments allow.")
    prop_seg_tq: PositiveInt | None = Field(None, description="Propagation segment. Left out: chosen so that phase "
                                                              "segment 1 equals phase segment 2 where possible.")
    verify: bool = Field(False, description="`true`: the bit timing is not confirmed (e.g. not measured on the "
                                            "bus).")
    bus_off: CanBusOff | None = Field(None, description="Behaviour of the bus after bus off (pause, dropping "
                                                        "pending frames, logging). Left out: the defaults of "
                                                        "every value.")


class UartUse(Strict):
    """Settings of a used board UART."""

    baudrate: PositiveInt = Field(description="Baud rate (e.g. 115200); the divider of the UART is calculated from "
                                              "its functional clock.",
                                  json_schema_extra={"unit": "bit/s"})


# IO-28: settings of a used board I2C bus. The rate is a use of the project (the slowest device on the bus decides
# it); pins and the FlexIO data pins stay in the board, shifters and timers are derived by tools/nxconfig/i2c.py.
class I2cUse(Strict):
    """Settings of a used board I2C bus."""

    baudrate_hz: PositiveInt = Field(description="Upper limit of the SCL frequency, e.g. 100000 (standard mode) or "
                                                 "400000 (fast mode); the next slower rate the bus driver can make "
                                                 "is used, never a faster one.",
                                     json_schema_extra={"unit": "Hz"})
    timeout_ms: Annotated[int, Field(ge=1, le=10000)] = Field(
        20, description="Time limit of one transfer, clock stretching of the device included; at least the "
                        "longest transfer of the bus driver at the chosen rate.", json_schema_extra={"unit": "ms"})
    verify: bool = Field(False, description="`true`: the rate is not confirmed (no device datasheet, not measured "
                                            "on the bus).")


SELECTION = "`all`, or a list of names from the board section of the same name. Left out: none."


class Use(Strict):
    """Board functions used by the project: 'all' or a list of board names. Omitted: none."""

    spi: Selection = Field([], description="Used SPI buses (`spi[].name` of the board): " + SELECTION)
    can: Selection = Field([], description="Used CAN buses (`can[].name` of the board): " + SELECTION)
    uart: Selection = Field([], description="Used UARTs (`uart[].name` of the board): " + SELECTION)
    i2c: Selection = Field([], description="Used I2C buses (`i2c[].name` of the board): " + SELECTION)
    adc: Selection = Field([], description="Used ADC channels (`adc[].name` of the board): " + SELECTION)
    input_capture: Selection = Field([], description="Used input capture channels (`input_capture[].name` of the "
                                                     "board): " + SELECTION)
    dio: Selection = Field([], description="Used digital inputs and outputs (`dio.inputs[].name`, "
                                           "`dio.outputs[].name` of the board): " + SELECTION)


class PwmTrigger(Strict):
    """Instant in the PWM period at which the timer of a group raises its trigger output (IO-61)."""

    point: Literal["zero_low", "zero_high"] = Field(
        description="Instant of the trigger in the period. `zero_low`: middle of the interval in which every "
                    "high-side switch of the group is off and every low-side switch on (zero vector with the low "
                    "sides; low-side shunts carry the phase currents there). `zero_high`: middle of the interval in "
                    "which every high-side switch is on. Both instants are fixed by the counter, independent of the "
                    "duties.")


class PwmUse(Strict):
    """Frequency and dead time of a used board PWM group."""

    frequency_hz: PositiveInt = Field(description="PWM frequency of the group.")
    deadtime_ns: PositiveInt = Field(description="Dead time between switching one side of a half bridge off and "
                                                 "the other on. Checked against the minimum derived from the power "
                                                 "stage of the board.")
    # IO-24: margin on top of the dead time the board's power stage needs.
    deadtime_margin_ns: Annotated[int, Field(ge=0, le=(1 << 31) - 1)] | None = Field(
        None, description="Margin on top of the dead time the power stage of the board needs (spread of the "
                          "switching times, temperature). Left out: not chosen; the minimum is derived without "
                          "margin and a warning says so.")
    # IO-61 (ARCH-65 fast control loop): counting mode and trigger output of the group, family neutral.
    alignment: Literal["edge", "center"] = Field(
        "edge", description="Counting of the PWM timer. `edge`: the counter counts up and restarts every period "
                            "(behaviour before IO-61). `center`: the counter counts up and down, the pulses of all "
                            "pairs are symmetric to the turning points; frequency and dead time stay the same, the "
                            "halved counter range is derived from the timer clock. In both modes new duties of all "
                            "pairs take effect together in the middle of the interval in which every high-side "
                            "switch is off.")
    trigger: PwmTrigger | None = Field(
        None, description="Trigger output of the PWM timer (start of an ADC sample group, fast control loop). "
                          "Left out: no trigger output.")
    # SYS-116 (doc/safety/partner-and-degraded.md part C): the safe states the group may be put into, the one in
    # force is selected at run time; freewheel is always allowed and always the fallback.
    safe_states: list[Literal["freewheel", "active_short_low", "active_short_high"]] = Field(
        [], description="Safe states of the group out of which the application selects the one in force at run "
                        "time (for example by speed). `freewheel`: every switch off. `active_short_low`/"
                        "`active_short_high`: every low-side resp. high-side switch on, the others off - the windings "
                        "are shorted, the DC link is not charged. `freewheel` must be listed: it is the state after "
                        "a reset, the fallback for an invalid or unavailable selection (timer clock off, bridge "
                        "fault, failed start test) and the state the hardware fault input forces. Left out: "
                        "freewheel only, as before.")
    safe_state_test: bool = Field(False, description="`true`: the start-up test of the active short (build the "
                                                     "short at standstill, read it back, back to freewheel) is "
                                                     "offered for the group; a failed test removes the short for "
                                                     "the power cycle. Needs an active short in `safe_states`.")
    safe_state_test_us: Annotated[int, Field(ge=1, le=1000000)] | None = Field(
        None, description="How long the start-up test holds the short before it goes back to freewheel. Required "
                          "with `safe_state_test`.")
    verify: bool = Field(False, description="`true`: the values are not confirmed (e.g. not measured at the power "
                                            "stage).")


class AdcInternal(Strict):
    """Internal ADC input without pin (bandgap, reference, supply monitor) the project reads as an ADC channel."""

    name: Name = Field(description="Channel name, unique among all ADC channels of the project.")
    instance: str = Field(description="ADC instance of the derivative (e.g. `ADC0`).")
    channel: str = Field(description="Internal channel of the instance, one of its `internal_channels`.")


# IO-18
class NtcBetaLoad(SensorFacts):
    """NTC after the beta model, R(T) = R_n * exp(B * (1/T - 1/T_n)); temperature in 0.1 degC.

    The generator turns it into a table of points between t_min and t_max (step), the runtime interpolates.
    """

    kind: Literal["ntc_beta"] = Field(description="Load type: NTC thermistor described by the beta model; "
                                                  "delivers the temperature in 0.1 °C. The board channel must "
                                                  "deliver a resistance in ohm.")
    r_nominal_ohm: Annotated[int, Field(gt=0, le=(1 << 31) - 1)] = Field(description="Resistance R_n at the "
                                                                                     "nominal temperature.")
    t_nominal_ddegc: Annotated[int, Field(ge=-2731, le=10000)] = Field(250, description="Nominal temperature T_n "
                                                                                        "(usually 25 °C).")
    beta_k: Annotated[int, Field(gt=0, le=100000)] = Field(description="Beta value B of the NTC, from its data "
                                                                       "sheet.")
    t_min_ddegc: Annotated[int, Field(ge=-2731, le=10000)] = Field(-400, description="Lowest temperature of the "
                                                                                     "generated table.")
    t_max_ddegc: Annotated[int, Field(ge=-2731, le=10000)] = Field(1500, description="Highest temperature of the "
                                                                                     "generated table.")
    step_ddegc: Annotated[int, Field(gt=0, le=10000)] = Field(50, description="Temperature step between the points "
                                                                              "of the generated table.")
    max_error_ddegc: Annotated[int, Field(gt=0, le=10000)] = Field(
        10, description="Largest interpolation error of the generated table that nxconfig accepts.")


# IO-18
class TableLoad(SensorFacts):
    """Characteristic as points [board value, load value] with ascending board value; linear in between."""

    kind: Literal["table"] = Field(description="Load type: characteristic given as a table of points.")
    unit: SensorUnit = Field(description="Unit of the load value (`adc.phys` of the channel).")
    points: Annotated[list[Annotated[list[Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)]],
                                     Field(min_length=2, max_length=2)]], Field(min_length=2, max_length=255)] = \
        Field(description="Points `[board value, load value]` with ascending board value, linear in between; the "
                          "ends are held. The board value is in the unit the board channel delivers, the load value "
                          "in `unit`.",
              json_schema_extra={"unit": "board unit, unit of `unit`"})


AdcLoad = Annotated[NtcBetaLoad | TableLoad, Field(discriminator="kind")]


# IO-31: how a board ADC channel is read. Project level: the window follows the PWM frequency of the project, and the
# same board without the power stage in use needs no averaging. The board keeps describing the circuit (adc[].sensor).
class AdcSampling(Strict):
    """Reading of a used board ADC channel other than one conversion per read."""

    kind: Literal["pwm_period_average"] = Field(
        description="Sampling of the channel: `pwm_period_average` converts `samples` times at equally spaced "
                    "instants over exactly one realized period of `pwm_group` and delivers the rounded mean of the "
                    "raw values. For channels that carry the ripple of a switching stage, e.g. the DC link current "
                    "of a motor bridge; the conversion (`adc.phys`, `adc.mv`) applies to the mean.")
    pwm_group: Name = Field(description="PWM group of the project (`pwm.<group>`) whose realized period is averaged "
                                        "over.")
    samples: Annotated[int, Field(ge=2, le=64)] = Field(
        description="Conversions per read, equally spaced over one period; they must fit into it (samples times the "
                    "conversion time of `modules.bspAdcAccess`). Harmonics of the PWM frequency that are multiples "
                    "of `samples` are not cancelled, so a sharper ripple needs more samples. The reading task is "
                    "busy for one PWM period per read.",
        json_schema_extra=NO_UNIT)


# PLAT-21: interrupt sources the project gives a rank (0 = most urgent source that may call the RTOS). The NVIC
# level follows from the rank (platforms/common/interruptPriority), so the same project runs on a derivative with
# other NVIC priority bits. `source` names the kind of board entry the interrupt belongs to.
# IO-62: `sample_group` is no board entry but a project entry (`sample_groups.<name>`, its "group complete"
# interrupt).
InterruptSource = Literal["can", "uart", "spi", "i2c", "adc", "input_capture", "pwm", "dio", "sample_group"]


class InterruptRank(Strict):
    """Rank of the interrupt of one board entry, e.g. {source: can, name: CAN0, rank: 3}."""

    source: InterruptSource = Field(description="Kind of board entry the interrupt belongs to.")
    name: Name = Field(description="Board entry of `source` (e.g. the CAN bus `CAN0`); the project must use it.")
    rank: NonNegativeInt = Field(description="Urgency rank; 0 is the most urgent interrupt that may call the RTOS. "
                                             "The hardware priority follows from the rank.",
                                 json_schema_extra=NO_UNIT)
    note: str | None = Field(None, description=NOTE)


# ARCH-23
class InterruptUse(Strict):
    """Interrupt priorities of the project (checks.py: against the NVIC priority bits of the derivative, on a VIM
    core against its channels)."""

    # ARMv6-M has no BASEPRI and needs 0; a VIM has no such level.
    max_syscall_level: NonNegativeInt | None = Field(
        None, description="NVIC cores only: highest NVIC priority level (numerically lowest) that may call the RTOS; "
                          "the RTOS masks interrupts up to it. Left out: the default of the core architecture "
                          "(0 on ARMv6-M, which has no BASEPRI).",
        json_schema_extra=NO_UNIT)
    ranks: list[InterruptRank] = Field([], description="Rank of each interrupt source; every used CAN bus needs "
                                                       "one.")


# SYS-117 (doc/safety/partner-and-degraded.md part B, decisions B1-B6 of 2026-09-29): the degraded reaction SD. The
# groups and the mapping are project values; which events may be mapped at all is a fact of the safe supervisor
# (NX_SAFE_DEGRADABLE_EVENTS of ecu/safety/safeSupervisor/include/safeSupervisor/Degraded.h), checked by
# tools/nxconfig/degraded.py with the YAML path.
class SafetyOutputGroup(Strict):
    """A safe-output group: the part of the safe state that the degraded reaction switches alone."""

    name: Name = Field(description="Name of the group, used by `safety.degraded[].groups` and in the RTE targets "
                                   "`safety.degraded.<name>`. Bit k of the RTE signal `safety.degraded` is the k-th "
                                   "group of this list.")
    pwm: Name = Field(description="PWM group of the project (board `pwm`, used by the project) whose channels the "
                                  "group forces to their inactive level. Every used PWM group belongs to exactly one "
                                  "safe-output group once groups are given. The gate-driver enable of the PWM group "
                                  "joins the group with IO-45; until then it goes off only with S1.")
    note: str | None = Field(None, description=NOTE)


class DegradedMapping(Strict):
    """One event mapped to the degraded reaction SD instead of its fail-safe reaction."""

    event: str = Field(description="Event of the safe supervisor. Only the events on the supervisor's closed list "
                                   "may be degraded - today DEVICE_FAULT and HW_SHUTOFF (SYS-135); faults of the MCU itself (memory, image, "
                                   "program flow, watchdog, clock, supply, kernel) are always fail-safe and refused.")
    device: Name | None = Field(None, description="DEVICE_FAULT: the board device (`devices[].name`) whose faults "
                                                  "are degraded. Faults of class IDENTITY and malformed reports stay "
                                                  "fail-safe in any case.")
    groups: list[Name] = Field(description="Safe-output groups (`safety.output_groups[].name`) that go to their safe "
                                           "state when the event occurs; at least one. When the last group that "
                                           "still works is degraded, the reaction becomes S1 without a reset.")
    note: str | None = Field(None, description=NOTE)


class Safety(Strict):
    """Pins of the OpenBSW safety demo."""

    safe_state_output: Name = Field(description="Indicator of the safe state, a digital output: disabled (it "
                                                "floats) in the safe state and switched back with the level the "
                                                "application wrote last when the safe state is left. No actuator, "
                                                "and an output the application does not write - otherwise see "
                                                "`safe_state_output_shared_with_application`.")
    locked_pin: Name = Field(description="Digital output whose pin configuration gets locked.")
    test_pin: str = Field(description="Unconnected pin, checked cyclically as input.")
    # SYS-87 (user decision 2026-09-25, variant A): the indicator should be an output the application does not
    # write (AoU-30 of doc/safety/concept.md). nxconfig warns when an rte.yaml `dio.out` target writes it; this key
    # confirms the case on purpose. A text, not `true`: the confirmation has to say why, and the reason stays in the
    # reviewed configuration (AoU-10) instead of in a comment that a copy or a reformat loses.
    safe_state_output_shared_with_application: str | None = Field(
        None, description="Confirms on purpose that the application writes `safe_state_output` (an rte.yaml "
                          "`dio.out` target), with the reason as text - for example that the output is only a "
                          "status LED. Entering the safe state disables the indicator's pin (it floats), leaving "
                          "it switches the pin back with the level the application wrote last, so an actuator "
                          "there would float in the safe state and come back on without an enable (AoU-30 of the "
                          "safety concept). With the key, nxconfig does not warn about that case; without the "
                          "case, the key itself is reported as superfluous. Left out: not confirmed.")
    # SYS-117: the degraded reaction SD (AoU-04 and AoU-32 of the safety concept: the integrator confirms that the
    # remaining function is safe with the mapped groups in their safe state and that the groups are independent in
    # his hardware)
    output_groups: list[SafetyOutputGroup] = Field(
        [], description="Safe-output groups for the degraded reaction (SYS-117). Left out: none - every reaction "
                        "switches all outputs, as before.")
    degraded: list[DegradedMapping] = Field(
        [], description="Events that report and switch only their safe-output groups instead of the fail-safe "
                        "reaction (safe state for everything, reset). The rest of the ECU keeps running without a "
                        "reset, the application reads the degraded groups from the RTE signal `safety.degraded`, "
                        "and the groups stay degraded until the next power cycle, also across a reset. Left out: "
                        "none - every event keeps its fail-safe reaction.")

    @field_validator("safe_state_output_shared_with_application", mode="before")
    @classmethod
    def _reason(cls, value):
        if isinstance(value, bool):
            raise ValueError("expects the reason as text, not true/false: why the application may write the "
                             "indicator of the safe state (for example: \"LED1 is only a status LED\")")
        if isinstance(value, str) and not value.strip():
            raise ValueError("the reason must not be empty: why the application may write the indicator of the "
                             "safe state")
        return value


class BootloaderMemory(Strict):
    """Bootloader area at the start of the code flash and the RAM the bootloader (and its updater) runs in."""

    size: PositiveInt = Field(description="Flash of the bootloader from the start of `memory.flash`; whole "
                                          "sectors.",
                              json_schema_extra=BYTES)
    ram: Name = Field(description="RAM region of the derivative the bootloader runs in.")
    ram_size: PositiveInt | None = Field(None, description="RAM of the bootloader from the start of `ram`. Left "
                                                           "out: the whole region.",
                                         json_schema_extra=BYTES)
    stack_size: PositiveInt = Field(0x1000, description="Stack of the bootloader.", json_schema_extra=BYTES)


class ApplicationMemory(Strict):
    """RAM of the application. The no-init area (hard fault dump, boot request) ends at the end of the last region."""

    ram: list[Name] = Field(min_length=1, description="RAM regions of the derivative the application uses, in "
                                                      "address order and contiguous.")
    noinit_size: PositiveInt = Field(description="Size of the no-init area at the end of the last RAM region: it "
                                                 "survives a reset and holds the hard fault dump and the boot "
                                                 "request.",
                                     json_schema_extra=BYTES)
    # PLAT-12 (proposal of BL-05): the hot part at the start of the RAM as a project value instead of the fixed
    # 4 KB + 4 KB of the linker template.
    ramfunctions_size: PositiveInt | None = Field(
        None, description="RAM for flash routines that run from RAM (`.launchFromRAM`), at the start of the RAM. "
                          "Small derivatives (e.g. 14 KB SRAM) need less than the template default. Left out: the "
                          "default of the linker template (0x1000).",
        json_schema_extra=BYTES)
    hot_ram_size: PositiveInt | None = Field(
        None, description="Hot part at the start of the RAM: RAM functions plus hot data and bss. Left out: the "
                          "default of the linker template (0x2000 with a single RAM region, otherwise the whole "
                          "first region).",
        json_schema_extra=BYTES)
    # SYS-132 (safety review SYS-97 S8 2026-09-30): interrupt stack (MSP) and its MPU guard as a project value
    # instead of the family default of the linker template
    isr_stack_size: PositiveInt | None = Field(
        None, description="Interrupt stack (main stack: start-up up to the scheduler plus every interrupt level "
                          "nested). Multiple of 8. Size it with the worst case of tools/stackuse for the image plus "
                          "a reserve. Left out: the default of the linker template (0x1000).",
        json_schema_extra=BYTES)
    isr_stack_guard_size: PositiveInt | None = Field(
        None, description="Guard directly below the interrupt stack that the core may read but not write (MPU): "
                          "an overflow ends in the hard fault reaction instead of overwriting data. Multiple of 32 "
                          "(MPU granularity); it must hold the largest frame on the interrupt stack plus the larger "
                          "of that frame and an exception frame (tools/stackuse checks it). Left out: the default of "
                          "the linker template (0x400).",
        json_schema_extra=BYTES)
    # PLAT-112 (safety review group A 2026-10-01, S132-1 b): proof note as for the BSW task stacks (PLAT-107 S3)
    isr_stack_evidence: "StackEvidence | None" = Field(
        None, description="Proof note of the interrupt stack: `used_bytes` is the worst case on the main stack "
                          "(start-up plus every interrupt level nested, tools/stackuse --strict for this image) or "
                          "the high-water mark at the board (inject mspuse). Required for `isr_stack_size` below the "
                          "default of the linker template in an ASIL project (project.integrity not `qm`), "
                          "otherwise a warning. The interrupt stack must hold `used_bytes`. Left out: no proof "
                          "noted.")
    isr_stack_guard_evidence: "StackEvidence | None" = Field(
        None, description="Proof note of the guard: `used_bytes` is the guard tools/stackuse --strict requires for "
                          "this image (largest frame on the interrupt stack plus the larger of that frame and an "
                          "exception frame); only `method: stackuse`, a measurement cannot show it. Required for "
                          "`isr_stack_guard_size` below the default in an ASIL project, otherwise a warning. The "
                          "guard must hold `used_bytes`. Left out: no proof noted.")


# SYS-89 (stage K3 of doc/architecture/kalibrierung.md, decisions E4 a and E5 a of the user 2026-09-28)
class CalibrationMemory(Strict):
    """Calibration segment: the values of all calibration parameters (rte::PARAMETERS) in a flash area of their
    own, behind a `CALV` header (magic, format, layout identifier, length, CRC-32). The application checks the
    header at start-up and reacts as `on_invalid` says. tools/nxconfig/memory.py places it: in the code flash of
    `memory.flash` directly behind the first sector of the application (the vector table stays where the
    bootloader jumps to), so the download of the application does not grow over the whole flash."""

    region: Name = Field(description="Flash region of the derivative the segment lies in: the code flash of "
                                     "`memory.flash`, or a data flash region (kind `data_flash`). A data flash can "
                                     "only be written separately, which the bootloader learns with BL-31 (NVM-12); "
                                     "until then it is refused with that reason.")
    size: PositiveInt = Field(description="Size of the segment, whole sectors of the region: 32 bytes of header "
                                          "plus the values of all calibration parameters (the linker checks that "
                                          "they fit).",
                              json_schema_extra=BYTES)
    on_invalid: Literal["refuse", "defaults", "warn"] = Field(
        "refuse", description="Reaction at start-up when the header, the layout identifier or the CRC of the "
                              "segment does not fit the application. `refuse`: the application functions (RTE "
                              "runnables) do not start, the ECU stays reachable over UDS for a new download; "
                              "required as soon as one parameter is safety relevant (`safety` of swc.yaml other "
                              "than qm). `defaults`: the values built into the application image, development "
                              "profile only. `warn`: the values of the segment as they are. Each reaction other "
                              "than a valid segment is logged and reported to the fault memory (BSW event "
                              "`calibration.segment`).")
    profile: Literal["development", "production"] = Field(
        "development", description="`development`: the values are copied into a RAM working page at start-up, "
                                   "XCP calibrates there, the flash segment is the reference page. `production`: "
                                   "the values are read directly from the flash segment, there is no RAM copy and "
                                   "no write path; the project must not have an `xcp` section.")


class MemoryUse(Strict):
    """Memory partitioning (doc/architecture/README.md, section 7). The validity record takes the last sector of
    `flash`, the application the sectors between bootloader and record. Addresses follow from the derivative."""

    flash: Name = Field(description="Code flash region of the derivative (`memory[].name`, kind `code_flash`).")
    bootloader: BootloaderMemory = Field(description="Flash and RAM of the bootloader.")
    application: ApplicationMemory = Field(description="RAM of the application.")
    calibration: CalibrationMemory | None = Field(None, description="Calibration segment (SYS-89). Left out: the "
                                                                    "parameter values stay in the initialised "
                                                                    "data of the application (RTE-03).")


# NVM-07: the non-volatile memory of the project. `partition` is the one-time split of the emulated EEPROM (s32k1xx
# FlexNVM); it is written into the chip only by the console command "flexnvm partition" of a test build, with the user
# at the board (never at start-up). `blocks` fixes the address and write policy of NvM blocks, which otherwise are
# laid out in name order (tools/rtegen) - a fixed address keeps a block where it is when another block is added.
class NvmPartition(Strict):
    """Split of the emulated EEPROM, in bytes; the generator derives the codes of the partition command from the
    derivative (`eeprom_emulation`)."""

    eeprom_bytes: NonNegativeInt = Field(description="Bytes of emulated EEPROM (s32k1xx: FlexRAM used as EEPROM). "
                                                     "One of the sizes of the derivative.",
                                         json_schema_extra=BYTES)
    data_flash_bytes: NonNegativeInt = Field(description="Bytes of the partitionable flash that stay data flash; the "
                                                         "rest backs up the emulated EEPROM.",
                                             json_schema_extra=BYTES)
    partition_code: Code4 | None = Field(
        None, description="Code of the split in the partition command (s32k1xx: DEPART). Only needed where the "
                          "derivative has several codes for the same split; it must be one of them.",
        json_schema_extra=NO_UNIT)
    load_on_reset: bool = Field(True, description="`true`: the chip loads the emulated EEPROM during every reset "
                                                  "(s32k1xx: PGMPART FCCOB3 = 0, IFR EEERST = 1 - opposite "
                                                  "encodings). `false`: every start would need a flash command "
                                                  "(SETRAM), which the start-up never launches; the NvM would run on "
                                                  "its defaults.")
    key_slots: NonNegativeInt | None = Field(
        None, description="NVM-11: key slots of the security module to reserve in the emulated EEPROM (s32k1xx "
                          "CSEc: 0, 5, 10 or 20; FCCOB1). One of the key space sizes of the derivative; the bytes "
                          "come off the usable EEPROM. Left out on a derivative with a key space: the partition "
                          "command refuses to write - the space is fixed with the partition and cannot be added "
                          "later.",
        json_schema_extra=NO_UNIT)
    key_verify_only: bool = Field(False, description="NVM-11: security flag extension (s32k1xx: SFE, FCCOB2): lets "
                                                     "a key be marked verify-only. Not offered yet: the partition "
                                                     "command writes it off.")


NvmType = Literal["boolean", "uint8", "sint8", "uint16", "sint16", "uint32", "sint32", "float32", "float64"]
NvmIntegrity = Literal["qm", "asil_a", "asil_b", "asil_c", "asil_d"]


class NvmBlockUse(Strict):
    """Definition of one NvM block (NVM-18: data type, scalar or vector, integrity), its place and write policy."""

    type: NvmType | None = Field(
        None, description="NVM-18: platform type of the block's value (of one element of a vector). The ports "
                          "connected to `nvm.<Block>` must have this type. Left out: the type comes from the "
                          "writing port (before NVM-18; the generator warns and proposes the definition). Not for "
                          "the blocks of the fault memory (DemEvents, DemEntries), whose layout comes from diag.yaml.")
    length: PositiveInt | None = Field(
        None, description="NVM-18: 1 = scalar, more = vector (array) of that many elements of `type`. Left out: 1. "
                          "Only with `type`.",
        json_schema_extra=NO_UNIT)
    integrity: NvmIntegrity | None = Field(
        None, description="NVM-18: `qm`: one stored value with CRC-16 (as before). `asil_a` .. `asil_d`: two "
                          "copies, each with CRC-32 and write counter; a defective copy is repaired from the other "
                          "and reported to the fault memory, with both defective the block starts from its default "
                          "and the application reads the quality 'unsecured' (`nvm.qlf.<Block>`). All ASIL levels "
                          "use the same mechanism. Left out: `qm`.")
    address:NonNegativeInt | None = Field(None, description="Offset of the block's first slot in the emulated "
                                                             "EEPROM, a multiple of 4. Left out: laid out after the "
                                                             "fixed blocks, in name order.",
                                           json_schema_extra=BYTES)
    write: Literal["shutdown", "change"] | None = Field(
        None, description="`shutdown`: written at the run-down and on request. `change`: also cyclically after a "
                          "change, at most once per `min_interval_ms`. Left out: the default of the block (blocks of "
                          "components `shutdown`, blocks of the fault memory from diag.yaml).")
    min_interval_ms: PositiveInt | None = Field(None, description="Only with `write: change`: shortest time between "
                                                                  "two writes of the block.")


class DataFlashArea(Strict):
    """NVM-12: one area of the data flash - whole sectors the application reads like memory and erases and programs
    at run time (libs/bsw/nvmDataFlash, dataflash::AreaService)."""

    size: PositiveInt = Field(description="Size of the area, whole sectors of the data flash region of the "
                                          "derivative (they are erased on their own).",
                              json_schema_extra=BYTES)
    purpose: Literal["calibration", "data"] = Field(
        description="`calibration`: calibration data changed in the device (maps, data sets). `data`: other data of "
                    "the application. Not for logs or images (the user's purpose 2026-09-28: calibration data).")
    offset: NonNegativeInt | None = Field(None, description="Offset of the area in the data flash, a multiple of the "
                                                            "sector size. Left out: laid out after the fixed areas, "
                                                            "in name order.",
                                          json_schema_extra=BYTES)


class NvmDataFlash(Strict):
    """NVM-12: what the data flash holds. tools/nxconfig checks it against the part the partition leaves as data
    flash (`nvm.partition.data_flash_bytes`, or the whole data flash region of a derivative without partition) and
    lays it out; tools/bswgen generates the table (nvm/nvmDataFlash.h)."""

    areas: dict[Name, DataFlashArea] = Field(description="Areas of the data flash; key: area name (C identifier, "
                                                         "the name in the generated table).")


class NvmUse(Strict):
    """Non-volatile memory of the project: partition of the emulated EEPROM, block definitions and places."""

    partition: NvmPartition | None = Field(None, description="Partition of the emulated EEPROM. Left out: the chip "
                                                             "is used as it is, no partition command is built.")
    blocks: dict[Name, NvmBlockUse] = Field({}, description="Definition (NVM-18: type, length, integrity), place "
                                                            "and write policy per NvM block; key: block name "
                                                            "(`nvm.<Block>` of rte.yaml, or DemEvents/DemEntries of "
                                                            "the fault memory).")
    data_flash: NvmDataFlash | None = Field(None, description="NVM-12: areas of the data flash (name, size, purpose). "
                                                              "Left out: the application does not use the data "
                                                              "flash.")


class IsoTpUse(Strict):
    """ISO 15765-2 parameters of the diagnostic connection (normal addressing, classic CAN).

    Bootloader and application answer with the same values, so one tester serves both. Ranges are the ones the
    protocol can encode; what is plausible for a download is checked in diag.py.
    """

    padding: Annotated[int, Field(ge=0, le=0xFF)] = Field(0xCC, description="Filler byte of frames shorter than 8 "
                                                                            "bytes.",
                                                          json_schema_extra=NO_UNIT)
    block_size: Annotated[int, Field(ge=0, le=0xFF)] = Field(
        0, description="Block size BS of the flow control frame: consecutive frames before the next flow control; "
                       "0: no further flow control.", json_schema_extra=NO_UNIT)
    stmin_ms: Annotated[int, Field(ge=0, le=0xFF)] = Field(
        0, description="Minimum separation time STmin of the flow control frame; at most 127.")
    n_cr_ms: PositiveInt = Field(1000, description="Timeout N_Cr for the next consecutive frame.")
    n_bs_ms: PositiveInt = Field(1000, description="Timeout N_Bs for the flow control frame.")


# Name of the deliberately insecure master key in the repository (tools/canflash/keys). Host tests and CI use it;
# a project has to allow it explicitly (allow_insecure_test_key), otherwise configuring it is an error.
INSECURE_TEST_KEY_NAME = "nextbsw-insecure-test"


# BL-03
class BootloaderSecurity(Strict):
    """Security access of the bootloader. The device answers a seed with HMAC-SHA256(device key, seed); the
    device key is HMAC-SHA256(master key, chip UID) and is written once per device by the provisioning tool
    (tools/canflash/provision.py). The master key never enters the repository or the bootloader image: the project
    names it, the workstation holds it (%LOCALAPPDATA%/neXtBSW/keys/<key>.key or an environment variable)."""

    # later e.g. ed25519, where the device holds a public key only
    method: Literal["hmac-sha256"] = Field("hmac-sha256", description="Method of the key calculation.")
    key: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")] | None = Field(
        None, description="Name of the master key in the key store of the workstation. Left out: the board name - "
                          "one key for every project of a board, so that switching between two applications of the "
                          "same hardware keeps the provisioned device key.")
    allow_insecure_test_key: bool = Field(False, description="`true`: the deliberately insecure test key of the "
                                                             "repository (`nextbsw-insecure-test`) may be named in "
                                                             "`key`. Test profiles only.")


class BootloaderDiag(Strict):
    """How the bootloader identifies itself in its flash (DID 0xF180) and where the application looks for it."""

    # PLAT-08 rename, PLAT-51 case-sensitive comparison.
    version_prefix: Annotated[str, Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")] \
        = Field("nextbsw-bl", description="Prefix of the bootloader version string, without the version number: "
                                          "identifies the bootloader software. The application searches the "
                                          "bootloader area for it, the updater accepts an image with the same "
                                          "prefix and major version. The former name of the software "
                                          "(`S32K14x-BL`) is accepted as well, so boards flashed before the rename "
                                          "keep working. Compared byte by byte and case-sensitively.")
    scan_start: NonNegativeInt | None = Field(
        None, description="First address the application searches for the version string; the end is the start of "
                          "the application. Left out: behind the flash area the family reserves (s32k1xx: vector "
                          "table and flash configuration field up to 0x40F).",
        json_schema_extra=NO_UNIT)
    security: BootloaderSecurity = Field(BootloaderSecurity(), description="Security access of the bootloader "
                                                                           "(seed and key).")


# COM-11, ARCH-07
class Diagnostics(Strict):
    """Diagnostic addresses of the ECU (doc/architecture/README.md, section 4: CAN ids and UDS addresses are a
    decision of the project). Used by the bootloader (bootloader/src/core), the application UDS stack (ecu/uds)
    and tools/canflash; generated into <build>/generated/board/bootloader/bl_diag.h.

    The CAN bus the application answers on is the diagnostic bus of the ECU description (rte.yaml uds.bus); the
    bootloader listens on bootloader.can.
    """

    request_id: NonNegativeInt = Field(description="CAN identifier of the physically addressed request (11 bit, "
                                                   "must not collide with other identifiers of the bus).",
                                       json_schema_extra=NO_UNIT)
    response_id: NonNegativeInt = Field(description="CAN identifier of the responses of this ECU.",
                                        json_schema_extra=NO_UNIT)
    functional_id: NonNegativeInt = Field(0x7DF, description="CAN identifier of the functionally addressed request "
                                                             "(ISO 14229 \"all nodes\").",
                                          json_schema_extra=NO_UNIT)
    tester_address: Annotated[int, Field(ge=0, le=0xFF)] = Field(
        0xF1, description="Transport address of the tester (1 byte) in the UDS stack.", json_schema_extra=NO_UNIT)
    ecu_address: Annotated[int, Field(ge=0, le=0xFF)] = Field(
        0x2A, description="Transport address of this ECU (1 byte) in the UDS stack.", json_schema_extra=NO_UNIT)
    isotp: IsoTpUse = Field(IsoTpUse(), description="ISO-TP parameters of the diagnostic connection.")
    bootloader: BootloaderDiag = Field(BootloaderDiag(), description="Identification and security access of the "
                                                                     "bootloader.")


# TOOL-01, TOOL-26, COM-08
class XcpUse(Strict):
    """XCP on CAN: bus and CAN identifiers, a decision of the project like the diagnostic addresses. tools/rtegen
    generates xcp/xcpCanConfiguration.h from it (ecu/xcp, tools/a2lgen) and adds master_id to the hardware receive
    filter of the bus. diag.py checks range, bus and collisions with the YAML path (11/29 bit, diagnostics, DBC of
    the bus)."""

    bus: Name | None = Field(None, description="Board CAN bus from `use.can` XCP runs on. Left out: the diagnostic "
                                               "bus.")
    master_id: Annotated[int, Field(ge=0, le=0xFFFFFFFF)] = Field(
        description="CAN identifier master -> ECU (commands, CTO); bit 31 set: 29-bit identifier (encoding of the "
                    "DBC).", json_schema_extra=NO_UNIT)
    slave_id: Annotated[int, Field(ge=0, le=0xFFFFFFFF)] = Field(
        description="CAN identifier ECU -> master (responses and errors), same encoding as `master_id`.",
        json_schema_extra=NO_UNIT)


class BootloaderUse(Strict):
    """CAN bus and devices of the bootloader."""

    can: Name = Field(description="Board CAN bus of bootloader and updater; its bit timing is `can.<bus>`.")
    init: list[Name] | None = Field(
        None, description="Board devices the bootloader switches on before CAN (drivers in "
                          "`bootloader/devices/<module>`). Left out: the transceiver of `can`, if its module has a "
                          "bootloader hook. Other used devices with a hook are only read (e.g. SBC watchdog).")
    # BL-28 (user decision 2026-09-21, bl26-sbc-watchdog): per project, default leave; the integrator decides.
    # BL-41 (safety review SBC watchdog 2026-10-06, SDMS0-1 c): leave takes effect only in development mode
    # BL-42 (safety review bootloader 0.3.10 2026-10-07, BL41-2 b): with SDMS 0 also served when the SBC is only read
    sbc_watchdog: Literal["leave", "serve"] = Field(
        "leave", description="Watchdog of a system basis chip the bootloader switches on (`init`). `serve`: the "
                             "bootloader sets the watchdog to the fixed period of the device hook (sbcUja113x: "
                             "autonomous, 1024 ms) in Standby, enters Normal mode and triggers it while it runs; "
                             "after the jump the application must take the watchdog over within that period "
                             "(assumption of use). `leave`: takes effect only while the SBC is in software "
                             "development mode (UJA113x: SDMS 1) - the bootloader does not touch the watchdog and "
                             "enters Normal mode. Without development mode (SDMS 0) the watchdog runs from the SBC "
                             "reset on, in Standby too (UJA113x: timeout, 128 ms), so the bootloader serves it as "
                             "with `serve` whatever this value says (BL-41); the application takes it over the same "
                             "way. This holds for an SBC that is only read (not in `init`) as well: with SDMS 0 the "
                             "bootloader sets up and triggers only the watchdog, the SBC stays in Standby and V2 and "
                             "CAN are not switched on (BL-42). `serve` differs from `leave` only with development mode: it also sets a watchdog "
                             "the application left running to the fixed setting. The MTPNV is never changed. Boards "
                             "without such an SBC: `leave`.")


# COM-21 (COM-07): the one CAN frame that wakes the ECU from Sleep of a system basis chip with partial networking
# (project.yaml modules.<device>.can_wakeup_frame of a sbcUja113x device). Which frame wakes this ECU is network
# knowledge of the project; whether the SBC can filter at all (/FD variant) is the board part. The bit rate is not
# asked again: tools/nxconfig takes can.<bus>.bitrate of the bus at the device's transceiver.
CAN_WAKEUP_FRAME = "can_wakeup_frame"


class CanWakeupFrame(Strict):
    """Selective CAN wake-up (ISO 11898-2/-6 partial networking) of a system basis chip."""

    id: Annotated[int, Field(ge=0)] = Field(description="Identifier of the wake-up frame (11 or 29 bit).",
                                            json_schema_extra=NO_UNIT)
    extended: bool = Field(False, description="`true`: 29 bit identifier, `false`: 11 bit.")
    compare_mask: Annotated[int, Field(ge=0)] | None = Field(
        None, description="Identifier bits that must match (bit = 1); bits 0 are don't care. Left out: all bits, the "
                          "frame must carry exactly `id`.", json_schema_extra=NO_UNIT)
    dlc: Annotated[int, Field(ge=0, le=8)] | None = Field(
        None, description="Data length the frame must have; with it the data field is part of the match. Left out: "
                          "only the identifier is compared.", json_schema_extra=NO_UNIT)
    data_mask: list[Annotated[int, Field(ge=0, le=255)]] = Field(
        [], description="Per data byte 0..dlc-1: the frame wakes if one of its bits set here is set in the frame. "
                        "Exactly `dlc` entries, at least one not 0 (left out with `dlc` 0).", json_schema_extra=NO_UNIT)
    fd_passive: bool = Field(False, description="`true`: CAN FD frames on the bus are tolerated in Sleep; `false`: "
                                                "they count as errors and wake the SBC after 32 of them.")


# IO-51 (concept doc/architecture/pin-interrupts.md, variant A, decision of the user 2026-09-29): events on a digital
# input of the board, counted with time stamp and qualifier by the pinEvent module (IO-53) and offered to the RTE as
# pinevent.<NAME>.count/.time_us/.qlf (IO-54). The edge is logical, relative to the active level of the board (F2);
# debounce is done in software (F3); the rate limit disarms the pin, reports QLF_ERROR and re-arms it automatically
# after `rearm_ms` (F4). The priority is the rank of the input (interrupts.ranks, source dio) - no second priority
# here. Checks with YAML path: tools/nxconfig/pinevent.py.
PinEventEdge = Literal["activate", "deactivate", "both"]
PIN_EVENT_MAX_DEBOUNCE_US = 1_000_000
PIN_EVENT_MAX_EVENTS = 65535
PIN_EVENT_MAX_WINDOW_MS = 60_000
PIN_EVENT_MAX_REARM_MS = 600_000


class PinEvent(Strict):
    """Events on one digital input of the board (pin interrupt on an edge)."""

    edge: PinEventEdge = Field(description="Edge that counts, relative to the active level of the board input "
                                           "(`dio.inputs[].active`): `activate` (the input becomes active), "
                                           "`deactivate` (it becomes inactive) or `both`. nxconfig derives the "
                                           "physical edge (rising/falling) from the board.")
    debounce_us: Annotated[int, Field(ge=0, le=PIN_EVENT_MAX_DEBOUNCE_US)] = Field(
        description="Software debounce: after an accepted edge, further edges are ignored for this time and the level "
                    "is confirmed afterwards. 0: no debounce. Must be shorter than the shortest spacing the rate "
                    "limit allows (`window_ms` / `max_events`). More than 0 needs edge detectors that report both "
                    "edges (`pin_interrupts.edges` of the derivative).")
    max_events: Annotated[int, Field(ge=1, le=PIN_EVENT_MAX_EVENTS)] = Field(
        description="Rate limit: at most this many candidate edges (edges of the configured direction, including "
                    "the ones the debounce later drops as glitches) per `window_ms`. One more disarms the pin, the "
                    "qualifier becomes QLF_ERROR (interrupt storm: bouncing contact, broken wire, fault on the line).",
        json_schema_extra=NO_UNIT)
    window_ms: Annotated[int, Field(ge=1, le=PIN_EVENT_MAX_WINDOW_MS)] = Field(
        description="Time window of the rate limit.")
    rearm_ms: Annotated[int, Field(ge=1, le=PIN_EVENT_MAX_REARM_MS)] = Field(
        description="Hold-off after the rate limit tripped: the pin is armed again automatically after this time.")
    wakeup: bool = Field(False, description="Reserved: the input as wake-up source of an MCU low-power mode. `true` "
                                            "is rejected until neXtBSW has an MCU low-power mode; today the ECU "
                                            "sleeps through the supply (SBC local wake inputs, `ecu.wakeup`).")
    note: str | None = Field(None, description=NOTE)


# SYS-86 (doc/safety/hardware-mechanisms.md section 2.2): a threshold on a board signal that forces the outputs of
# a PWM group to their inactive level in hardware, without the core. The comparator compares the pin against its
# DAC; its output is routed by the family code to a fault input of the group's timer. Recovery is manual only: the
# outputs stay forced until a reset (the latching behaviour of every software path, SM-40). Checks with YAML path:
# tools/nxconfig/hwthreshold.py; the DAC level and the routing are derived by the family generator.
HW_THRESHOLD_MAX_FILTER_NS = 100_000


class HwThreshold(Strict):
    """Hardware threshold on a board signal that switches a PWM group off without software."""

    input: Name = Field(description="Board comparator input (`comparators.inputs[].name`) carrying the signal.")
    trip: Literal["above", "below"] = Field(description="Direction that trips: the signal rises above the "
                                                        "threshold, or falls below it.")
    threshold: Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)] = Field(
        description="Threshold in the unit of the signal (the board ADC entry named by the comparator input: mA of a "
                    "shunt amplifier, mV of a divider, ...). The DAC level is rounded towards the safe side: an "
                    "`above` threshold trips at or below this value, a `below` threshold at or above it.",
        json_schema_extra={"unit": "unit of the signal"})
    reference: Literal["supply", "bandgap"] = Field(
        description="Reference of the comparator's DAC: `supply` (the supply pin the board states, e.g. VDDA - "
                    "ratiometric with a sensor on the same supply) or `bandgap` (the internal reference, "
                    "independent of the supply, small range).")
    pwm: Name = Field(description="PWM group of the project (`pwm.<group>`) whose outputs are forced to their "
                                  "inactive level when the threshold trips.")
    filter_ns: Annotated[int, Field(ge=0, le=HW_THRESHOLD_MAX_FILTER_NS)] = Field(
        0, description="Glitch filter of the timer's fault input: a crossing shorter than this is ignored. It adds to "
                       "the reaction time. 0: no filter.")
    verify: bool = Field(False, description="`true`: the threshold is not confirmed (e.g. not measured on a board).")
    note: str | None = Field(None, description=NOTE)
# IO-62 (doc/architecture/fast-control-loop.md section 3): a named set of board ADC channels converted after every
# hardware trigger of a PWM group, without software. The trigger instant is the trigger point of the group
# (`pwm.<group>.trigger`, IO-61) plus `delay_ns`; channels on different ADCs are converted at the same time, channels
# on the same ADC one after another in the listed order. One interrupt at the end of the whole group ("group
# complete", contract libs/bsp/bspSampleGroup) hands the raw values to its consumer. The ADC instances of a group are
# exclusive to the hardware chain: no software conversion (use.adc, adc_internal) may run on them. Checks with YAML
# path: tools/nxconfig/samplegroup.py; routing and register values: the family generator.
SAMPLE_GROUP_MAX_DELAY_NS = 1_000_000


class SampleGroup(Strict):
    """Board ADC channels converted after every hardware trigger of a PWM group (fast control loop, IO-62)."""

    pwm: Name = Field(description="PWM group of the project (`pwm.<group>`) whose trigger output starts the group; "
                                  "the group needs `pwm.<group>.trigger`.")
    channels: Annotated[list[Name], Field(min_length=1, max_length=32)] = Field(
        description="Board ADC entries (`adc[].name`) of the group, in the order of the results. Entries on "
                    "different ADCs are sampled at the same instant, entries on one ADC one after another in this "
                    "order (nxconfig warns and names the time offset).", json_schema_extra=NO_UNIT)
    delay_ns: Annotated[int, Field(ge=0, le=SAMPLE_GROUP_MAX_DELAY_NS)] = Field(
        0, description="Time from the trigger point of the PWM group to the start of the first sample. The "
                       "realised value is a whole number of sequencer clocks (nxconfig shows it).")
    verify: bool = Field(False, description="`true`: the sampling instant is not confirmed (e.g. not measured on a "
                                            "board).")
    note: str | None = Field(None, description=NOTE)
# ------------------------------------------------------------------------ NVM-14: keys of the security module
# Decision of the user 2026-09-29 (csec-pgmpart-rueckweg): after a partition with key space the only way back is the
# debug authentication with the MASTER_ECU_KEY, and one key with WRITE_PROTECTION removes it for ever. So the schema
# offers the SHE key flags WITHOUT write protection, the validators refuse the word in every spelling with their own
# message, and tools/nxconfig/security.py refuses it once more on the loaded project - also after a later extension
# of this schema. The key value never stands in the project: the project names it, the workstation holds it
# (tools/nxconfig/keystore.py, outside the repository).
KEY_FLAGS = ("boot_protection", "debugger_protection", "key_usage", "wildcard", "verify_only")
KeyFlag = Literal["boot_protection", "debugger_protection", "key_usage", "wildcard", "verify_only"]
FORBIDDEN_KEY_FLAG = "write_protection"
_WRITE_PROTECTION_SPELLINGS = ("writeprotection", "writeprotect", "writeprotected", "writeprot", "wp")
WRITE_PROTECTION_REFUSED = ("WRITE_PROTECTION is never allowed for a key (NVM-14, decision csec-pgmpart-rueckweg): "
                            "one write-protected key makes the debug authentication with the MASTER_ECU_KEY - the "
                            "only way back after a partition with key space - impossible for ever")
KeyStoreName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


def is_write_protection(value) -> bool:
    """True for any spelling of the SHE flag WRITE_PROTECTION (write_protection, WRITE-PROTECT, WriteProtected, wp)."""
    return isinstance(value, str) and "".join(c for c in value.lower() if c.isalnum()) in _WRITE_PROTECTION_SPELLINGS


class SecurityKey(Strict):
    """Attributes of one key of the security module (SHE key flags; s32k1xx: CSEc). Only what the key may be used
    for - the key value is never part of the project."""

    flags: list[KeyFlag] = Field(
        [], description="SHE key flags set when the key is loaded: `boot_protection` (unusable after a failed secure "
                        "boot), `debugger_protection` (unusable while a debugger is attached), `key_usage` (CMAC "
                        "instead of encryption), `wildcard` (may be loaded without the chip UID), `verify_only` (CMAC "
                        "verification only; needs `nvm.partition.key_verify_only`). WRITE_PROTECTION is deliberately "
                        "not offered and refused in every spelling (NVM-14): one write-protected key makes the debug "
                        "authentication - the only way back after a partition with key space - impossible for ever.")

    @model_validator(mode="before")
    @classmethod
    def _no_write_protection_field(cls, value):
        if isinstance(value, dict) and any(is_write_protection(k) for k in value):
            raise ValueError(WRITE_PROTECTION_REFUSED)
        return value

    @field_validator("flags", mode="before")
    @classmethod
    def _no_write_protection_flag(cls, value):
        if isinstance(value, list) and any(is_write_protection(v) for v in value):
            raise ValueError(WRITE_PROTECTION_REFUSED)
        return value


class SecurityUse(Strict):
    """Keys of the hardware security module the project loads (s32k1xx: CSEc; SHE key names). The key space itself
    is reserved by `nvm.partition.key_slots`."""

    stage: Literal["development", "production"] = Field(
        "development", description="`development`: development boards; every board of the project gets the same "
                                   "MASTER_ECU_KEY from the key store of the workstation, so the debug "
                                   "authentication can bring any of them back to the factory state. `production`: "
                                   "per device keys - not offered yet, refused.")
    master_ecu_key: KeyStoreName | None = Field(
        None, description="Name of the MASTER_ECU_KEY in the key store of the workstation "
                          "(`%LOCALAPPDATA%/neXtBSW/keys/csec/<name>.key` or `NX_CSEC_KEY_DIR`; create it with "
                          "`nxconfig csec-key new`). A name, never the key: a value that looks like key material is "
                          "refused. Left out: the board name.")
    keys: dict[Name, SecurityKey] = Field(
        {}, description="Flags per key; key: SHE name (`MASTER_ECU_KEY`, `BOOT_MAC_KEY`, `BOOT_MAC`, `KEY_1` ... "
                        "`KEY_<n>` up to the free slots of `nvm.partition.key_slots`). A key left out gets no flags.")

    @model_validator(mode="before")
    @classmethod
    def _no_write_protection_field(cls, value):
        if isinstance(value, dict) and any(is_write_protection(k) for k in value):
            raise ValueError(WRITE_PROTECTION_REFUSED)
        return value


# PLAT-107 (safety review 2026-10-01, PLAT-26/S3): proof note of a task stack - where the stack need of the task in
# this project's image is shown. Checked by tools/nxconfig/bswtasks.py: the stack must hold used_bytes.
class StackEvidence(Strict):
    """Proof note of a BSW task stack: what the task uses of its stack and where that is shown."""

    method: Literal["stackuse", "high_water_mark"] = Field(
        description="`stackuse`: worst case of the static analysis tools/stackuse for the image of this project. "
                    "`high_water_mark`: high-water mark measured at the board (console `stats`) after the deepest "
                    "paths of the task have run (covers only what ran).")
    used_bytes: PositiveInt = Field(description="Stack the task uses according to the proof (worst case of "
                                                "tools/stackuse, or the measured high-water mark); the stack of "
                                                "the task must hold it.", json_schema_extra=BYTES)
    reference: str = Field(min_length=1, description="Where the proof is: report file and image (version, commit), "
                                                     "or measurement record (date, build, test steps).")


# PLAT-26: stack and merge of a BSW task (ecu/asyncCoreConfiguration/tasks.yaml) or of the OpenBSW idle and timer
# task; defaults, floors and allowed merges in ecu/configuration/taskStacks.yaml, checked by tools/nxconfig/bswtasks.py
class BswTaskUse(Strict):
    """Stack of one BSW task, or the task it runs in instead of a task of its own."""

    stack_bytes: PositiveInt | None = Field(
        None, description="Stack of the task. At least `min_stack_bytes` of ecu/configuration/taskStacks.yaml and a "
                          "multiple of its `multiple_of` (8, the safety task 32: MPU region). Below the default "
                          "nxconfig warns: confirm the value with the high-water mark (console `stats`) or "
                          "tools/stackuse. Left out: the default of taskStacks.yaml.",
        json_schema_extra=BYTES)
    run_in: Name | None = Field(
        None, description="Merge the task into another BSW task: no FreeRTOS task and no stack of its own, its work "
                          "runs in the named task at that task's priority (its stack has to hold both). Only the "
                          "merges taskStacks.yaml lists for the task (`run_in`); never for or into the safety task. "
                          "Left out: the task runs on its own.")
    # PLAT-107
    evidence: StackEvidence | None = Field(
        None, description="Proof note of the stack (tools/stackuse or high-water mark at the board). Required for a "
                          "stack below the default in an ASIL project (project.integrity not `qm`), otherwise a "
                          "warning. Left out: no proof noted.")
    note: str | None = Field(None, description=NOTE)

    @model_validator(mode="after")
    def _stack_or_merge(self):
        if self.stack_bytes is not None and self.run_in is not None:
            raise ValueError("stack_bytes and run_in exclude each other: a merged task has no stack of its own")
        if self.evidence is not None and self.run_in is not None:
            raise ValueError("evidence and run_in exclude each other: a merged task has no stack of its own (the "
                             "proof belongs to the task it runs in)")
        return self


# PLAT-112: ApplicationMemory names StackEvidence before its definition (proof note of the interrupt stack); resolve
# it here (and in MemoryUse, which holds it) so that the schema (Studio, docgen) sees the nested model, not a forward
# reference.
ApplicationMemory.model_rebuild()
MemoryUse.model_rebuild()


# PLAT-27: console (command input) and logger (log lines on the terminal UART) as a function of the project, and the
# size of the logger ring buffer. The bounds are those of the root CMakeLists.txt (PLAT-04 block), which writes the
# generated logger/Config.h from NX_LOGGER_BUFFER_BYTES; checked by tools/nxconfig/console.py.
LOGGER_BUFFER_MIN_BYTES = 512
LOGGER_BUFFER_MAX_BYTES = 65536
LOGGER_BUFFER_DEFAULT_BYTES = 8192      # default of the root CMakeLists.txt (PLAT-04, board 2026-09-18)


class ConsoleUse(Strict):
    """Console and logger of the application on the terminal UART."""

    enabled: bool = Field(
        True, description="`false`: no console (no command input, no console commands of the application) and no "
                          "logger (log calls do nothing, no ring buffer). The UART stays initialised: the safety "
                          "lines of the safe supervisor (SAFETY RESET, safety::safeConsoleWrite) and the start-up "
                          "lines are written to it synchronously in every image. Left out: `true`.")
    logger_buffer_bytes: Annotated[int, Field(ge=LOGGER_BUFFER_MIN_BYTES, le=LOGGER_BUFFER_MAX_BYTES)] | None = Field(
        None, description=f"Size of the logger ring buffer. A full buffer drops the oldest entries (console "
                          f"`logger lost` counts them); below {LOGGER_BUFFER_DEFAULT_BYTES} bytes the start-up of the "
                          f"eval board lost lines (board 2026-09-18). Left out: {LOGGER_BUFFER_DEFAULT_BYTES}.",
        json_schema_extra=BYTES)
    note: str | None = Field(None, description=NOTE)


class Project(Strict):
    """Project file `projects/<project>/project.yaml`: what one ECU software uses of its board."""

    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    project: ProjectInfo = Field(description="Identification of the project; board and ECU description it is "
                                             "built from.")
    clock: ProjectClock = Field(description="System clock of the project.")
    memory: MemoryUse | None = Field(None, description="Memory partitioning. Left out: no bootloader, no generated "
                                                       "memory layout.")
    # NVM-07
    nvm: NvmUse | None = Field(None, description="Non-volatile memory: partition of the emulated EEPROM, fixed "
                                                 "block places. Left out: chip as it is, blocks in name order.")
    # NVM-14
    security: SecurityUse | None = Field(None, description="Keys of the hardware security module (flags, name of "
                                                           "the MASTER_ECU_KEY in the key store of the "
                                                           "workstation). Left out: no keys described.")
    bootloader: BootloaderUse | None = Field(None, description="CAN bus and devices of the bootloader.")
    # COM-11: required together with `memory` (bl_config.h)
    diagnostics: Diagnostics | None = Field(None, description="UDS addresses of bootloader and application; "
                                                              "required together with `memory`.")
    # TOOL-26
    xcp: XcpUse | None = Field(None, description="XCP on CAN. Left out: no XCP slave in the application.")
    use: Use = Field(Use(), description="Board entries the project uses.")
    # ARCH-18
    peripheral_clocks: dict[Name, PeripheralClockUse] = Field(
        {}, description="Functional clock of a peripheral instance; key: instance of the derivative, or a clock "
                        "gate of `clock.pcc`. Only needed where the derivative's default is not wanted or where it "
                        "has none.")
    can: dict[Name, CanUse] = Field({}, description="Bit timing per CAN bus; key: board CAN bus, required for every "
                                                    "used bus.")
    uart: dict[Name, UartUse] = Field({}, description="Baud rate per UART; key: board UART, required for every used "
                                                      "UART.")
    # IO-28
    i2c: dict[Name, I2cUse] = Field({}, description="Rate and time limit per I2C bus; key: board I2C bus. A used bus "
                                                    "without an entry runs at 100 kHz (standard mode).")
    pwm: dict[Name, PwmUse] = Field({}, description="Frequency and dead time per PWM group; key: board PWM group.")
    adc_internal: list[AdcInternal] = Field([], description="Internal ADC inputs without pin the project reads.")
    # IO-18
    adc_loads: dict[Name, AdcLoad] = Field(
        {}, description="Load connected to a board ADC channel with a sensor circuit, e.g. the NTC of a motor; key: "
                        "the channel.")
    # IO-31
    adc_sampling: dict[Name, AdcSampling] = Field(
        {}, description="Sampling of a used board ADC channel other than one conversion per read, e.g. averaged over "
                        "one PWM period; key: the channel. Left out: one conversion per read.")
    # PLAT-21
    interrupts: InterruptUse = Field(InterruptUse(), description="Interrupt priorities as ranks.")
    # IO-51
    pin_events: dict[Name, PinEvent] = Field(
        {}, description="Events on digital inputs (pin interrupt on an edge); key: a used board input "
                        "(`dio.inputs[].name`). Each one needs a rank (`interrupts.ranks`, source `dio`). Left out: "
                        "inputs are only read as levels (`dio.in.<NAME>`).")
    safety: Safety | None = Field(None, description="Pins of the OpenBSW safety demo. Left out: none.")
    modules: dict[Name, dict[Name, int | bool | str | CanWakeupFrame]] = Field(
        {}, description="Parameters of the modules; key: board device, or a module with project parameters "
                        "(`project_parameters` in its module.yaml) that has no board device. Values are checked "
                        "against the parameters of that module; `verify: true` marks the values of an entry as not "
                        "confirmed. A system basis chip with partial networking (module sbcUja113x) takes "
                        "`can_wakeup_frame` in addition.",
        json_schema_extra={"unit": "unit of the module parameter"})
    # SYS-86
    hw_thresholds: dict[Name, HwThreshold] = Field(
        {}, description="Hardware thresholds: a board signal at a comparator input that forces a PWM group to its "
                        "inactive level without software (latched until reset); key: name of the threshold. Left "
                        "out: none.")
    # IO-62
    sample_groups: dict[Name, SampleGroup] = Field(
        {}, description="Sample groups: board ADC channels converted after every hardware trigger of a PWM group, "
                        "with one interrupt at the end of the group; key: name of the group. Each group needs an "
                        "interrupt rank (`interrupts.ranks`, source `sample_group`). Left out: none.")
    # PLAT-26
    bsw_tasks: dict[Name, BswTaskUse] = Field(
        {}, description="Stacks of the BSW tasks (ecu/asyncCoreConfiguration/tasks.yaml: background, bsp, can, "
                        "sysadmin, safety) and of the OpenBSW tasks idle and timer, or the task a BSW task runs in "
                        "instead of its own; key: task name. The application tasks take their stack from the ECU "
                        "description (rte.yaml tasks[].stack_bytes). Left out: the defaults of "
                        "ecu/configuration/taskStacks.yaml, every BSW task on its own.")
    # PLAT-27
    console: ConsoleUse | None = Field(
        None, description="Console and logger of the application: selectable, size of the logger ring buffer. Left "
                          "out: console and logger on, ring buffer 8192 bytes.")


# ----------------------------------------------------------------------------------------------- ECU description
# projects/<project>/rte.yaml, named by project.rte (ARCH-34). Part of the project level: which components the ECU
# runs, how their ports are connected, the application tasks and the CAN database. Structure and value ranges are
# checked here, with file, line and YAML path by tools/nxconfig. What needs the component ARXML, the DBC, the BSW
# task manifest (ecu/asyncCoreConfiguration/tasks.yaml) or the board signals of the project is checked by
# tools/rtegen (validate(), the same messages in neXtBSW Studio): unknown ports and runnables, types of a
# connection, periods and priorities against the BSW tasks, E2E rules of a profile, DBC signals.

TaskName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")]   # no underscore: task copies are Rte_Cpy_<task>_...
RESERVED_TASK_NAMES = ("idle", "timer")                          # OpenBSW idle and timer task
Ms = Annotated[int, Field(ge=0)]


class RunnableRef(Strict):
    """A runnable assigned to an application task, optionally with a start offset.

    In the list `tasks[].runnables` an entry may be written as plain text `Component.Runnable` (offset 0) or as a
    mapping `{runnable: Component.Runnable, offset_ms: 20}`. A mode switch runnable (RTE-08) is listed the same way
    and never has an offset.
    """

    runnable: str = Field(min_length=1, description="Runnable as `Component.Runnable` (name of the component in "
                                                    "`components[]`, runnable of its ARXML): a periodic runnable "
                                                    "(TIMING-EVENT) or a mode switch runnable (SWC-MODE-SWITCH-EVENT "
                                                    "on entry, exit or transition of an ECU mode; the event is part "
                                                    "of the component, the task is chosen here).")
    offset_ms: Ms = Field(0, description="Start offset inside the task: the runnable runs in the task cycles "
                                         "`offset_ms`, `offset_ms + period`, ... A multiple of the task period and "
                                         "below the period of the runnable. Not for a mode switch runnable, which "
                                         "runs once at the start of the first task cycle after the mode switch.")
    # RTE-23
    enabled: bool = Field(True, description="`false`: the runnable is disabled - it keeps its place in this list but "
                                            "is not called. Required ports of other components that it feeds read "
                                            "a constant value instead: the init value of the provided port, else "
                                            "`components[].disabled_values`, else the init value of the "
                                            "reading port; a qualifier port reads "
                                            "QLF_NOT_AVAILABLE. Not allowed for a runnable of a safety-relevant "
                                            "component (swc.yaml safety other than qm).")

    @model_validator(mode="before")
    @classmethod
    def _plain_text(cls, value):
        return {"runnable": value} if isinstance(value, str) else value


# RTE-02
class AppTask(Strict):
    """One application task of the ECU: FreeRTOS task running the runnables assigned to it.

    The BSW tasks (bsp, can, ...) are not listed here; they come from `ecu/asyncCoreConfiguration/tasks.yaml`.
    """

    name: TaskName = Field(description="Task name: lower case letters and digits, starting with a letter (no "
                                       "underscore; `idle` and `timer` are reserved). Must not be the name of a "
                                       "BSW task.")
    period_ms: Annotated[int, Field(gt=0)] = Field(description="Cycle of the task, a multiple of the OS tick of the "
                                                               "BSW task manifest. Every runnable of the task has "
                                                               "a period that is a multiple of it.")
    priority: Annotated[int, Field(gt=0)] = Field(description="Ordering key between the BSW tasks of the manifest "
                                                              "(e.g. between bsp 20 and can 40); unique over all "
                                                              "tasks. The FreeRTOS priority follows from the order.",
                                                  json_schema_extra=NO_UNIT)
    stack_bytes: Annotated[int, Field(ge=0)] = Field(description="Stack of the task; at least `min_stack_bytes` "
                                                                 "of the BSW task manifest.")
    runnables: list[RunnableRef] | None = Field(None, description="Runnables in call order. Left out in every "
                                                                  "task and only one task: all periodic and mode "
                                                                  "switch runnables run in it. Otherwise every "
                                                                  "periodic and every mode switch runnable has to "
                                                                  "be listed in exactly one task. Mode switch "
                                                                  "runnables run at the start of a cycle, before "
                                                                  "the periodic ones: first every on-exit, then "
                                                                  "every on-transition, then every on-entry "
                                                                  "runnable, each group in the order of this list.")

    @field_validator("name")
    @classmethod
    def _reserved(cls, value):
        if value in RESERVED_TASK_NAMES:
            raise ValueError(f"task name {value} is reserved (OpenBSW idle and timer task)")
        return value


# RTE-23: value of a provided port while its writer is disabled - a scalar, an enumeration literal, or a list
# (array elements, record fields in declaration order; nested for arrays of records). Checked against the port type
# by tools/rtegen, which knows the type.
DisabledValue = bool | int | float | str | list


class ComponentRef(Strict):
    """A software component of the ECU: its name and the folder with `generated/arxml` and `<Name>_data.m`."""

    name: Name = Field(description="Component name as in its ARXML (Simulink model name, e.g. DemoSwc).")
    path: str = Field(min_length=1, description="Component folder relative to the repository root (tools/rtegen "
                                                "--repo), e.g. swc/DemoSwc. The RTE reads "
                                                "`<path>/generated/arxml/*.arxml` and the calibration values of "
                                                "`<path>/<name>_data.m`.")
    # RTE-23 (user test 2026-09-26): switch a component off without taking it out of the ECU description
    enabled: bool = Field(True, description="`false`: the component is disabled - its init and its runnables are "
                                            "not called (it stays compiled; the linker drops the code nobody "
                                            "calls). Required ports of other components it feeds read a constant "
                                            "value instead: the init value of its provided port, else "
                                            "`disabled_values`, else the init value of the reading port; a "
                                            "qualifier port reads QLF_NOT_AVAILABLE. Not "
                                            "allowed for a safety-relevant component (swc.yaml safety other than "
                                            "qm).")
    disabled_values: dict[str, DisabledValue] = Field(
        {}, description="Value a provided port (key `Port.Element`) delivers while the part that writes it is "
                        "disabled (`enabled: false` here or at the runnable). Required - and only allowed - for a "
                        "port without an init value of its own: a C component (swc.yaml has no init values) or an "
                        "ARXML port without INIT-VALUE (unless every reading port has an init value of its own, or "
                        "is a qualifier port). A number (physical value of a fixed-point type), `true`/"
                        "`false`, an enumeration literal, or a list for an array or a record. Kept while the part "
                        "is enabled, so switching it on and off again needs no new entry.",
        json_schema_extra=NO_UNIT)


# COM-10
class E2eUse(Strict):
    """AUTOSAR E2E protection of one CAN message: profile, data id and the DBC signals of CRC and counter.

    Receiving, the RTE checks it and reports errors in the signal qualifier; sending, it writes counter and CRC.
    Which keys a profile takes is checked by tools/rtegen: P01/P11 `data_id` (16 bit, NIBBLE: 12 bit) and
    `data_id_mode`, P02 `data_id_list` (16 values of 8 bit).
    """

    profile: Literal["P01", "P02", "P11"] = Field(description="E2E profile of libs/bsw/e2e.")
    data_id: Annotated[int, Field(ge=0)] | None = Field(None, description="Data id of the message (P01, P11). "
                                                                          "Left out: 0.",
                                                        json_schema_extra=NO_UNIT)
    data_id_list: list[Annotated[int, Field(ge=0)]] | None = Field(
        None, description="P02 only: one 8-bit data id per counter value 0..15 (exactly 16 values).",
        json_schema_extra=NO_UNIT)
    data_id_mode: Literal["BOTH", "ALT", "LOW", "NIBBLE"] | None = Field(
        None, description="How the data id enters the CRC (P01: BOTH, ALT, LOW, NIBBLE; P11: BOTH, NIBBLE; not for "
                          "P02). Case does not matter. Left out: BOTH.")
    data_id_nibble: str | None = Field(None, description="Mode NIBBLE only: DBC signal that carries the high "
                                                         "nibble of the data id.")
    crc: str = Field(min_length=1, description="DBC signal of the CRC.")
    counter: str = Field(min_length=1, description="DBC signal of the sequence counter.")
    max_delta_counter: Annotated[int, Field(gt=0)] = Field(1, description="Largest accepted counter jump "
                                                                          "(MaxDeltaCounter), below the counter "
                                                                          "range of the profile.",
                                                           json_schema_extra={"unit": "counter steps"})

    @field_validator("data_id_mode", mode="before")
    @classmethod
    def _upper(cls, value):
        return value.upper() if isinstance(value, str) else value


# COM-03
class QualifierUse(Strict):
    """Supervision of one CAN message: timeout, debouncing and error values of the signal qualifier.

    A received message may use every key, a sent message only `e2e`. A received message without entry keeps the
    defaults (DBC timeout, no debouncing, values hold).
    """

    timeout_ms: Ms | None = Field(None, description="Received message: time without a message after which the "
                                                    "qualifier reports ERROR_TIMEOUT; 0 switches the supervision "
                                                    "off. Left out: GenMsgTimeoutTime of the DBC.")
    debounce_ms: Ms = Field(0, description="Received message: an E2E or timeout error must last this long before "
                                           "the qualifier reports it (QLF_DEBOUNCE until then).")
    on_error: Literal["hold", "init"] = Field("hold", description="Received message: signal values while the "
                                                                  "qualifier reports an error - hold keeps the last "
                                                                  "valid values, init falls back to "
                                                                  "GenSigStartValue.")
    e2e: E2eUse | None = Field(None, description="E2E protection of the message; left out: none.")


class CanSignals(Strict):
    """CAN signals of the ECU (section `can:`): the DBC, the node of this ECU in it and the bus they run on."""

    dbc: str = Field(min_length=1, description="DBC file relative to the repository root.")
    node: str = Field(min_length=1, description="Node of this ECU in the DBC: its messages are sent, the messages "
                                                "it receives are received.")
    bus: Name | None = Field(None, description="CAN bus of the project (board `can[].name`, used in "
                                               "`use.can`) the signals run on. Left out: the first CAN bus of the "
                                               "project.")
    monitor: list[str] = Field([], description="DBC messages whose signals the RTE receives for the console and "
                                               "XCP without a connected port.")
    task: str | None = Field(None, description="Application task that sends and receives the messages. Left out: "
                                               "the only application task (required with several).")
    qualifiers: dict[str, QualifierUse | None] = Field({}, description="Supervision per DBC message (key: message "
                                                                       "name).")


class UdsUse(Strict):
    """CAN bus of the application diagnostic stack (ecu/uds). Left out: the bus of the CAN signals."""

    bus: Name = Field(description="CAN bus of the project (board `can[].name`, used in `use.can`) that DoCAN runs "
                                  "on. The diagnostic addresses are `project.yaml diagnostics`.")


Target = Annotated[str, Field(min_length=1)]
# One target or a list of targets. The discriminator validates only the branch the YAML shape selects, so a finding
# is reported once (a plain union reports every branch); the tags are the union tags the loaders leave out of the
# YAML path (config._UNION_TAGS, or "[" in the tag).
Targets = Annotated[Annotated[Target, Tag("str")] | Annotated[list[Target], Field(min_length=1), Tag("list[str]")],
                    Discriminator(lambda value: "list[str]" if isinstance(value, list) else "str")]


class EcuDescription(Strict):
    """ECU description of a project (projects/<project>/rte.yaml, named by project.rte).

    Software components, the connections of their ports, application tasks and the CAN signals of one ECU; read by
    tools/rtegen. Values of the ECU state management and XCP are project values (project.yaml) and do not stand here.

    Format 2 (RTE-22, customer-project.md gap N3): `components[].path` and `can.dbc` are relative to *this file*,
    like `project.board` is relative to `project.yaml`. Before that they were relative to the root of the
    repository, which only exists in the neXtBSW tree itself - a customer workspace has no such root. A file
    without the key is format 1 and is raised by `nxconfig migrate` (migrate.STEPS, step `rte:2`).
    """

    schema_version: Literal[2] = Field(description="Version of the file format; currently 2.")
    tasks: Annotated[list[AppTask], Field(min_length=1)] | None = Field(
        None, description="Application tasks. Left out: one task with the default name, priority and stack of the "
                          "BSW task manifest and the period `base_period_ms`.")
    base_period_ms: Annotated[int, Field(gt=0)] | None = Field(
        None, description="Only without `tasks`: period of the one application task. Left out: the greatest common "
                          "divisor of the runnable periods.")
    # TOOL-59 (decision of the user 2026-09-22): an empty project starts without components; the key stays required
    # so an ECU description names its components on purpose
    components: list[ComponentRef] = Field(
        description="Software components of the ECU; each name once. `[]`: none yet (empty project, only the BSW "
                    "runs).")
    can: CanSignals | None = Field(None, description="CAN signals from a DBC; left out: no CAN signals.")
    uds: UdsUse | None = Field(None, description="Bus of the application diagnostic stack.")
    connections: dict[str, Targets] = Field(
        {}, description="Port connections, key `Component.Port.Element`. A required port has exactly one source: "
                        "a provided port `Component.Port.Element`, a BSW signal (`dio.in.<INPUT>`, "
                        "`adc.mv.<CHANNEL>`, `adc.raw.<CHANNEL>`, `adc.phys.<CHANNEL>`, `adc.qlf.<CHANNEL>`, "
                        "a value of a module instance `<prefix>.<INSTANCE>.<value>` as its module.yaml `rte` "
                        "describes it (e.g. `capture.<CHANNEL>.<value>`, `hall.<DEVICE>.<value>`, "
                        "`gd.<DEVICE>.<value>`), `ecu.state`, "
                        "`ecu.wakeup[.<source>]`, a BSW service port `bsw.power.<value>`, `bsw.reset.<value>`, "
                        "`bsw.time.<value>`), a received CAN signal `can.rx.<Message>.<Signal>` or qualifier "
                        "`can.qlf.<Message>`, a non-volatile block `nvm.<Block>` (reads the stored value, same type "
                        "as its writer) or its quality `nvm.qlf.<Block>` (NVM-18), or `rte.init` (keeps its init "
                        "value). A provided port feeds one target or a list: `dio.out.<OUTPUT>`, `pwm.<PAIR>`, "
                        "`can.tx.<Message>.<Signal>`, `ecu.sleep_request`, a non-volatile block `nvm.<Block>` "
                        "(exactly one writer per block, its init value is the default; definition, place and write "
                        "policy in project.yaml `nvm.blocks`), or `rte` (value stays in the RTE). A mode port "
                        "(required port with a mode switch interface, key `Component.Port.ModeGroup`) is connected "
                        "to the ECU mode manager `ecu.mode`: its mode declaration group names exactly the ECU "
                        "states STARTUP, RUN, "
                        "STANDBY, RUNDOWN, SHUTDOWN and SLEEP, and the component reads the mode with "
                        "`Rte_Mode_<Port>_<ModeGroup>()` (RTE-08).")
    # SYS-08
    ecu_state: None = Field(None, description="Not allowed: the parameters of the ECU state management are project "
                                              "values in `project.yaml modules.ecuState`.")

    @field_validator("base_period_ms")
    @classmethod
    def _not_with_tasks(cls, value, info):
        if value is not None and info.data.get("tasks") is not None:
            raise ValueError("not allowed together with tasks:, the period is tasks[].period_ms")
        return value

    @field_validator("components")
    @classmethod
    def _unique_components(cls, value):
        seen = set()
        for entry in value:
            if entry.name in seen:
                raise ValueError(f"component {entry.name} configured twice")
            seen.add(entry.name)
        return value

    @field_validator("ecu_state", mode="before")
    @classmethod
    def _moved(cls, value):
        raise ValueError("the parameters of the ECU state management are project values, move them to "
                         "project.yaml modules.ecuState (checked by tools/nxconfig)")


# ------------------------------------------------------------------------------------- diagnostic description
# projects/<project>/diag.yaml, named by project.diag (ARCH-51). Part of the project level like the ECU
# description: which data identifiers, routines, sessions, security levels and diagnostic trouble codes one ECU
# software serves. Structure and value ranges are checked here, relations between the entries (unique and
# non-reserved identifiers, lengths, references into rte.yaml and into this file itself) in diagdesc.py - every
# finding with file, line and YAML path.
#
# The file describes; it does not implement. The UDS jobs and the fault memory are generated from it by COM-28
# and NVM-03, and TOOL-43 exports it as ODX/PDX. Nothing here is a board or a derivative fact, so the description
# stays usable on any board and any derivative.
#
# ISO 14229-1 (2026) is the source of the number ranges only; the ranges below are stated as numbers with our own
# wording, never as text of the standard:
#   Annex C.1  data identifier ranges and the standardized identifiers F180..F19F
#   Annex D.1  DTC number ranges and the group identifiers
#   Annex D.2  status bits and the terms of the fault memory (operation cycle, enable conditions, aging)
#   Annex D.3  severity bits (7..5) and the OBD class bits (4..0)
#   Annex D.4  DTCFormatIdentifier; this description is format 0x01 (the ISO 14229-1 DTC format)
#   Annex D.8  extended data record numbers
#   Annex F.1  routine identifier ranges
#   Clause 9.2 session types, Clause 9.4 security access levels

# ODX short names are NMTOKEN-like and start with a letter, so the export (TOOL-43) can use a name unchanged.
OdxName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
# "Component.Port.Element" of the ECU description, the notation of rte.yaml connections
PortRef = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){2}$")]
# A BSW value in the notation of rte.yaml connections, e.g. `ecu.state` or `adc.mv.VBAT`
BswRef = Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+)+$")]
# "Component.Runnable" of the ECU description
RunnableRef = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")]

LABEL = ("Readable name for the tester and for the ODX export (TOOL-43: LONG-NAME). Left out: the export uses "
         "`name`.")
DESCRIPTION = "Sentence for the reader and for the ODX export (TOOL-43: DESC)."
GROUP = ("Free group name the tester can sort jobs by (TOOL-43: FUNCT-CLASS), e.g. `identification` or "
         "`actuator_test`. Left out: no group.")

# Size in bytes of a data type with a fixed width; `ascii` and `bytes` take their width from `length_bytes`.
DIAG_TYPE_BYTES = {"uint8": 1, "uint16": 2, "uint32": 4, "int8": 1, "int16": 2, "int32": 4, "float32": 4}
DIAG_TYPES = tuple(DIAG_TYPE_BYTES) + ("ascii", "bytes")
VARIABLE_WIDTH_TYPES = ("ascii", "bytes")
# COM-33: an `ascii`/`bytes` element always stands on the wire with its full `length_bytes`; a shorter value (a
# version text of the stack, a text constant) is filled up with this byte. A fixed width per identifier is what
# lets a tester split the answer to a ReadDataByIdentifier request with several identifiers (the records stand
# back to back without a length, ISO 14229-1), and NUL is what a C string and tools/canflash already strip.
TEXT_FILL_BYTE = 0x00
INTEGER_TYPES = ("uint8", "uint16", "uint32", "int8", "int16", "int32")


class DiagTextValue(Strict):
    """One line of a text table: a coded value and what the tester shows for it.

    A list instead of a mapping, because the export writes one entry per line (TOOL-43: COMPU-SCALE with its
    own text) and because the YAML loader of tools/nxconfig reads every mapping key as a string.
    """

    value: int = Field(description="Coded value as it stands on the wire.", json_schema_extra=NO_UNIT)
    text: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        description="What the tester shows for this value.")


class DiagParameter(Strict):
    """One value on the wire: a parameter of a routine request or response.

    Everything an ODX export needs to describe the value on the wire and in physical units is here: the coded
    type (`type`, `length_bytes`, `byte_order`), the conversion to the physical value (`factor`, `offset`,
    `unit`, or `values` for a text table) and its range (`min`, `max`). The byte position follows from the order
    of the list, the total length from the parameters - neither is configured.

    The data of an identifier uses `DiagDataElement`, which adds where the value comes from.
    """

    name: OdxName = Field(description="Name of the element, unique within its list; the export uses it as the "
                                      "short name of the parameter.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    type: Literal[DIAG_TYPES] = Field(
        description="Type on the wire: an integer (`uint8`, `uint16`, `uint32`, `int8`, `int16`, `int32`), "
                    "`float32`, a text (`ascii`) or raw bytes (`bytes`). `ascii` and `bytes` need "
                    "`length_bytes`, the others take their width from the type.")
    length_bytes: PositiveInt | None = Field(
        None, description="Only for `ascii` and `bytes`: width of the element on the wire - always exactly this "
                          "many bytes: a shorter text is filled up with NUL (0x00), a longer one is cut, so that "
                          "a tester can split a response by the description alone (e.g. several identifiers in "
                          "one 22 response, COM-33). "
                          "For every other type the width follows from `type` and stating it is an error.",
        json_schema_extra=BYTES)
    byte_order: Literal["big", "little"] = Field(
        "big", description="Byte order of an element wider than one byte. `big` is the order UDS uses for its "
                           "own parameters and the default here; `little` only where the value is passed on "
                           "unchanged from a little endian source.")
    unit: str | None = Field(
        None, description="Physical unit of the value as it is shown (e.g. `mV`, `degC`, `1/min`); the export "
                          "uses it as the display name of the unit. Left out: the value has no unit (a count, an "
                          "index, a text, raw bytes).",
        json_schema_extra={"unit": "unit of the described value"})
    factor: Annotated[float, Field(allow_inf_nan=False)] = Field(
        1.0, description="Physical value = `factor` * coded value + `offset` (the linear conversion of the "
                         "export). Only for numeric types.", json_schema_extra=NO_UNIT)
    offset: Annotated[float, Field(allow_inf_nan=False)] = Field(
        0.0, description="Added after `factor`; see there. Only for numeric types.", json_schema_extra=NO_UNIT)
    min: Annotated[float, Field(allow_inf_nan=False)] | None = Field(
        None, description="Smallest physical value the element takes; the export writes it as the lower limit. "
                          "Left out: the range of the type.",
        json_schema_extra={"unit": "unit of the described value"})
    max: Annotated[float, Field(allow_inf_nan=False)] | None = Field(
        None, description="Largest physical value the element takes; see `min`.",
        json_schema_extra={"unit": "unit of the described value"})
    values: Annotated[list[DiagTextValue], Field(min_length=1)] | None = Field(
        None, description="Text table of an integer element: one entry per coded value (the export writes it as a "
                          "text table conversion). Only for integer types, and not together with `factor`, "
                          "`offset`, `min` or `max`; each coded value only once.")

    @model_validator(mode="after")
    def _consistent(self):
        variable = self.type in VARIABLE_WIDTH_TYPES
        if variable and self.length_bytes is None:
            raise ValueError(f"type {self.type} needs length_bytes (its width is not fixed by the type)")
        if not variable and self.length_bytes is not None:
            raise ValueError(f"type {self.type} has a fixed width of {DIAG_TYPE_BYTES[self.type]} bytes, "
                             "length_bytes must not be set")
        if self.values is not None:
            if self.type not in INTEGER_TYPES:
                raise ValueError(f"values is a text table of an integer element, not of {self.type}")
            named = [n for n, v in (("factor", self.factor), ("offset", self.offset)) if v != (1.0 if n == "factor"
                                                                                               else 0.0)]
            named += [n for n, v in (("min", self.min), ("max", self.max)) if v is not None]
            if named:
                raise ValueError(f"values is a text table and replaces the linear conversion, so {', '.join(named)} "
                                 "must not be set")
            coded = [v.value for v in self.values]
            twice = sorted({v for v in coded if coded.count(v) > 1})
            if twice:
                raise ValueError("every coded value stands once in a text table, twice: "
                                 + ", ".join(str(v) for v in twice))
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min {self.min} is larger than max {self.max}")
        return self

    @property
    def width_bytes(self) -> int:
        """Width of the element on the wire; derived, never configured."""
        return self.length_bytes if self.length_bytes is not None else DIAG_TYPE_BYTES[self.type]


class DiagSource(Strict):
    """Where the data of an identifier comes from: exactly one of the three."""

    port: PortRef | None = Field(
        None, description="Provided port element of the ECU description, `Component.Port.Element` "
                          "(`rte.yaml components`). The value is read through the RTE.")
    bsw: BswRef | None = Field(
        None, description="Value of the basic software in the notation of the `rte.yaml` connections, e.g. "
                          "`ecu.state` or `adc.mv.<CHANNEL>`. Which names exist is resolved by the RTE generator, "
                          "not here.")
    constant: int | str | None = Field(
        None, description="Fixed value that does not change at runtime (e.g. a hardware number or the number of "
                          "modules of 0xF180). A text for `ascii`, a number for a numeric element; it has to "
                          "fit the element (checked in diagdesc.py).",
        json_schema_extra={"unit": "unit of the element it fills"})

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("port", "bsw", "constant") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one source: port (RTE port), bsw (value of the basic software) or "
                             f"constant (fixed value){', given: ' + ', '.join(given) if given else ''}")
        return self


class DiagDataElement(DiagParameter):
    """One value inside the data of a data identifier: a parameter plus where its value comes from."""

    source: DiagSource = Field(description="Where this value comes from: an RTE port, a value of the basic "
                                           "software, or a constant. Every value of an identifier has its own "
                                           "source, so an identifier can mix a fixed byte with a live value "
                                           "(e.g. the number of modules of 0xF180 with the version text).")


class DataIdentifier(Strict):
    """One data identifier (DID) the ECU serves with 22 (read) and 2E (write).

    The identifier is checked against the ranges of ISO 14229-1 Annex C.1 and against the identifiers the
    bootloader already answers (diagdesc.py). The length of the response follows from `data`.
    """

    identifier: Annotated[int, Field(ge=0, le=0xFFFF)] = Field(
        description="16-bit data identifier. Must lie in a range Annex C.1 leaves to the manufacturer or the "
                    "system supplier, must not be one of the identifiers the bootloader answers, and must not "
                    "appear twice in this file.", json_schema_extra=NO_UNIT)
    name: OdxName = Field(description="Name of the identifier, unique in the file; snapshots refer to it by "
                                      "this name and the export uses it as the short name.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    group: Name | None = Field(None, description=GROUP)
    data: Annotated[list[DiagDataElement], Field(min_length=1)] = Field(
        description="The values of the identifier in the order they stand on the wire; the length of the "
                    "identifier is their total width.")
    read: bool = Field(True, description="`true`: the identifier answers ReadDataByIdentifier (22).")
    write: bool = Field(False, description="`true`: the identifier accepts WriteDataByIdentifier (2E). A "
                                           "constant source cannot be written.")
    read_sessions: list[Name] | None = Field(
        None, description="Sessions (`sessions[].name`) the identifier may be read in. Left out: every session "
                          "of the file.")
    write_sessions: list[Name] | None = Field(
        None, description="Sessions the identifier may be written in; see `read_sessions`. Only with `write`.")
    read_security: Name | None = Field(
        None, description="Security level (`security[].name`) that has to be unlocked for reading. Left out: "
                          "no security access needed.")
    write_security: Name | None = Field(
        None, description="Security level that has to be unlocked for writing; see `read_security`. Only with "
                          "`write`.")

    @model_validator(mode="after")
    def _write_keys(self):
        if not self.write:
            given = [n for n in ("write_sessions", "write_security") if getattr(self, n) is not None]
            if given:
                raise ValueError(f"{', '.join(given)} without write: true (the identifier is read only)")
            return self
        fixed = [e.name for e in self.data if e.source.constant is not None]
        if fixed:
            raise ValueError(f"a constant cannot be written, but {', '.join(fixed)} has one: remove "
                             "write: true, or give those values a port or a bsw source")
        return self

    @property
    def length_bytes(self) -> int:
        """Length of the identifier's data; derived from `data`, never configured. Every read answers exactly this
        many bytes (texts filled up with TEXT_FILL_BYTE, COM-33)."""
        return sum(e.width_bytes for e in self.data)


class DiagRoutineCall(Strict):
    """What one step of a routine runs: exactly one of the two."""

    runnable: RunnableRef | None = Field(
        None, description="Runnable of a component of the ECU description, `Component.Runnable`.")
    bsw: BswRef | None = Field(
        None, description="Service of the basic software in the notation of the `rte.yaml` connections, e.g. "
                          "`bsw.reset.request`. Which names exist is resolved by the RTE generator, not here.")

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("runnable", "bsw") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one call: runnable (of a component) or bsw (service of the basic "
                             f"software){', given: ' + ', '.join(given) if given else ''}")
        return self


class DiagRoutineStep(Strict):
    """One of the three steps of RoutineControl (31): start, stop or requestResults."""

    call: DiagRoutineCall = Field(description="What the step runs: a runnable of a component or a service of "
                                              "the basic software.")
    request: list[DiagParameter] = Field(
        [], description="Parameters the tester sends with this step, in wire order. Left out: none.")
    response: list[DiagParameter] = Field(
        [], description="Parameters the ECU answers with, in wire order. Left out: none.")


class DiagRoutine(Strict):
    """One routine the ECU serves with RoutineControl (31).

    The identifier is checked against the ranges of ISO 14229-1 Annex F.1 and against the routines of the
    bootloader (diagdesc.py).
    """

    identifier: Annotated[int, Field(ge=0, le=0xFFFF)] = Field(
        description="16-bit routine identifier. Must lie in a range Annex F.1 leaves to the manufacturer or the "
                    "system supplier and must not appear twice in this file.", json_schema_extra=NO_UNIT)
    name: OdxName = Field(description="Name of the routine, unique in the file.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    group: Name | None = Field(None, description=GROUP)
    start: DiagRoutineStep = Field(description="startRoutine (sub-function 01); every routine has one.")
    stop: DiagRoutineStep | None = Field(
        None, description="stopRoutine (sub-function 02). Left out: the routine cannot be stopped.")
    results: DiagRoutineStep | None = Field(
        None, description="requestRoutineResults (sub-function 03). Left out: the routine reports no result of "
                          "its own.")
    sessions: list[Name] | None = Field(
        None, description="Sessions (`sessions[].name`) the routine may run in. Left out: every session of the "
                          "file.")
    security: Name | None = Field(
        None, description="Security level (`security[].name`) that has to be unlocked. Left out: none.")


class DiagSession(Strict):
    """One diagnostic session of the ECU (DiagnosticSessionControl, 10; ISO 14229-1, clause 9.2)."""

    name: OdxName = Field(description="Name of the session, unique in the file; identifiers and routines refer "
                                      "to it by this name.")
    id: Annotated[int, Field(ge=0x01, le=0x7E)] = Field(
        description="Session type of the sub-function. 0x01 default, 0x02 programming, 0x03 extended, 0x04 "
                    "safety system; 0x40..0x5F are left to the manufacturer and 0x60..0x7E to the system "
                    "supplier. The ranges ISO 14229-1 reserves are an error.", json_schema_extra=NO_UNIT)
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    p2_ms: PositiveInt = Field(50, description="P2 of this session: how long the ECU may take to answer before "
                                               "it has to ask the tester to wait.")
    p2_star_ms: PositiveInt = Field(5000, description="P2* of this session: how long the tester waits after the "
                                                      "ECU asked it to wait.")

    @model_validator(mode="after")
    def _timing(self):
        if self.p2_star_ms < self.p2_ms:
            raise ValueError(f"p2_star_ms {self.p2_star_ms} is shorter than p2_ms {self.p2_ms}: the extended "
                             "timeout has to be at least the normal one")
        return self


class DiagSecurityLevel(Strict):
    """One security level of the application (SecurityAccess, 27; ISO 14229-1, clause 9.4).

    The security access of the *bootloader* is not described here: it is a project value
    (`project.yaml diagnostics.bootloader.security`, BL-03), because the tester and the provisioning tool have
    to agree on it before any application exists.
    """

    name: OdxName = Field(description="Name of the level, unique in the file.")
    level: Annotated[int, Field(ge=0x01, le=0x7D)] = Field(
        description="requestSeed sub-function of the level; it has to be odd, the matching sendKey is one "
                    "higher (ISO 14229-1, clause 9.4).", json_schema_extra=NO_UNIT)
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    # COM-30: later methods (e.g. one with a public key per level) join the Literal; an unknown one is a schema
    # error, never a silent fallback
    method: Literal["hmac-sha256"] = Field(
        "hmac-sha256", description="Method of the key calculation. `hmac-sha256`: the device key of the "
                                   "bootloader's security access (BL-03: HMAC-SHA256 of the project master key and "
                                   "the chip UID, provisioned into the key sector) answers the seed; key = the "
                                   "first `key_bytes` of HMAC-SHA256(device key, \"NXAK\" + level + seed). The key "
                                   "is bound to the level, so the key of one level unlocks no other level and not "
                                   "the bootloader. `seed_bytes` and `key_bytes` at most 32.")
    seed_bytes: Annotated[int, Field(ge=1, le=255)] = Field(4, description="Length of the seed the ECU sends.",
                                                            json_schema_extra=BYTES)
    key_bytes: Annotated[int, Field(ge=1, le=255)] = Field(4, description="Length of the key the tester answers "
                                                                          "with.", json_schema_extra=BYTES)
    attempts: Annotated[int, Field(ge=1, le=255)] = Field(
        3, description="Wrong keys in a row before the ECU refuses further attempts for `delay_ms`.",
        json_schema_extra=NO_UNIT)
    delay_ms: PositiveInt = Field(10000, description="How long the ECU refuses further attempts after "
                                                     "`attempts` wrong keys.")
    sessions: list[Name] | None = Field(
        None, description="Sessions the level can be unlocked in. Left out: every session of the file except "
                          "the default session.")

    @field_validator("level")
    @classmethod
    def _odd(cls, value):
        if value % 2 == 0:
            raise ValueError(f"level 0x{value:02X} is even; a level is named by its requestSeed sub-function, "
                             f"which is odd (the sendKey 0x{value:02X} belongs to level 0x{value - 1:02X})")
        return value


class DiagOperationCycle(Strict):
    """One operation cycle of the fault memory (ISO 14229-1, D.2.1): what starts and ends a round of testing.

    The status bits of a DTC that speak about "this operation cycle", the confirmation threshold and the aging
    counter all count in the cycle named here.
    """

    name: OdxName = Field(description="Name of the cycle, unique in the file; a DTC refers to it by this name.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    kind: Literal["power", "ignition", "operation", "other"] = Field(
        "power", description="What the cycle follows: `power` (the ECU is supplied), `ignition` (terminal 15), "
                             "`operation` (a manufacturer defined round of operation) or `other`. The fault "
                             "memory treats all of them alike; the name is for the reader and the export.")
    autostart: bool = Field(True, description="`true`: the cycle starts when the ECU starts and ends when it "
                                              "shuts down. `false`: the application starts and ends it.")


class DiagEnableCondition(Strict):
    """One condition that has to hold before a DTC may update its status (ISO 14229-1, D.2.1).

    While a condition of a DTC is not fulfilled its monitor result is discarded: the debounce counter keeps its
    value and no status bit changes.
    """

    name: OdxName = Field(description="Name of the condition, unique in the file; a DTC refers to it by this "
                                      "name.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    port: PortRef | None = Field(
        None, description="Provided port element of the ECU description that reports the condition as a boolean, "
                          "`Component.Port.Element`. Exactly one of `port` and `bsw`.")
    bsw: BswRef | None = Field(
        None, description="Value of the basic software that reports the condition, e.g. `ecu.state`. Exactly one "
                          "of `port` and `bsw`.")
    initial: bool = Field(False, description="State of the condition before the source has reported for the "
                                             "first time. `false`: no DTC waiting on it updates until then.")

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("port", "bsw") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one source of the condition: port or bsw"
                             f"{', given: ' + ', '.join(given) if given else ''}")
        return self


class DtcCounterDebounce(Strict):
    """Debouncing by counting test samples (AUTOSAR DEM counter debouncing).

    Every failed sample moves the counter up by `increment`, every passed sample down by `decrement`. At
    `failed_threshold` the DTC is failed, at `passed_threshold` it is passed. The counter is the fault detection
    counter ISO 14229-1 D.6 describes.
    """

    failed_threshold: Annotated[int, Field(ge=1, le=127)] = Field(
        127, description="Counter value at which the test counts as failed.", json_schema_extra=NO_UNIT)
    passed_threshold: Annotated[int, Field(ge=-128, le=-1)] = Field(
        -128, description="Counter value at which the test counts as passed.", json_schema_extra=NO_UNIT)
    increment: Annotated[int, Field(ge=1, le=255)] = Field(1, description="Step of one failed sample.",
                                                           json_schema_extra=NO_UNIT)
    decrement: Annotated[int, Field(ge=1, le=255)] = Field(1, description="Step of one passed sample.",
                                                           json_schema_extra=NO_UNIT)
    jump_up: Annotated[int, Field(ge=-128, le=127)] | None = Field(
        None, description="Value a counter below it jumps to on a failed sample, before the sample adds "
                          "`increment`. Left out: 0 - the first failed sample after passed ones moves the counter "
                          "over zero, so the DTC is prefailed at once (ISO 14229-1, D.6).", json_schema_extra=NO_UNIT)
    jump_down: Annotated[int, Field(ge=-128, le=127)] | None = Field(
        None, description="Value a counter above it jumps to on a passed sample, before the sample subtracts "
                          "`decrement`. Left out: 0 (the first passed sample after failed ones moves it below "
                          "zero).", json_schema_extra=NO_UNIT)


class DtcTimeDebounce(Strict):
    """Debouncing by time: the monitor has to report the same result for this long before it counts."""

    failed_ms: PositiveInt = Field(description="How long the monitor has to report failed without interruption "
                                               "before the test counts as failed.")
    passed_ms: PositiveInt = Field(description="How long the monitor has to report passed without interruption "
                                               "before the test counts as passed.")


class DtcDebounce(Strict):
    """How the raw result of a monitor becomes a matured test result: exactly one of the two ways."""

    counter: DtcCounterDebounce | None = Field(None, description="Debouncing by counting test samples.")
    time: DtcTimeDebounce | None = Field(None, description="Debouncing by time.")

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("counter", "time") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one way of debouncing: counter or time"
                             f"{', given: ' + ', '.join(given) if given else ''}")
        return self


DTC_EVENT_RESULTS = ("boolean", "qualifier", "status", "limit")


class DtcEvent(Strict):
    """The monitor whose result sets and clears one DTC: exactly one source.

    Every DTC has an event; a DTC nothing can set is a description of nothing and is reported as an error.
    """

    port: PortRef | None = Field(
        None, description="Provided port element of the ECU description that reports the test result, "
                          "`Component.Port.Element`.")
    bsw: BswRef | None = Field(
        None, description="Monitor of the basic software that reports the test result, e.g. "
                          "`can.busoff.<BUS>`. Which names exist is resolved by the RTE generator, not here.")
    description: str | None = Field(None, description="What the monitor tests, for the reader and the export.")
    result: Literal[DTC_EVENT_RESULTS] | None = Field(
        None, description="How the value of the source becomes a test result, sampled once per fault memory cycle "
                          "(`fault_memory.cycle_ms`): `boolean` (true: failed), `qualifier` (a signal qualifier of "
                          "Rte_Qlf.h: QLF_OK passed, QLF_DEBOUNCE no result, every error failed), `status` (the "
                          "AUTOSAR event status: 0 passed, 1 failed, 2 prepassed, 3 prefailed, other values no "
                          "result) or `limit` (`failed_above`/`failed_below`). Left out: derived by the RTE "
                          "generator - `limit` if a limit is given, `qualifier` for a qualifier source, `boolean` "
                          "for a boolean port. A source the BSW reports itself (a `bsw` name the RTE does not know) "
                          "reports AUTOSAR event states through dem::IFaultMemory::report().")
    failed_above: int | None = Field(
        None, description="`limit`: the sample is failed while the value is above this (in the unit of the "
                          "source), passed otherwise.", json_schema_extra=NO_UNIT)
    failed_below: int | None = Field(
        None, description="`limit`: the sample is failed while the value is below this.", json_schema_extra=NO_UNIT)

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("port", "bsw") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one source of the test result: port or bsw"
                             f"{', given: ' + ', '.join(given) if given else ''}")
        limits = [n for n in ("failed_above", "failed_below") if getattr(self, n) is not None]
        if limits and self.result not in (None, "limit"):
            raise ValueError(f"{' and '.join(limits)} belong to result limit, not {self.result}")
        if self.result == "limit" and not limits:
            raise ValueError("result limit needs failed_above, failed_below or both")
        if len(limits) == 2 and self.failed_below >= self.failed_above:
            raise ValueError(f"failed_below {self.failed_below} is not below failed_above {self.failed_above}: "
                             "no value would pass")
        return self


class DtcAging(Strict):
    """When a confirmed DTC is forgotten again (ISO 14229-1, D.2.1 and D.7)."""

    threshold_cycles: Annotated[int, Field(ge=1, le=255)] = Field(
        description="Operation cycles that have to end without the test failing before the confirmed bit is "
                    "cleared and the entry is released.", json_schema_extra=NO_UNIT)
    cycle: Name | None = Field(
        None, description="Operation cycle (`operation_cycles[].name`) the aging counts in. Left out: the cycle "
                          "of the DTC.")


class DtcSnapshot(Strict):
    """One snapshot record of a DTC: the data identifiers stored when the DTC is first confirmed."""

    record_number: Annotated[int, Field(ge=0x01, le=0xFE)] = Field(
        description="Number of the record, unique per DTC. 0x00 and 0xFF are reserved by ISO 14229-1 (0xFF "
                    "requests all records).", json_schema_extra=NO_UNIT)
    dids: Annotated[list[Name], Field(min_length=1)] = Field(
        description="Data identifiers (`data_identifiers[].name`) stored in the record, in this order; each "
                    "one once.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)


DTC_EXTENDED_COUNTERS = ("occurrence_counter", "aging_counter", "fault_detection_counter", "failed_cycles",
                         "operation_cycles_since_first_failed")


class DtcExtendedDataItem(Strict):
    """One value of an extended data record: a counter of the fault memory or a data identifier."""

    name: OdxName = Field(description="Name of the value inside the record, unique in the record.")
    counter: Literal[DTC_EXTENDED_COUNTERS] | None = Field(
        None, description="Counter of the fault memory: `occurrence_counter` (how often the DTC matured), "
                          "`aging_counter` (cycles counted towards aging), `fault_detection_counter` (the "
                          "debounce counter, D.6), `failed_cycles` (operation cycles with a failed test) or "
                          "`operation_cycles_since_first_failed`. Each is one byte; exactly one of `counter` "
                          "and `did`.")
    did: Name | None = Field(
        None, description="Data identifier (`data_identifiers[].name`) stored in the record. Exactly one of "
                          "`counter` and `did`.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [n for n in ("counter", "did") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("name exactly one content: counter (of the fault memory) or did"
                             f"{', given: ' + ', '.join(given) if given else ''}")
        return self


class DtcExtendedData(Strict):
    """One extended data record of a DTC (ISO 14229-1, D.8)."""

    record_number: Annotated[int, Field(ge=0x01, le=0x8F)] = Field(
        description="Number of the record, unique per DTC. Annex D.8 leaves 0x01..0x8F to the manufacturer; "
                    "0x00, 0x90..0xFD are reserved and 0xFE/0xFF request all records.",
        json_schema_extra=NO_UNIT)
    data: Annotated[list[DtcExtendedDataItem], Field(min_length=1)] = Field(
        description="The values of the record in the order they stand on the wire.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)


DTC_SEVERITIES = ("none", "maintenance_only", "check_at_next_halt", "check_immediately")


class Dtc(Strict):
    """One diagnostic trouble code of the fault memory.

    The number is a 3-byte DTC in the ISO 14229-1 format (Annex D.4, format identifier 0x01); the ranges Annex
    D.1 reserves for legislation and for the group identifiers are checked in diagdesc.py. Warning lamp and OBD
    are deliberately not described (decision of the user, 2026-09-21): the status byte has no
    warningIndicatorRequested source here and the OBD class bits of Annex D.3.3 stay at the unclassified value.
    """

    number: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = Field(
        None, description="3-byte DTC number. 0x000000..0x0000FF is reserved for legislation and 0xFFFF00..0xFFFFFF "
                          "are the group identifiers (Annex D.1); the number must not appear twice in this file. "
                          "Left out (TOOL-178): only for an event of the basic software that the neXtBSW numbering "
                          "plan knows (tools/nxconfig/bswdtc.py) - the number is `bsw_dtc_range.base` plus the "
                          "offset of the plan, so it follows the range of the project.",
        json_schema_extra=NO_UNIT)
    bsw_instance: Annotated[int, Field(ge=0, le=0xFF)] | None = Field(
        None, description="Low byte of a derived number for a BSW source with instances (TOOL-178): index of the "
                          "used CAN bus for `can.busoff.<BUS>`, cause for `safety.<cause>`, NvM block index for "
                          "`nvm.*.<BLOCK>`. Only with `number` left out and only for such a source.",
        json_schema_extra=NO_UNIT)
    name: OdxName = Field(description="Name of the DTC, unique in the file.")
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)
    group: Name | None = Field(None, description=GROUP)
    display_code: Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")] | None = Field(
        None, description="Code the tester shows instead of the number (e.g. `P061600`), for the ODX export "
                          "(TOOL-43: DISPLAY-TROUBLE-CODE). It is not derived: this description uses the ISO "
                          "14229-1 DTC format (Annex D.4, 0x01), which fixes no display text. Left out: the "
                          "export has none and the tester shows the number - except for a derived BSW number "
                          "(`number` left out), which gets its SAE J2012 display (0x160100: P160100).")
    severity: Literal[DTC_SEVERITIES] = Field(
        "none", description="Severity of the DTC (Annex D.3.2, bits 7..5 of the severity byte): `none`, "
                            "`maintenance_only`, `check_at_next_halt` or `check_immediately`.")
    event: DtcEvent = Field(description="The monitor whose result sets and clears the DTC.")
    debounce: DtcDebounce = Field(description="How the raw monitor result becomes a matured test result.")
    operation_cycle: Name = Field(description="Operation cycle (`operation_cycles[].name`) the status bits, the "
                                              "confirmation and the aging of this DTC count in.")
    enable_conditions: list[Name] = Field(
        [], description="Conditions (`enable_conditions[].name`) that all have to hold before the status of "
                        "this DTC is updated. Left out: none.")
    confirmation_threshold: Annotated[int, Field(ge=1, le=255)] = Field(
        1, description="Operation cycles with a failed test before the confirmed bit is set and the entry is "
                       "stored (Annex D.2.1). 1: the first failed test confirms.", json_schema_extra=NO_UNIT)
    aging: DtcAging | None = Field(None, description="When a confirmed DTC is forgotten again. Left out: it is "
                                                     "kept until the tester clears it.")
    priority: Annotated[int, Field(ge=1, le=255)] = Field(
        1, description="Which DTC wins when the fault memory is full: 1 is the most important entry, a larger "
                       "number is displaced first.", json_schema_extra=NO_UNIT)
    snapshots: list[DtcSnapshot] = Field([], description="Snapshot records of this DTC. Left out: none.")
    extended_data: list[DtcExtendedData] = Field(
        [], description="Extended data records of this DTC. Left out: none.")

    # TOOL-178: set by DiagDescription when `number` was left out and derived from the neXtBSW range
    _derived: bool = PrivateAttr(False)

    @property
    def number_derived(self) -> bool:
        """True when the number was left out in the file and derived from `bsw_dtc_range` (TOOL-178)."""
        return self._derived

    @model_validator(mode="after")
    def _instance_only_derived(self):
        if self.bsw_instance is not None and self.number is not None:
            raise ValueError("bsw_instance is the low byte of a derived number: leave it out with an explicit "
                             "number, or leave the number out")
        return self


class DtcGroup(Strict):
    """A group of DTCs a tester clears with ClearDiagnosticInformation (14) (NVM-09).

    `group` is the 3-byte groupOfDTC value the tester sends, `first`..`last` the DTC numbers it stands for. ISO
    14229-1, Annex D.1 leaves the groups to the vehicle manufacturer and reserves 0xFFFF00..0xFFFFFE for the
    functional groups (e.g. 0xFFFF33 emissions, 0xFFFFD0 safety); 0xFFFFFF (all DTCs) and a single DTC number need
    no entry. Checked in diagdesc.py, generated by tools/diaggen as the table `diagDtcGroups()` of ecu/uds.
    """

    name: OdxName = Field(description="Name of the group, unique in the file.")
    bsw: bool = Field(
        False, description="`true` (TOOL-178): the group of the whole neXtBSW range - `group`, `first` and `last` "
                           "are derived from `bsw_dtc_range` (base, base, base + size - 1) and are left out.")
    group: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = Field(
        None, description="groupOfDTC value of the group (Annex D.1): a group of the vehicle manufacturer or a "
                          "functional group 0xFFFF00..0xFFFFFE. Not 0xFFFFFF (all DTCs) and not the number of a "
                          "described DTC; unique in the file. Required unless `bsw` is true.",
        json_schema_extra=NO_UNIT)
    first: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = Field(
        None, description="Lowest DTC number of the group. Required unless `bsw` is true.",
        json_schema_extra=NO_UNIT)
    last: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = Field(
        None, description="Highest DTC number of the group (at least `first`, below the group identifiers "
                          "0xFFFF00). Required unless `bsw` is true.", json_schema_extra=NO_UNIT)
    label: str | None = Field(None, description=LABEL)
    description: str | None = Field(None, description=DESCRIPTION)

    @model_validator(mode="after")
    def _values_or_bsw(self):
        given = [n for n in ("group", "first", "last") if getattr(self, n) is not None]
        if self.bsw and given:
            raise ValueError(f"{', '.join(given)} of a group with bsw: true are derived from bsw_dtc_range: leave "
                             "them out")
        missing = [n for n in ("group", "first", "last") if getattr(self, n) is None]
        if not self.bsw and missing:
            raise ValueError(f"{', '.join(missing)} missing: a group names group, first and last, or is the group "
                             "of the neXtBSW range (bsw: true)")
        return self


class BswDtcRange(Strict):
    """The block of DTC numbers the basic software raises its own DTCs in (TOOL-131, configurable since TOOL-178).

    Numbering plan and default: tools/nxconfig/bswdtc.py. A DTC of a BSW event the plan knows leaves `number` out
    and gets `base` + the offset of its source, so moving the base moves every BSW DTC. Project DTCs are numbered
    outside the range (a project DTC inside it is reported as a warning).
    """

    base: Annotated[int, Field(ge=bswdtc.LOWEST_BASE, le=bswdtc.GROUP_IDENTIFIERS - bswdtc.BLOCK,
                               multiple_of=bswdtc.BLOCK)] = Field(
        bswdtc.BSW_DTC_BASE, description="First number of the range; it is also the group identifier of the "
                                         "whole range and never a DTC. A multiple of 0x100. Left out: 0x160000 "
                                         "(P16xx-yy, the default of neXtBSW).", json_schema_extra=NO_UNIT)
    size: Annotated[int, Field(ge=bswdtc.BLOCK, le=bswdtc.GROUP_IDENTIFIERS - bswdtc.LOWEST_BASE,
                               multiple_of=bswdtc.BLOCK)] = Field(
        bswdtc.BSW_DTC_SIZE, description="Numbers in the range, a multiple of 0x100 (one block per source of the "
                                         "plan). Below 0x900 the later sources of the plan have no number. Left "
                                         "out: 0x10000.", json_schema_extra=NO_UNIT)

    @model_validator(mode="after")
    def _valid(self):
        problem = bswdtc.range_problem(self.base, self.size)
        if problem:
            raise ValueError(problem)
        return self


class DiagFaultMemory(Strict):
    """Size and behaviour of the fault memory as a whole (simplified AUTOSAR DEM, user 2026-09-21)."""

    max_entries: Annotated[int, Field(ge=1, le=255)] = Field(
        description="DTCs the memory holds at the same time. Fewer than the described DTCs is allowed (not all "
                    "of them are stored at once) and is reported as a warning.", json_schema_extra=NO_UNIT)
    overflow: Literal["replace_oldest", "keep_oldest"] = Field(
        "replace_oldest", description="What happens when the memory is full and a further DTC matures: "
                                      "`replace_oldest` displaces the least important, oldest entry (see "
                                      "`priority`), `keep_oldest` discards the new one.")
    snapshot_records_max: Annotated[int, Field(ge=0, le=254)] = Field(
        1, description="Snapshot records stored per DTC. 0: no snapshots are stored.",
        json_schema_extra=NO_UNIT)
    extended_records_max: Annotated[int, Field(ge=0, le=143)] = Field(
        1, description="Extended data records stored per DTC. 0: none.", json_schema_extra=NO_UNIT)
    aging_cycle: Name | None = Field(
        None, description="Operation cycle the aging of a DTC counts in when the DTC names none. Left out: the "
                          "cycle of the DTC.")
    cycle_ms: Annotated[int, Field(ge=1, le=1000)] = Field(
        10, description="Cycle of the fault memory: every event is sampled once per cycle, so a counter debouncing "
                        "of n samples takes n cycles, and time debouncing counts in steps of this. Reported BSW "
                        "events are taken over once per cycle as well.")
    write_interval_ms: Annotated[int, Field(ge=0, le=3600000)] = Field(
        1000, description="Shortest distance between two writes of a changed fault memory block to the persistent "
                          "memory (NvM write policy CHANGE, wear of an EEPROM emulation); the run-down writes "
                          "what is left. 0: only at the run-down.")


class DiagDescription(Strict):
    """Diagnostic description of a project (`projects/<project>/diag.yaml`, named by `project.diag`).

    What one ECU software answers on the diagnostic connection and what its fault memory stores: sessions,
    security levels, data identifiers, routines, and the DTCs with their events, debouncing, enable conditions,
    operation cycle, aging, snapshots and extended data. The *addresses* of the connection (CAN ids, ISO-TP,
    bootloader security access) are project values and stand in `project.yaml diagnostics`.

    The file is a description only. The UDS jobs and the fault memory are generated from it (COM-28, NVM-03)
    and TOOL-43 exports it as ODX/PDX.
    """

    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    sessions: list[DiagSession] = Field(
        [], description="Diagnostic sessions of the ECU. Left out: the identifiers and routines name no session "
                        "and everything is served in every session the UDS stack knows.")
    security: list[DiagSecurityLevel] = Field(
        [], description="Security levels of the application. Left out: none (the security access of the "
                        "bootloader stands in `project.yaml`).")
    operation_cycles: list[DiagOperationCycle] = Field(
        [], description="Operation cycles of the fault memory; required as soon as a DTC is described.")
    enable_conditions: list[DiagEnableCondition] = Field(
        [], description="Conditions a DTC can wait on before it updates its status. Left out: none.")
    data_identifiers: list[DataIdentifier] = Field([], description="Data identifiers the ECU serves.")
    routines: list[DiagRoutine] = Field([], description="Routines the ECU serves.")
    fault_memory: DiagFaultMemory | None = Field(
        None, description="Size and behaviour of the fault memory; required as soon as a DTC is described.")
    # TOOL-178: before dtcs and dtc_groups, whose validators derive their numbers from it
    bsw_dtc_range: BswDtcRange = Field(
        default_factory=BswDtcRange,
        description="Block of DTC numbers of the basic software (neXtBSW range). Left out: 0x160000..0x16FFFF.")
    dtcs: list[Dtc] = Field([], description="Diagnostic trouble codes of the fault memory.")
    dtc_groups: list[DtcGroup] = Field(
        [], description="Groups of DTCs the tester can clear with 14 besides all DTCs (0xFFFFFF) and a single DTC "
                        "(ISO 14229-1, Annex D.1). Left out: none.")

    @field_validator("dtcs")
    @classmethod
    def _derive_bsw_numbers(cls, dtcs: list[Dtc], info: ValidationInfo) -> list[Dtc]:
        """TOOL-178: a DTC without number gets bsw_dtc_range.base + the offset of its BSW source; every finding
        lands at the DTC (dtcs[i].number or .bsw_instance)."""
        span = info.data.get("bsw_dtc_range")
        if span is None:
            return dtcs                             # the range itself is invalid and reported there
        errors = []
        for index, dtc in enumerate(dtcs):
            if dtc.number is not None:
                continue
            try:
                number = bswdtc.derive(dtc.event.bsw, span.base, span.size, dtc.bsw_instance)
            except bswdtc.DerivationError as e:
                key = "bsw_instance" if "bsw_instance" in str(e) else "number"
                errors.append(InitErrorDetails(type=PydanticCustomError("bsw_dtc", str(e)), loc=(index, key),
                                               input=dtc.name))
                continue
            dtc.number = number
            dtc._derived = True
            if dtc.display_code is None:
                dtc.display_code = bswdtc.display_code(number)
        if errors:
            raise ValidationError.from_exception_data("dtcs", errors)
        return dtcs

    @field_validator("dtc_groups")
    @classmethod
    def _derive_bsw_group(cls, groups: list[DtcGroup], info: ValidationInfo) -> list[DtcGroup]:
        span = info.data.get("bsw_dtc_range")
        for group in groups:
            if group.bsw and span is not None:
                group.group, group.first, group.last = span.base, span.base, span.base + span.size - 1
        return groups


# ----------------------------------------------------------------------------------------------- module manifest
# libs/bsw/<module>/module.yaml (doc/architecture/README.md, section 5)

ParameterName = Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")]
Value = int | bool | str
# reserved project key of every module entry (not a parameter of the module)
VERIFY_KEY = "verify"


class Lifecycle(Strict):
    level: Annotated[int, Field(ge=1, le=9)]
    task: Name


class ConnectSpec(Strict):
    """One connection of a module instance to a board entry (board.yaml devices[].connect)."""

    kind: Literal["digital_output", "digital_input", "analog_input"]   # dio.outputs, dio.inputs, adc
    description: str | None = None
    # IO-32: power output enable - the fault handlers switch it to its inactive level as their very first action,
    # before dump and UART (generated table bsp/safeOutputs/safeOutputsConfiguration.h). digital_output only.
    safe_off: bool = False
    # ARCH-52: the board may leave the connection unwired (e.g. an optional RESET or FAULT pin of a part). The
    # generated binding tells the driver per instance whether it is wired (NX_DEVICE_<device>_IO_<conn>_WIRED).
    optional: bool = Field(
        default=False, description="The board may leave this connection out (devices[].connect); the driver is "
                                   "told per instance whether it is wired. Not with safe_off: an output the fault "
                                   "handlers switch off must be wired.")

    @model_validator(mode="after")
    def _safe_off_output(self):
        if self.safe_off and self.kind != "digital_output":
            raise ValueError(f"safe_off needs kind digital_output (a {self.kind} cannot be switched off)")
        if self.safe_off and self.optional:
            raise ValueError("a safe_off connection cannot be optional: the fault handlers and the safe state switch "
                             "it off, so every instance must have it wired")
        return self


# IO-35: power stage facts a gate driver parameter can set -> unit the parameter must have. vds_threshold_mv applies
# to the switches of both sides, the others to one side.
POWER_STAGE_PARAMETERS = ("vds_threshold_mv", "vds_threshold_high_side_mv", "vds_threshold_low_side_mv",
                          "blanking_ns")
POWER_STAGE_UNITS = {"vds_threshold_mv": "mV", "vds_threshold_high_side_mv": "mV", "vds_threshold_low_side_mv": "mV",
                     "blanking_ns": "ns"}


class ParameterSpec(Strict):
    """One project parameter of a module instance (project.yaml modules.<device>.<parameter>)."""

    type: Literal["int", "bool", "str"]
    min: int | None = None                          # int: required (generated as 32-bit value)
    max: int | None = None
    pattern: str | None = None                      # str: regular expression the whole value must match
    # str: the value is a list of names - "all", "none" or comma separated names, none twice (checks.py).
    # can_bus: every name is a CAN bus of use.can; any: every name the pattern allows.
    names: Literal["can_bus", "any"] | None = None
    default: Value | None = None                    # None: the project must set the parameter
    unit: str | None = None
    description: str | None = None
    # IO-35: int: the only values the parameter takes (settings with a few steps, e.g. filter times); in min..max
    values: list[int] | None = None
    # IO-35: gate driver modules (provides gate_driver): the parameter sets this fact of the power stage the device
    # drives (board pwm[].gate_driver); nxconfig derives the short-circuit current from it with R_DS(on) of the
    # board (powerstage.py). A value <= 0 means the device keeps its own setting: nothing is derived.
    power_stage: Literal[POWER_STAGE_PARAMETERS] | None = None
    # IO-21: int with unit: the parameter is a level of the physical value (adc.phys) of this analog_input connection
    # of the instance, e.g. a load current in mA the driver compares with the converted sense channel. nxconfig checks
    # that the wired board channel delivers this unit (its adc[].sensor) and that the level lies in the range the
    # channel can report over every converter code (sensed.py); the part facts stay in the board.
    sense: Name | None = None


# IO-33: use of a device pin that makes it a local wake-up input of the power supply (PinSpec.local_wakeup)
WAKE_INPUT_USE = "wake_input"


class PinSpec(Strict):
    """IO-33: a pin of the part itself (not an MCU connection) whose wiring is a board fact
    (board.yaml devices[].pins.<pin>), e.g. an HVIO or the EN output of a system basis chip."""

    uses: list[Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")]] = Field(min_length=1)
    # fnmatch patterns on devices[].part: the pin exists only on these variants; empty: on every part of the module
    parts: list[Annotated[str, Field(min_length=1)]] = []
    # the pin used as wake_input is the local wake-up input LOCAL<n> of the power supply (ecuState wakeup_local)
    local_wakeup: Annotated[int, Field(ge=0, le=7)] | None = None
    description: str | None = None


# RTE-18: values a module offers the application through the RTE (module.yaml rte). tools/rtegen forms the
# connection targets <prefix>.<Instance>.<value> from it, so a new driver becomes connectable without a change of
# the generator. Platform types a value may be read into (tools/rtegen PLATFORM_TYPES).
RTE_VALUE_TYPES = ("boolean", "uint8", "sint8", "uint16", "sint16", "uint32", "sint32", "float32", "float64")
# where the instances come from: `devices` - the board devices of the module the project integrates;
# `input_capture` - the board input_capture entries the project uses (use.input_capture), for an mcu_driver
RTE_INSTANCE_SOURCES = ("devices", "input_capture")
# how the generated RTE reads one instance: `reading` is the generic contract of every new module (a reading
# struct filled by a function per instance, see RteSpec); the others are the access code tools/rtegen has for the
# modules that existed before RTE-18 (one update per cycle of the capture unit, the pin interrupt snapshot of the
# Hall sensors, the gate driver reader with its health code and the bus verdict of SYS-29) and name that code
RTE_ACCESS = ("reading", "input_capture", "hall", "gate_driver")
# first parts of targets tools/rtegen already gives a meaning; a module prefix must not take one of them
# (tools/rtegen/test_rtegen_device_values.py compares the list with the generator)
RTE_RESERVED_PREFIXES = ("dio", "adc", "pwm", "can", "ecu", "bsw", "nvm", "did", "routine", "dem", "age", "rte",
                         "pinevent", "swc", "safety")  # SYS-117: safety.degraded
CIdentifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
CQualifiedName = Annotated[str, Field(min_length=1, pattern=r"^(::)?[A-Za-z_][A-Za-z0-9_]*(::[A-Za-z_][A-Za-z0-9_]*)*$")]


class RteValueSpec(Strict):
    """RTE-18: one value of an instance, target <prefix>.<Instance>.<value> (module.yaml rte.values.<value>)."""

    types: list[Literal[RTE_VALUE_TYPES]] = Field(
        min_length=1, description="Platform types a required port may have to read the value (the RTE casts).")
    unit: Annotated[str, Field(min_length=1)] = Field(
        description="Unit or meaning of the number, shown in the Studio and in messages (e.g. `0.1 degC`, "
                    "`QLF_* of Rte_Qlf.h`).")
    description: str | None = None
    field: CIdentifier | None = Field(
        default=None, description="Member of the reading that holds the value. Left out: the value name.")
    qualifier: bool = Field(
        default=False,
        description="The value is the quality of the other values of the instance, with the qualifier values of "
                    "COM-03 (QLF_* of Rte_Qlf.h). tools/rtegen warns when values of an instance are connected "
                    "without it. At most one per module.")
    without: str | None = Field(
        default=None,
        description="Qualifier only: what a component cannot tell apart when the values are connected without it, "
                    "for the warning (`a measured value from a held one (standstill)`). Left out: a current value "
                    "from a held one.")


class RteSpec(Strict):
    """RTE-18: readable values of a module for the application (module.yaml rte), inputs only.

    Generic contract (`access: reading`): the header `include` declares the struct `reading`; the integration of the
    module defines per instance `void ::integration::rte::<read>_<Instance>(<reading>& reading)` (callable from any
    task, it takes a consistent snapshot itself) - the generated RTE declares and calls it once per task and cycle
    that reads the instance and copies the connected members.
    """

    prefix: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")] | None = Field(
        default=None, description="First part of the targets `<prefix>.<Instance>.<value>`. Left out: the module "
                                  "name in lower case. Unique over all modules.")
    instances: Literal[RTE_INSTANCE_SOURCES] = Field(
        default="devices", description="`devices`: the board devices of this module the project integrates; "
                                       "`input_capture`: the board input_capture entries the project uses.")
    access: Literal[RTE_ACCESS] = Field(
        default="reading", description="How the RTE reads an instance: `reading` (generic contract, needs "
                                       "`include`, `reading` and `read`) or the access code of an existing module.")
    include: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_./-]+\.h(pp)?$")] | None = Field(
        default=None, description="`reading` only: header (include path of the module's CMake target) that "
                                  "declares the reading struct.")
    reading: CQualifiedName | None = Field(
        default=None, description="`reading` only: C++ type of one reading, a struct with a member per value.")
    read: CIdentifier | None = Field(
        default=None, description="`reading` only: name of the reader function in ::integration::rte, called as "
                                  "`<read>_<Instance>(reading)`.")
    instance: CIdentifier = Field(default="Instance", description="Word for the instance in messages "
                                                                   "(`<prefix>.<Device>.<value>`).")
    noun: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, description="What one instance is, for messages (`gate driver`). Left out: "
                                  "`<module> instance`.")
    which: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, description="Which instances the project has, for messages. Left out: derived from "
                                  "`instances`.")
    input_note: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, description="Why a provided port cannot use a value, for messages. Left out: a measured "
                                  "value is an input.")
    value_note: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, description="Appended to the message on an unknown value (e.g. that no value switches the "
                                  "device on).")
    values: dict[ParameterName, RteValueSpec] = Field(
        min_length=1, description="The readable values by name (the last part of the target).")

    @model_validator(mode="after")
    def _access_fields(self):
        generic = (self.include, self.reading, self.read)
        if self.access == "reading" and None in generic:
            raise ValueError("access reading needs include, reading and read (the reader contract of the module)")
        if self.access != "reading" and generic != (None, None, None):
            raise ValueError(f"include, reading and read belong to access reading; access {self.access} is code "
                             f"of tools/rtegen")
        qualifiers = [name for name, value in self.values.items() if value.qualifier]
        if len(qualifiers) > 1:
            raise ValueError(f"at most one qualifier value per module ({', '.join(qualifiers)})")
        for name, value in self.values.items():
            if value.qualifier and not set(value.types) <= {"uint8", "uint16", "uint32"}:
                raise ValueError(f"qualifier value {name} takes uint8, uint16 or uint32 only (QLF_* values)")
            if value.without is not None and not value.qualifier:
                raise ValueError(f"without belongs to the qualifier value, not to {name}")
        return self


class BindingSpec(Strict):
    """ARCH-52: how the family integration binds each instance of a device driver (module.yaml binding).

    The manifest only names the trait; everything C++-specific (construction of the configuration and its check,
    the pin and SPI adapters of the driver, the safe-off access, the RTE snapshot) is code of the module in that
    header, checked by the compiler (decision arch52-vorschlag, doc/architecture/treiber-anbindung-analyse.md). What
    is wired, when and where it runs stays in the manifest: connect, requires, lifecycle, console, rte.
    tools/bswgen writes the per-instance block (integration/moduleBinding.h) from both; the family port only
    provides one binder per connection kind and contract.
    """

    include: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_./-]+\.h(pp)?$")] = Field(
        description="Header of the trait, relative to the include directory of the module (`include/`), e.g. "
                    "`gd/Tmc6200Binding.h`.")
    trait: CQualifiedName = Field(
        description="C++ type of the trait, e.g. `::gd::Tmc6200Binding`: System, Parts<I, B>, config<I>(), "
                    "valid(), create() and per safe_off connection SafeOff_<connection> (contract in "
                    "tools/bswgen/modules.py render_binding_header).")


MODULE_KINDS = ("mcu_driver", "device_driver", "service", "library", "contract")
# DOC-14: kinds that are catalogue entries only - a library (code other modules or the application link, e.g. the
# E2E profiles) or a contract (interface headers in libs/bsp that drivers implement, e.g. bspSpi). No board device,
# no instance, no project values, nothing generated: nxconfig and bswgen see them only as known module names.
CATALOGUE_KINDS = ("library", "contract")
# fields that make a module something nxconfig assigns, wires, parameterizes or bswgen generates for
_INTEGRATION_FIELDS = ("parts", "requires", "provides", "connect", "parameters", "project_parameters", "constraints",
                       "lifecycle", "console", "pins", "rte", "phases", "binding", "max_instances")
VERIFICATION_LEVELS = ("none", "mock", "hardware")
# PLAT-49 (doc/architecture/lizenz-und-release.md section 5.2): distribution classes in rising order - a file of a
# class may go into a release profile that is not lower (open < licensed < nda < private < workstation)
DISTRIBUTION_CLASSES = ("open", "licensed", "nda", "private", "workstation")
# how the knowledge a module is built on was obtained, and the lowest class that follows from it
PROVENANCE_ACCESS = {"own": "open", "public": "open", "customer": "licensed", "nda": "nda"}
# LicenseRef identifiers of this tree and the lowest distribution class a file under them needs (LICENSES/)
LICENSE_REF_CLASSES = {"LicenseRef-neXtBSW-Commercial": "licensed", "LicenseRef-neXtBSW-NDA": "nda",
                       "LicenseRef-neXtBSW-Proprietary": "private"}
_SPDX_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9][A-Za-z0-9.+-]*)")


def spdx_identifiers(expression: str) -> list[str]:
    """Licence identifiers of an SPDX licence expression (`A OR (B AND C) WITH X`); ValueError if it is none.

    Only the syntax is checked (identifiers, AND/OR/WITH, parentheses), not the SPDX licence list: LicenseRef-
    identifiers are free by definition, and which licences a release may carry is the allowlist of the profile
    (distribution.yaml, tools/distcheck R2)."""
    tokens, pos = [], 0
    while pos < len(expression):
        match = _SPDX_TOKEN.match(expression, pos)
        if match is None:
            if expression[pos:].strip() == "":
                break
            raise ValueError(f"not an SPDX licence expression: unexpected '{expression[pos:].strip()[:20]}'")
        tokens.append(match.group(1))
        pos = match.end()
    ids, depth, want_id = [], 0, True
    for i, token in enumerate(tokens):
        if want_id:
            if token == "(":
                depth += 1
                continue
            if token in ("AND", "OR", "WITH", ")"):
                raise ValueError(f"not an SPDX licence expression: '{token}' where a licence identifier belongs")
            if i > 0 and tokens[i - 1] == "WITH":
                want_id = False                     # exception identifier (e.g. LLVM-exception), not a licence
                continue
            ids.append(token)
            want_id = False
        elif token == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("not an SPDX licence expression: ')' without '('")
        elif token in ("AND", "OR", "WITH"):
            want_id = True
        else:
            raise ValueError(f"not an SPDX licence expression: '{token}' where AND, OR or WITH belongs (licence "
                             f"names are written without blanks, e.g. Apache-2.0)")
    if want_id or depth != 0:
        raise ValueError("not an SPDX licence expression: incomplete (missing identifier or ')')")
    return ids


class ProvenanceEntry(Strict):
    """PLAT-49: one source of knowledge a module is built on (module.yaml provenance)."""

    doc: Annotated[str, Field(min_length=1, max_length=200)] = Field(
        description="Title and revision of the source, e.g. `MC33771C data sheet Rev. 6.0` - never its content.")
    access: Literal[tuple(PROVENANCE_ACCESS)] = Field(
        description="How the source was obtained: `own` (own work, measurement), `public` (freely downloadable), "
                    "`customer` (under a customer contract) or `nda` (under a confidentiality agreement with the vendor). It sets "
                    "the lowest distribution class of the module: own/public open, customer licensed, nda nda.")
    verify: bool = Field(
        default=False, description="true: the access is not established yet (nxconfig warns until it is).")


class Manifest(Strict):
    module: Name
    kind: Literal[MODULE_KINDS] = Field(
        description="What the module is: `device_driver` (serves board parts), `mcu_driver`, `service`, `library` "
                    "(code other modules link, no configuration) or `contract` (interface headers in libs/bsp that "
                    "drivers implement). `library` and `contract` are catalogue entries only: no board device, no "
                    "project parameters, nothing generated.")
    description: str | None = None
    # DOC-14: state of verification for the module catalogue (doc/reference/modules.md). No tool derives anything
    # from it; it is stated by hand from the changelog, with the evidence in verification_note or a YAML comment.
    verification: Literal[VERIFICATION_LEVELS] | None = Field(
        default=None, json_schema_extra=NO_UNIT,
        description="How far the module is verified: `none` (not yet), `mock` (host tests, register models, "
                    "generator and build tests only), `hardware` (behaviour confirmed on a real board). Left out: "
                    "not stated. Set by hand from the changelog; `hardware` needs the board evidence (date or task "
                    "ID) in `verification_note` or a comment.")
    verification_note: Annotated[str, Field(min_length=1, max_length=400)] | None = Field(
        default=None,
        description="One short English sentence to go with `verification`: what is confirmed on a board and what "
                    "is not (e.g. a path only covered by host tests). Needs `verification`.")
    # PLAT-49: licence and release class of the module (doc/architecture/lizenz-und-release.md section 5.2). Left
    # out, the central table distribution.yaml at the root of the tree decides; a manifest that states them beats it.
    license: Annotated[str, Field(min_length=1, max_length=200)] | None = Field(
        default=None, json_schema_extra=NO_UNIT,
        description="SPDX licence expression for the files of the module folder that carry no SPDX header of "
                    "their own, e.g. `Apache-2.0` or `LicenseRef-neXtBSW-NDA` (texts under LICENSES/). Left out: "
                    "distribution.yaml.")
    distribution: Literal[DISTRIBUTION_CLASSES] | None = Field(
        default=None, json_schema_extra=NO_UNIT,
        description="Release class of the module folder: `open` (open source line), `licensed` (licensees only), "
                    "`nda` (derived from NDA material), `private` (never released), `workstation` (never in a "
                    "repository - not possible for a module in a tree). Not lower than its `provenance` and its "
                    "`license` need. Left out: distribution.yaml.")
    provenance: list[ProvenanceEntry] = Field(
        default=[], description="Sources of knowledge the module is built on (documents, not code origin), each "
                                "with how it was obtained. Needs `distribution`.")
    parts: list[Annotated[str, Field(min_length=1)]] = []    # fnmatch patterns on board devices[].part
    requires: dict[Name, Annotated[int, Field(gt=0)]] = {}   # contracts through buses, e.g. spi_device: 1
    provides: list[Name] = []                               # roles, e.g. can_phy, early_init
    connect: dict[Name, ConnectSpec] = {}                   # board entries each instance is wired to
    parameters: dict[ParameterName, ParameterSpec] = {}
    # Module without a board device (service, mcu_driver) whose parameter values come from the project
    # (project.yaml modules.<module>, e.g. bspAdcAccess, ecuState). Device drivers always get their values per
    # instance from modules.<device> and must not set this.
    project_parameters: bool = False
    constraints: list[str] = []                             # "<operand> <op> <operand>", operands: int parameter or integer
    lifecycle: Lifecycle | None = None
    cmake_target: Name | None = None
    console: str | None = None                              # command group; "instance": parameter console per instance
    pins: dict[Name, PinSpec] = {}                          # IO-33: part pins wired per board (devices[].pins)
    # RTE-18: values the application reads through the RTE, targets <prefix>.<Instance>.<value>
    rte: RteSpec | None = None
    # IO-45: gate driver modules (provides gate_driver): the number of half bridges (phases) the device drives, a fact
    # of the part and its driver (gdTmc6200: three, gd::PhaseDuty). The PWM group bound to the device (board
    # pwm[].gate_driver) needs one pair per phase for the driver's enable rule (all phases in one state); nxconfig
    # warns otherwise, and the family port attaches the phase duty source only to a group with this many pairs.
    phases: Annotated[int, Field(ge=1, le=8)] | None = None
    # ARCH-52: the most instances one project may use (e.g. 1 for a driver whose integration holds one object);
    # nxconfig reports the surplus device at the project entry that brings it in
    max_instances: Annotated[int, Field(ge=1, le=64)] | None = Field(
        default=None, description="Most instances of this module one project may use. Left out: no limit beyond the "
                                  "board.")
    # ARCH-52: trait of the module the generated per-instance integration is built from (BindingSpec)
    binding: BindingSpec | None = Field(
        default=None, description="Header and C++ type of the module's binding trait. With it, tools/bswgen writes "
                                  "the integration of every instance (objects, lifecycle registration in the "
                                  "declared level and task, safe_off adapters, RTE readers); no code in the family "
                                  "port.")

    @property
    def rte_prefix(self) -> str | None:
        """First part of the RTE targets of this module (RTE-18), None without `rte`."""
        if self.rte is None:
            return None
        return self.rte.prefix or self.module.lower()

    @field_validator("verification_note")
    @classmethod
    def _note_needs_verification(cls, value, info):
        # a failed `verification` is missing from info.data and reported on its own
        if value is not None and "verification" in info.data and info.data["verification"] is None:
            raise ValueError("verification_note needs verification (none, mock or hardware)")
        return value

    @field_validator("license")
    @classmethod
    def _spdx_expression(cls, value):
        # PLAT-49: syntax only; what a class may carry is checked in checks.py (_licence_problems)
        if value is not None:
            spdx_identifiers(value)
        return value

    @field_validator(*_INTEGRATION_FIELDS)
    @classmethod
    def _catalogue_kind_has_no_integration(cls, value, info):
        # DOC-14: a library or contract must not look like something to assign, wire, configure or generate -
        # reported at the field (YAML path), not at the manifest as a whole
        kind = info.data.get("kind")
        if kind in CATALOGUE_KINDS and value not in (None, False, [], {}):
            raise ValueError(f"a {kind} is a catalogue entry only (no board device, no project values, nothing "
                             f"generated) and takes no {info.field_name}; use kind service or device_driver")
        return value


# ----------------------------------------------------------------------------------------------- workspace pin
# ARCH-56 (doc/architecture/customer-project.md section 3.1, gap N2): nextbsw.yaml at the root of a customer
# repository (workspace) names exactly one neXtBSW tree and the workspace's own extensions. It is NOT a fourth
# configuration level: it carries no value of the ECU, only where neXtBSW and the extensions are found.

# MAJOR.MINOR.PATCH, the same rule as the VERSION file of the tree (tools/nxconfig/version.py)
SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
PIN_SOURCES = ("submodule", "archive")
DISTRIBUTION_PROFILES = ("open", "licensed", "nda")
SearchPath = Annotated[str, Field(min_length=1)]


class PinnedTree(Strict):
    version: Annotated[str, Field(pattern=SEMVER)] = Field(
        description="The neXtBSW release the workspace is built with, `MAJOR.MINOR.PATCH`. Must equal the `VERSION` "
                    "file of the tree at `path` (and, for a release build, the tag `v<version>` it is on).")
    source: Literal[PIN_SOURCES] = Field(
        description="How the tree gets into the workspace: `submodule` (git submodule at `path`, checked out at the "
                    "tag `v<version>`; recommended) or `archive` (release archive unpacked to `path`, checked by "
                    "`url` and `sha256`).")
    path: SearchPath = Field(
        description="Folder of the neXtBSW tree, relative to `nextbsw.yaml` (usually `nextbsw`).")
    profile: Literal[DISTRIBUTION_PROFILES] = Field(
        default="open",
        description="Distribution line of the tree (doc/architecture/lizenz-und-release.md section 5.2): `open`, "
                    "`licensed` or `nda`. Left out: `open`.")
    url: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, description="`archive` only: where the release archive is downloaded from.")
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, description="`archive` only: SHA-256 of the release archive, 64 lower-case hex digits.")

    @model_validator(mode="after")
    def _archive_fields(self):
        if self.source == "archive" and (self.url is None or self.sha256 is None):
            raise ValueError("source archive needs url and sha256 of the release archive")
        if self.source == "submodule" and (self.url is not None or self.sha256 is not None):
            raise ValueError("url and sha256 belong to source archive; a submodule is pinned by its gitlink")
        return self


class WorkspaceOverlay(Strict):
    """One further neXtBSW tree next to the pinned one (licensed line, model B, customer-project.md 5.8).

    TOOL-65 (gap N10) reads it for the part a release depends on: an overlay belongs to the same release as the
    pinned tree, so its `version` must equal `nextbsw.version`, and the tree itself must be a clean, annotated,
    published baseline (`tools/nxconfig/pincheck.py`). Finding the modules in it and checking them against
    `nextbsw.profile` is N16 and not read yet.
    """
    path: SearchPath = Field(
        description="Folder of the overlay tree, relative to `nextbsw.yaml` (usually `nextbsw-licensed`).")
    version: Annotated[str, Field(pattern=SEMVER)] = Field(
        description="The release of the overlay, `MAJOR.MINOR.PATCH`. Must equal `nextbsw.version`: an overlay "
                    "is the licensed half of the same release, not a tree of its own age.")


class WorkspaceSearch(Strict):
    mcudb: list[SearchPath] = Field(
        default=[],
        description="Derivative databases of the workspace (`<dir>/<family>/<derivative>.yaml`), relative to "
                    "`nextbsw.yaml`, searched before the tree's `mcudb`. A derivative found in two places is an "
                    "error.")
    modules: list[SearchPath] = Field(
        default=[],
        description="Module folders of the workspace (`<dir>/**/<module>/module.yaml`), relative to "
                    "`nextbsw.yaml`, searched before the tree's `libs/bsw` and `libs/bsp`. A module name found in "
                    "two places is an error.")
    boards: list[SearchPath] = Field(
        default=[],
        description="Board folders of the workspace (`<dir>/<board>/board.yaml`), relative to `nextbsw.yaml`, "
                    "offered before the tree's `boards`. A project still names its board by path; a board name "
                    "found in two places is an error.")
    overlays: list[WorkspaceOverlay] = Field(
        default=[],
        description="Further neXtBSW trees of the same release (licensed lines, model B): `path` and `version` "
                    "each. The pin check (N10) requires the same version and the same release tag; the module "
                    "search in them is N16.")


class WorkspacePin(Strict):
    """nextbsw.yaml: which neXtBSW tree a workspace is built with, and its own search paths."""
    schema_version: Literal[1] = Field(description="Version of the file format; currently 1.")
    nextbsw: PinnedTree = Field(description="The pinned neXtBSW tree.")
    search: WorkspaceSearch = Field(
        default_factory=WorkspaceSearch,
        description="The workspace's own extensions, searched before the tree's own folders.")
