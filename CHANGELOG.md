# Changelog neXtBSW

Dieser Changelog beginnt mit der ersten Veröffentlichung. Die Entwicklungsgeschichte davor ist nicht Teil dieser
Veröffentlichung: sie wurde in einem privaten Repository geführt und enthält interne Planung, Messprotokolle
eigener Hardware und Verweise auf Unterlagen, die nicht weitergegeben werden dürfen.

Format: neueste Einträge oben. Jeder Eintrag nennt **was** geändert wurde, **warum**, die **Messwerte**, sofern es
welche gibt, und **wie verifiziert** wurde (Host/Mock oder an echter Hardware). Was nicht verifiziert ist, wird
ausdrücklich als offen genannt.

## Erste Veröffentlichung

- neXtBSW als quelloffene Basis-Software unter Apache-2.0: Konfigurationsmodell und Generatoren
  (`tools/nxconfig`, `tools/bswgen`, `tools/rtegen`), Bibliotheken (`libs/`), Plattformcode (`platforms/`),
  UDS-Bootloader (`bootloader/`) und die Derivatdatenbank (`mcudb/`).
- Eclipse OpenBSW wird als Git-Submodul eingebunden (https://github.com/eclipse-openbsw/openbsw) und nicht mitgeliefert.
- Beispielprofil: Bau-Test S32K148 (ohne Hardware). Profile von Hersteller-Eval-Boards folgen, sobald ihre Werte
  öffentlich belegt sind. Eigene Hardware des Autors und deren Schaltplanmaterial sind nicht Teil der Veröffentlichung.
- Verifikation dieser Veröffentlichung: Host-Tests und Target-Build im exportierten Baum
  (`python tools/distcheck export --verify`).
