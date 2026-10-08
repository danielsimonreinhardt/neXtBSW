# csec – host side of the CSEc way back to the factory state

Task NVM-13. Computes on the workstation what the CSEc of the S32K1 family needs for the way back after a
`PGMPART` with key space: the SHE key derivation, the messages M1..M5 of the first `MASTER_ECU_KEY` load, and the
authorisation of `CMD_DBG_AUTH`. The device side is
[`platforms/s32k1xx/bsp/bspCsec`](../../platforms/s32k1xx/bsp/bspCsec/README.md), which explains the procedure and
the safety rules. **No device access here.** Running the lines on a board is part of NVM-07, with the user.

```sh
python -m tools.csec load-master --key-file <file> [--uid <30 hex>]
python -m tools.csec check-load  --key-file <file> --m4 <64 hex> --m5 <32 hex> [--uid <30 hex>]
python -m tools.csec debug-auth  --key-file <file> --challenge <32 hex> --uid <30 hex>
```

- `load-master`: M1, M2, M3 for `CMD_LOAD_KEY` of the `MASTER_ECU_KEY` into its empty slot (authorised by itself,
  authorising value all ones, counter 1, **no key flags**), the M4/M5 the device must answer, and the device console
  lines (staged: `csec m1/m2/m3`, then `csec loadkey LOADKEY-MASTER-ECU-KEY`).
- `check-load`: the device's M4/M5 against the expectation. With a wildcard M1 (UID 0) the device answers with its
  own UID in M4 – pass `--uid` (from `csec challenge`, which runs `CMD_GET_ID`).
- `debug-auth`: `AUTHORIZATION = CMAC_K(CHALLENGE | UID)`, `K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C)`, and the line
  `csec dbgauth <AUTH> DBGAUTH-<first 4 challenge bytes>`.

Exit code 0 ok, 1 the answer does not match, 2 unusable input.

## Rules

- **No key ever gets `WRITE_PROTECTION`.** There is no option and no parameter for key flags; the flag field of M2
  is always zero (`she.memory_update`). The device refuses any other first block of M2 as well.
- The key file holds the `MASTER_ECU_KEY` as 32 hex digits and must lie **outside the repository** (refused
  otherwise). There is no option to pass the key on the command line (shell history); the key is never printed.
  Instead of `--key-file`, `--project <project.yaml>` or `--key-name <name>` read the key from the key store of the
  workstation (NVM-14: `%LOCALAPPDATA%/neXtBSW/keys/csec/<name>.key`, name from `project.yaml
  security.master_ecu_key`, default the board name; created once with `python tools/nxconfig csec-key new <project>`;
  see [`doc/architecture/csec-concept.md`](../../doc/architecture/csec-concept.md) section 6.5). The same file format
  applies to `--key-file` (`#` comments allowed; the empty slot, all zeros and public test values are refused).

## Implementation

`she.py`: AES-128 (encryption only), AES-CBC, AES-CMAC, Miyaguchi-Preneel compression, KDF, memory update protocol,
debug authorisation – pure Python on purpose (a few blocks per run; a crypto package would be a new pinned
dependency of the shared environment). `cli.py`: the command line.

## Tests

`test_she.py` (tools/test `csec-host`), published vectors only: FIPS-197 Appendix B and C.1 (AES), NIST SP 800-38A
F.2.1 (CBC), RFC 4493 section 4 (CMAC), and the HIS SHE Functional Specification v1.1 example of the memory update
protocol (K1, K2, M1..M5). The debug authorisation has no published vector and is tested by construction from the
checked KDF and CMAC. The test also checks that the constant of the device (`FIRST_LOAD_M2_BLOCK0`) and the vectors
of its host test are what this tool computes.

Sources: HIS SHE Functional Specification v1.1; NXP AN5401 Rev. 1 Appendix A and section 4.5
(`doc/hardware/s32k1xx/AN5401.pdf`); S32K1xx Reference Manual Rev. 13, 36.5.13.
