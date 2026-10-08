/* Host test stub of util/command/GroupCommand.h (SYS-05): enough of the console command framework to compile
 * the real RteSystem.cpp. The macros expand exactly like the OpenBSW ones, the context collects the text. */

#pragma once

#include <cstdint>
#include <string>

namespace util
{
namespace command
{

/** Collects what a command writes, so a test can read it back. */
class CommandContext
{
public:
    std::string output;
};

class GroupCommand
{
public:
    struct PlainCommandInfo
    {
        char const* id;
        char const* description;
        uint8_t index;
    };

    virtual ~GroupCommand() = default;

protected:
    virtual PlainCommandInfo const* getInfo() const  = 0;
    virtual void executeCommand(CommandContext& context, uint8_t index) = 0;
};

} // namespace command
} // namespace util

#define DECLARE_COMMAND_GROUP_GET_INFO PlainCommandInfo const* getInfo() const override;

#define DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(_class, _id, _desc)                    \
    const ::util::command::GroupCommand::PlainCommandInfo* _class::getInfo() const \
    {                                                                              \
        static const PlainCommandInfo _info[] = { {(_id), (_desc), 0U},
#define COMMAND_GROUP_COMMAND(_idx, _id, _desc) {(_id), (_desc), (_idx)},
#define DEFINE_COMMAND_GROUP_GET_INFO_END \
    {                                     \
        nullptr, nullptr, 0U              \
    }                                     \
    }                                     \
    ;                                     \
    return _info;                         \
    }
