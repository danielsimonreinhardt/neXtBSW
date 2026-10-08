# Zephyr hal_nxp – Pin-Multiplexing S32K1

- Herkunft: https://github.com/zephyrproject-rtos/hal_nxp, Ordner `dts/nxp/s32/pinctrl`, Commit
  `bb740b2e6dd0a192130caf76d45f1a8f40050346` (2026-09-09), abgerufen 2026-09-15 (ARCH-11).
- Dateien unverändert übernommen (Dateinamen wie im Original; `#include` der Delta-Header zeigt auf sie).
- Lizenz: Apache-2.0 laut Dateikopf (`Copyright 2023, NXP`, `SPDX-License-Identifier: Apache-2.0`), Lizenztext in
  `LICENSE` (aus zephyrproject-rtos/zephyr, Commit `8445f99`).
- Auswertung: `mcudb/tools/pinmux_hal_nxp.py` erzeugt daraus `mcudb/s32k1xx/<Teil>.pinmux.yaml`.
- Aus demselben Commit stammen die S32K116-Derivat-Header (PLAT-32, `s32/drivers/s32k1/BaseNXP/header`, **BSD-3-Clause**,
  nicht Apache-2.0). Sie liegen beim Build statt hier: `platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/rtd/README.md`.
- Ergänzt mit PLAT-11 (2026-09-16), gleicher Commit `bb740b2`: `S32K116_LQFP48-pinctrl.h` (Delta-Header auf Basis
  `S32K146_LQFP64-pinctrl.h`) und `S32K116_QFN32-pinctrl.h`.
