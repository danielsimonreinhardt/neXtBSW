/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* PLAT-12: translation unit of bspCore for derivatives without the LMEM processor code cache
 * (mcudb derivative.features without lmem_cache, e.g. every S32K11x - S32K1xx Reference Manual Rev. 14, 33.1
 * "Variants of S32K11x series do not have LMEM"). The OpenBSW file src/cache/cache.cpp writes LMEM->PCCCR and
 * does not compile without an LMEM block in the device header; platforms/s32k1xx/CMakeLists.txt puts this file
 * in its place.
 *
 * On purpose it defines **nothing**: cacheEnable()/cacheDisable() of clock/cache.h stay undefined, so a caller
 * that forgot the feature check (#if NX_FEATURE_LMEM_CACHE, generated features/derivativeFeatures.h) fails to
 * link with the name of the function instead of writing to registers that do not exist.
 */
