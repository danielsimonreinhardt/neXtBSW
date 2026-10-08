<!-- SPDX-License-Identifier: Apache-2.0 -->

# The AUTOSAR Classic Platform document set: a map, not a reading

Status: 2026-09-23 (**ARCH-62**). Survey only. The index was walked and counted; **no specification was read for
this page** beyond the release overview, and nothing here has been checked against the repository. Verification:
**mock/analysis**.

Update 2026-10-01 (**ARCH-62**, second part): the ECUC parameter definitions, the BSW UML model and the E2E/CRC change
histories were opened and the statements of section 3 checked against them; the result, with two corrections, is in
[`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md). Where a statement below was corrected, a note says so.
Everything else on this page is still survey level.

Companion page: [`autosar.md`](autosar.md) says which parts of neXtBSW follow a published specification and which
only borrow the vocabulary. This page says where those specifications are, what else is in the set, and which parts
of the current release would touch neXtBSW if we ever go further.

## 1. Where the documents are

The entry point is <https://www.autosar.org/standards/classic-platform>. That page carries **no direct links to
PDFs**. It offers two filters into the site's document search:

- **By release** — 16 entries, from R25-11 (current) back to Release 2.0. R3.x and older return nothing; the page
  says so itself.
- **By architecture element** — the clickable layer diagram, 19 filters (Application Interfaces, Run Time
  Environment, System/Memory/Crypto/Communication/Off-board Communication Services, the five hardware-abstraction
  blocks, the five driver blocks, Microcontroller Abstraction Layer). A few of those links still filter on
  **R24-11**, among them "Crypto Services" and "Microcontroller Abstraction Layer" — check the release filter after
  clicking.

Every file is reachable directly, without the search:

```
https://www.autosar.org/fileadmin/standards/<release>/CP/AUTOSAR_CP_<TYPE>_<Name>.pdf
```

for example `…/standards/R25-11/CP/AUTOSAR_CP_SWS_CRCLibrary.pdf`. Foundation and Adaptive use `FO` and `AP` in
place of `CP`.

## 2. What the set contains (R25-11, Classic Platform)

**218 items: 212 PDFs, 5 ZIPs, 1 hash file.**

| Type | Count | What it is |
|---|---|---|
| SWS | 127 | Software Specification — the module behaviour, API and configuration |
| RS | 45 | Requirements on a module or area, the layer above SWS |
| EXP | 21 | Explanation — rationale and guidance, e.g. LayeredSoftwareArchitecture |
| TR | 11 | Technical Report — methodology, VFB, release overview |
| TPS | 7 | Template Specification — SystemTemplate, ECUConfiguration, SWC template, … |
| ASWS | 1 | TransformerGeneral |

The specifications are grouped into 19 clusters: ReleaseDocumentation, ApplicationInterfaces, BSWGeneral,
Communication, Crypto, Diagnostics, General, GlobalTime, IO, Libraries, MCAL, Memory, MethodologyAndTemplates,
ModeManagement, RTE, SWArch, Safety, Security, SystemServices.

The non-PDF files are the interesting part for a configurator:

| File | Why it matters here |
|---|---|
| `AUTOSAR_CP_MOD_ECUConfigurationParameters.zip` | the complete ECUC parameter definitions, machine-readable — the closest thing to a reference schema for what `tools/nxconfig` does with its own YAML |
| `AUTOSAR_CP_MOD_BSWUMLModel.zip` | the BSW UML model: modules, interfaces, types |
| `AUTOSAR_CP_MOD_AISpecification.zip`, `…Examples.zip` | standardised application interfaces (port and data type blueprints) |
| `AUTOSAR_CP_EXP_ModelingShowCases.zip` | worked modelling examples |
| `AUTOSAR_CP_TR_SpecificationHashes.sha256` | checksums for the whole release |

Other releases, for scale: CP had 234 documents in R24-11, 229 in R23-11, 226 in R22-11, 219 in R21-11, 217 in
R20-11, 231 in R19-11, 232 in R4.4.0. R25-11 also ships **64 Foundation** and **63 Adaptive** documents; Foundation
is a separate page and increasingly holds the protocol specifications (PRS) that the CP documents refer to.

## 3. What R25-11 changed that would touch us

R25-11 (published 2025-11-27, a minor release, supersedes R24-11) is mostly a clean-up, and the clean-up hits
exactly the areas neXtBSW has open work in:

- **The Flash and EEPROM drivers are gone.** `CP SWS FlashDriver` (Fls) and `CP SWS EEPROMDriver` (Eep) are
  *removed* — not deprecated, not released any more. The Memory cluster is now `MemoryDriver` (Mem),
  `MemoryAccess` (MemAcc), `MemoryAbstractionInterface`, `NVRAMManager`, `MemoryMapping`, `FlashTest`, `RAMTest`,
  `FlashEEPROMEmulation`, `EEPROMAbstraction`. Anyone reading AUTOSAR for guidance on our storage stack should read
  Mem/MemAcc, not the Fls/Eep material that most textbooks still describe.
  *Footnote 2026-10-01:* the ECUC definitions still contain `Fee`, `Ea` and `MemIf`, and NvM still points at an Ea or
  Fee block; those two sit on MemAcc. The stack is NvM, Fee/Ea, MemAcc, Mem; only the driver-specific modules below
  Fee/Ea are gone.
- **LdCom disappears into Com.** The *COMHandler* concept merges large-data COM into `Com` and adds communication
  error abstraction and monitoring (data integrity, reception deadline monitoring, data invalidity, range check).
  `CP SWS LargeDataCOM` is removed.
- **Secure onboard communication moved to Foundation** (`FO RS SecureOnboardCommunication`), as did the CAN time
  sync protocol, split into `FO PRS TimeSyncOverCANProtocol` (protocol) and `CP SWS TimeSyncOverCAN` (AUTOSAR
  realisation).
  *Correction 2026-10-01:* only the protocol and requirements moved. `SecOC` and `CanTSyn` are still Classic
  Platform modules with their own CP SWS and CP configuration parameters. The same split already applies to E2E, which is why `libs/bsw/e2e` cites a PRS and an SWS.
- **Removed or obsolete, so not worth planning against:** TTCAN driver and interface (removed), the whole V2X branch
  including Chinese V2X and Wireless Ethernet (obsolete), `HWTestManager` with its RS and integration guide
  (obsolete) — the last one is worth knowing because start-up/shutdown hardware tests are a plausible thing to want
  in a safety context, and AUTOSAR has just stopped standardising it.
- **New:** Vehicle Data Protocol (`VDPCMRemote`, in-vehicle data collection with sampling decoupled from
  transmission), DDS on the Classic Platform (events/methods/fields over DDS with QoS, new `DDS Transformer`),
  reworked MACsec support.
- The release overview also names **known technical deficiencies** in individual specifications (LSduRouter,
  BusMirroring, TimeSyncOverCAN/FlexRay, TransformerGeneral, the SAE J1939 modules, BSWDistributionGuide). Read
  that chapter before trusting one of those documents.

One cross-check worth recording: the current index contains no `FlashDriver` and no `EEPROMDriver`, which confirms
the removal from the outside, and it still serves the V2X and `HWTestManager` PDFs, which confirms that *obsolete*
still means downloadable.

Our own E2E implementation is based on **R22-11** documents (`libs/bsw/e2e/doc/e2e.md`). Checked 2026-10-01 against
the R25-11 change histories: nothing relevant for P01, P02, P11 and the CRC routines in use (details in
[`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md) section 4).

## 4. How to handle these documents here

The specifications are free to download and are copyrighted; the site's terms of use allow reproduction for
information only. Treat them like the licensed standards in `doc/confidential/`:

- **Do not commit AUTOSAR PDFs or ZIPs to this repository**, and do not paste specification text into code, comments
  or documentation.
- Cite by document name, release and section — `AUTOSAR_CP_SWS_CRCLibrary R22-11, section 7.2` — the way
  `libs/bsw/e2e` already does.
- Published check values, API names and numeric constants that we implement are fine to use; that is what the
  specification is for.

## 5. Appendix: the R25-11 CP documents by type

Names only, prefix `AUTOSAR_CP_<TYPE>_`, extension `.pdf`.

**SWS (127)** — ADCDriver, ARTI, BFXLibrary, BSWGeneral, BSWModeManager, BSWMulticoreLibrary, BulkNvDataManager,
BusMirroring, CANDriver, CANInterface, CANNetworkManagement, CANStateManager, CANTransceiverDriver,
CANTransportLayer, CANXLDriver, CANXLTransceiverDriver, COM, COMBasedTransformer, COMManager, CRCLibrary,
CellularV2XDriver, ChineseV2XManagement, ChineseV2XMessage, ChineseV2XNetwork, ChineseV2XSecurity,
CommunicationStackTypes, CoreTest, CryptoDriver, CryptoInterface, CryptoServiceManager, DIODriver,
DataDistributionService, DataDistributionServiceTransformer, DefaultErrorTracer, DiagnosticCommunicationManager,
DiagnosticEventManager, DiagnosticLogAndTrace, DiagnosticOverIP, E2ELibrary, E2ETransformer, ECUStateManager,
EEPROMAbstraction, EFXLibrary, EthernetDriver, EthernetInterface, EthernetStateManager, EthernetSwitchDriver,
EthernetTransceiverDriver, Firewall, FlashEEPROMEmulation, FlashTest, FlexRayARTransportLayer, FlexRayDriver,
FlexRayISOTransportLayer, FlexRayInterface, FlexRayNetworkManagement, FlexRayStateManager, FlexRayTransceiverDriver,
FunctionInhibitionManager, GPTDriver, HWTestManager, I2CDriver, ICUDriver, IEEE1722TransportLayer, IFLLibrary,
IFXLibrary, IOHardwareAbstraction, IPDUMultiplexer, ISO15118Charging, IntrusionDetectionSystemManager, KeyManager,
LINDriver, LINInterface, LINStateManager, LINTransceiverDriver, LSduRouter, MACsecKeyAgreement, MCUDriver,
MFLLibrary, MFXLibrary, MSFLibrary, MemoryAbstractionInterface, MemoryAccess, MemoryDriver, MemoryMapping,
NVRAMManager, NetworkManagementInterface, OCUDriver, OS, PDURouter, PWMDriver, PlatformTypes, PortDriver, RAMTest,
RTE, SAEJ1939DiagnosticCommunicationManager, SAEJ1939FunctionalSafetyCommProtocol, SAEJ1939NetworkManagement,
SAEJ1939RequestManager, SAEJ1939TransportLayer, SOMEIPTransformer, SOMEIPTransportProtocol, SPIHandlerDriver,
SecureOnboardCommunication, ServiceDiscovery, SocketAdaptor, SoftwareClusterConnection, StandardTypes,
SynchronizedTimeBaseManager, TcpIp, TimeService, TimeSyncOverCAN, TimeSyncOverEthernet, TimeSyncOverFlexRay,
UDPNetworkManagement, V2XBasicTransport, V2XDataManager, V2XFacilities, V2XGeoNetworking, V2XManagement, VDPCMRemote,
WatchdogDriver, WatchdogInterface, WatchdogManager, WirelessEthernetDriver, WirelessEthernetTransceiverDriver, XCP

**ASWS (1)** — TransformerGeneral

**RS (45)** — ADCDriver, BSWGeneral, BusMirroring, CAN, COM, ChargingManager, ChineseV2XCommunication, CoreTest,
CryptoStack, DIODriver, DebugTraceProfile, Ethernet, FirmwareOverTheAir, FlashTest, FlexRay,
FunctionInhibitionManager, GPTDriver, Gateway, HWTestManager, I2CDriver, ICUDriver, IOHWAbstraction, IPDUMultiplexer,
LIN, Libraries, MCUDriver, MemoryHWAbstractionLayer, MemoryServices, ModeManagement, OCUDriver, OS, PWMDriver,
PortDriver, RAMTest, RTE, SAEJ1939, SPALGeneral, SPIHandlerDriver, SWCModeling, SoftwareClusterConnection,
TimeService, Transformer, V2XCommunication, WatchdogDriver, XCP

**TPS (7)** — BSWModuleDescriptionTemplate, DiagnosticExtractTemplate, ECUConfiguration, ECUResourceTemplate,
SoftwareComponentTemplate, SystemTemplate, TimingExtensions

**EXP (21)** — AIADASAndVMC, AIBodyAndComfort, AIChassis, AIHMIMultimediaAndTelematics,
AIOccupantAndPedestrianSafety, AIPowertrain, AIUserGuide, ARTI, ApplicationLevelErrorHandling, BSWDistributionGuide,
CDDDesignAndIntegrationGuideline, ErrorDescription, FirmwareOverTheAir, FunctionalSafetyMeasures,
LayeredSoftwareArchitecture, MacroEncapsulationofInterpolationCalls, ModeManagementGuide, NVDataHandling,
SafetyUseCase, SwClusterDesignAndIntegrationGuideline, UtilizationOfCryptoServices

**TR (11)** — AIDesignPatternsCatalogue, AIMeasurementCalibrationDiagnostics, BSWUMLModelModelingGuide,
GeneralBlueprintsSupplement, HWTestManagementIntegrationGuide, Methodology, ModelingShowCases, ReleaseOverview,
SWCModelingGuide, VFB, WorkflowExample
