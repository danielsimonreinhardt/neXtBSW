#pragma once

#include <cstddef>

namespace highside
{

/** Names of one switch instance, chosen by the integration (static strings). */
struct HighSideSwitchNames
{
    /**
     * Longest label: every log line of HighSideSwitchSystem is sized for it (SYS-16). OpenBSW's buffered logger
     * copies a "%s" argument into one entry of 128 bytes and cuts what does not fit ("<?>"); the integration
     * checks its labels against this value at compile time (hasValidLabel), tools/logcheck relies on that.
     */
    static constexpr size_t LABEL_MAX_CHARS = 16U;

    /** Console command group, e.g. "kl30b". */
    char const* command;
    /** Name in log messages and status output, e.g. "KL30B", at most LABEL_MAX_CHARS characters. */
    char const* label;
    /** Help text of the command group, e.g. the switched supply and the part. */
    char const* description;
};

/** True if the label of \p names is set and has at most LABEL_MAX_CHARS characters (usable in static_assert). */
constexpr bool hasValidLabel(HighSideSwitchNames const& names)
{
    if (names.label == nullptr)
    {
        return false;
    }
    size_t length = 0U;
    while (names.label[length] != '\0')
    {
        ++length;
        if (length > HighSideSwitchNames::LABEL_MAX_CHARS)
        {
            return false;
        }
    }
    return true;
}

} // namespace highside
