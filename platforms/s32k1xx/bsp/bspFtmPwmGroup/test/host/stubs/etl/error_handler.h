// Host stub of etl/error_handler.h (only the macros used by ftm/Ftm.h)
#pragma once

#include <cstdlib>

#define ETL_ERROR_GENERIC(text) (text)
#define ETL_ASSERT(condition, error) \
    do                               \
    {                                \
        if (!(condition))            \
        {                            \
            std::abort();            \
        }                            \
    } while (0)
