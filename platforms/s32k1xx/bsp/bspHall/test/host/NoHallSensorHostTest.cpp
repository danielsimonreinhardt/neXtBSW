// Host test for a project without a hall device (IO-07, lesson of ARCH-17): the generated configuration must be
// includable on its own - no register, no sector table, no interrupt - and the driver must still compile and
// link, so a board without a motor is not a special case of the build. Every sensor reports "no reading".
// With a generat that has a sensor this file must NOT compile; tools/bswgen/test_bswgen.py checks that too.

#include "hall/HallSensors.h"

#include <cstdio>

#if NX_HALL_SENSOR_COUNT != 0
#error "this test belongs to a project without a hall device (NX_HALL_SENSOR_COUNT must be 0)"
#endif

#if NX_HALL_PORT_COUNT != 0
#error "a project without a hall device needs no pin interrupt vector (NX_HALL_PORT_COUNT must be 0)"
#endif

#if NX_HALL_PORT_MASK != 0x00U
#error "a project without a hall device claims no pin interrupt vector (NX_HALL_PORT_MASK must be 0)"
#endif

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

// The instance lists add nothing to a structure or an enumeration (the macro must be usable unconditionally)
struct Probe
{
    int first;
#define NX_HALL_MEMBER(name) int name;
    NX_HALL_SENSORS(NX_HALL_MEMBER)
#undef NX_HALL_MEMBER
    int last;
};

struct VectorProbe
{
    int first;
#define NX_HALL_VECTOR_MEMBER(index, port, handler, bit) int handler;
    NX_HALL_PORT_VECTORS(NX_HALL_VECTOR_MEMBER)
#undef NX_HALL_VECTOR_MEMBER
    int last;
};
} // namespace

int main()
{
    CHECK(sizeof(Probe) == 2U * sizeof(int));
    CHECK(sizeof(VectorProbe) == 2U * sizeof(int));
    CHECK(bios::HallSensors::SensorCountAll == 0U);
    // the calls exist and do nothing: the integration needs no #if around them
    bios::HallSensors::init();
    bios::HallSensors::edge(0U, 0U);
    bios::HallSensors::Reading const r = bios::HallSensors::get(0U);
    CHECK(r.quality == bios::HallSensors::QUALITY_DEBOUNCE);
    CHECK((r.state == 0U) && (r.sector == 0U) && (r.period_us == 0U) && (r.mech_period_us == 0U));
    CHECK((r.speed_rpm == 0U) && (r.direction == bios::HallSensors::DIRECTION_UNKNOWN));
    CHECK(bios::HallSensors::getStatistics(0U).edges == 0U);
    CHECK(bios::HallSensors::getStatistics(0U).glitches == 0U);
    CHECK(bios::HallSensors::getName(0U)[0] == '\0');
    std::printf("no_hall_sensor_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
