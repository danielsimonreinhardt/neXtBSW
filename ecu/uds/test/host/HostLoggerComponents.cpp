// Logger components that the target defines in ecu/application/src/logger/logger.cpp. Without a
// component mapping the logger is disabled (Logger::getLevel returns LEVEL_NONE).

#include <util/logger/Logger.h>

DEFINE_LOGGER_COMPONENT(GLOBAL);
