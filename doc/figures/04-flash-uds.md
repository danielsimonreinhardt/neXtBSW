<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 4: Flash-Ablauf über UDS

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
sequenceDiagram
    autonumber
    participant T as Tester canflash
    participant A as Application ecu/uds
    participant B as Bootloader

    Note over T,A: pre-programming, best effort
    T->>A: 10 03, 85 02, 28 03
    T->>A: 10 02
    A-->>T: 50 02
    A->>A: boot request, software reset
    loop until the bootloader answers
        T->>B: 10 02
    end
    B-->>T: 50 02
    T->>B: 10 02 programmingSession
    T->>B: 27 01 requestSeed
    B-->>T: seed
    T->>B: 27 02 sendKey, HMAC-SHA256
    T->>B: 22 F180 boot software id
    T->>B: 22 F1A0, 31 01 F1A0 compatibility
    T->>B: 2E F184 fingerprint
    T->>B: 31 01 FF00 eraseMemory
    B-->>T: 7F 31 78 responsePending
    B-->>T: 71 01 FF00
    T->>B: 34 RequestDownload
    loop each block
        T->>B: 36 TransferData
        B-->>T: 76
    end
    T->>B: 37 RequestTransferExit
    T->>B: 31 01 FF01 checkMemory, CRC-32
    B-->>T: 71 01 FF01 status 00
    T->>B: 11 01 hardReset
    Note over B,A: start check, jump into the new application
```

**Legende**

- Ablauf von `canflash flash` bzw. dem CMake-Ziel `flash-can`. Ohne `--wait` schaltet der Tester eine laufende
  Anwendung selbst in den Bootloader (Schritte 2 bis 6).
- Die Vorbereitung (Schritt 1) ist best effort: ein älterer Stand lehnt sie ab, der Flash-Lauf geht weiter.
- Ohne Fingerprint (`2E F184`) lehnt der Bootloader Löschen und Download mit NRC 0x22 ab.
- Passt das Abbild nicht zum Ziel (lokale Prüfung oder Routine `F1A0`), wird vor dem Löschen abgebrochen und mit
  `11 01` zurückgesetzt; die installierte Anwendung startet wieder, wenn sie gültig ist.
- `7F 31 78` steht hier stellvertretend für jeden langen Auftragsschritt (Löschen, CRC, Programmieren); der
  Bootloader sendet ihn nur, wenn der nächste Schritt P2* überschreiten könnte.
- Bootloader vor 0.3.4 prüfen die Kompatibilität unter Routine `FF02` (BL-24); canflash wählt nach DID `F180`.

Quellen: [`tools/canflash/README.md`](../../tools/canflash/README.md),
[`tools/canflash/canflash.py`](../../tools/canflash/canflash.py) (`flash_over_bus`, `flash_application`,
`pre_programming_step`), [Bootloader-Doku, Abschnitte 2 und 4](../../bootloader/doc/README.md#4-security-access-identification-and-fingerprint-bl-03-bl-04-bl-10),
[`ecu/uds/src/systems/DiagSystem.cpp`](../../ecu/uds/src/systems/DiagSystem.cpp) (`enterBootloader`).
