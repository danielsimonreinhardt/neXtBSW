# Security policy

neXtBSW contains a UDS bootloader with seed/key access and CSEc key handling. Vulnerabilities in these parts can
affect devices in the field, so please report them privately and not in a public issue.

## Reporting a vulnerability

Use **GitHub Private Vulnerability Reporting**: open the repository's *Security* tab and choose *Report a
vulnerability*. Please do not send reports by e-mail and do not open a public issue or pull request for them.

A useful report names the affected component and version (or commit), the steps or the message sequence that
reproduce the problem, and the effect you observed. Do not include real production keys.

neXtBSW is maintained by one person alongside other work. The project makes no response time commitment; reports are
handled on a best-effort basis (see below). You will be told when the report is read and, if the project agrees that
it is a vulnerability, what happens next. Please keep the report private until a fix or a statement is published.

## Supported versions

neXtBSW is versioned according to [Semantic Versioning](https://semver.org). The rules, including what counts as
stable, are in [doc/architecture/versioning.md](doc/architecture/versioning.md).

| Version | Support |
|---|---|
| first release candidate (rc1) | none: published without a support commitment |
| releases after rc1 | best effort: the latest release is looked at first; there is no commitment to fix older versions, no response time and no backports |
| development branch | no support |

Best effort is not a service level. Anyone who uses neXtBSW in a product remains responsible for its safety and
security; see the [safety manual](doc/safety/safety-manual.md) for the assumptions of use. The release makes no safety
claim.

## Scope

In scope:

- the bootloader (`bootloader/`): update protocol, flash programming, start-up decision, update image handling;
- UDS services of the bootloader and of the ECU application (`ecu/`), in particular security access with seed and key,
  session handling and the services that write memory;
- CSEc: key slots, key update, the use of the hardware security module of the S32K1xx family;
- the generators, where their output weakens one of these (for example a generated key constant or access check).

Not in scope:

- Eclipse OpenBSW, FreeRTOS and the other third-party parts listed in [NOTICE](NOTICE): please report to their
  projects. If neXtBSW uses them in an unsafe way, that part is in scope.
- The configuration tools (`tools/`) as such: they run on the developer's computer and process the developer's own files.
- Keys of your own product: they are never part of this repository and have to be managed by you.
