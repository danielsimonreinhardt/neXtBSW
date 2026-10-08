// Test profile of I2cBusesHostTest.cpp (IO-28), in the form tools/bswgen writes it: two buses on one FlexIO with
// invented pins and rates, not the eval board. The bridge of the test ignores the configuration values.
#pragma once

#define NX_I2C_BUS_COUNT 2
#define NX_I2C_SENSOR_BAUDRATE_HZ 400000U
#define NX_I2C_Eeprom_BAUDRATE_HZ 100000U
#define NX_I2C_BUSES(X)                                                                                    \
    X(SENSOR, 0U, nullptr, 8000000U, NX_I2C_SENSOR_BAUDRATE_HZ, 5000U, 0U, 1U, 0U, 1U, 0U, "SENSOR on FlexIO") \
    X(Eeprom, 1U, nullptr, 8000000U, NX_I2C_Eeprom_BAUDRATE_HZ, 20000U, 2U, 3U, 2U, 3U, 2U, "Eeprom on FlexIO")
