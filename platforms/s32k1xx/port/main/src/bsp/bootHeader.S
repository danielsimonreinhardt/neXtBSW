/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/


/* Boot header of the application, read by the bootloader (bootloader/src/core/app_image.h): data only, no
 * instruction. PLAT-12: no `.arch` directive any more - it named armv7-m and made the file unassemblable for
 * ARMv6-M (Cortex-M0+), although nothing here depends on the architecture. The architecture comes from -mcpu
 * of the toolchain file (tools/nxconfig, derivative.core). */

    .syntax unified

    .section .bootHeader, "a"
    .align 2
    .globl __bootHeader

__bootHeader:
    .long       __PATTERN1       /* 00h: */
    .long       __start+1          /* 04h: */
    .long       __PATTERN2       /* 08h: */
    .long       __PATTERN2       /* 0Ch: */

    .long       __PATTERN2       /* 10h:  __PATTERN3 */
    .long       __PATTERN2       /* 14h:  */
    .long       __PATTERN2       /* 18h:  */
    .long       __PATTERN2       /* 1Ch:  */

    .size    __bootHeader, . - __bootHeader
