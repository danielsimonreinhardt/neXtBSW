// SYS-42: pin table of a test profile for SafeIoHostTest.cpp, in the form tools/bswgen generates it
// (tools/bswgen/templates/ioConfiguration.h.j2). Deliberately not the eval board: the indicator, the locked role pin
// and the checked pin sit on other ports and pins, and two further pins share a port resp. a pin number with the
// checked pin, so a check that looks at the wrong port or bit shows up.
// SYS-24: plus the pins of a PWM pair on FTM1 (PTD15/PTD16, ALT2) and a safe_off output with drive strength (PTA7) -
// the register list of this profile is ioStubs/bsp/safeIo/safeIoConfiguration.h.

// IGNORE_INCLUDE_GUARD_CHECK

#if defined(BSP_IO_PIN_CONFIGURATION) && (BSP_IO_PIN_CONFIGURATION == 1)

Io::PinConfiguration const Io::fPinConfiguration[Io::NUMBER_OF_IOS] = {
    // clang-format off
    /*   0 */ {_PORTA_, PA3, _OUT, 0, (uint32_t) GPIO | (uint32_t) STRENGTH_ON},
    /*   1 */ {_PORTB_, PB5, _OUT, 0, (uint32_t) GPIO},
    /*   2 */ {_PORTE_, PE8, _OUT, 0, (uint32_t) GPIO},
    /*   3 */ {_PORTE_, PE9, _IN, 0, (uint32_t) GPIO | (uint32_t) PULLDOWN},
    /*   4 */ {_PORTB_, PB9, _IN, 0, (uint32_t) GPIO},
    /*   5 */ {_PORTD_, PD15, _OUT, 0, (uint32_t) ALT2},
    /*   6 */ {_PORTD_, PD16, _OUT, 0, (uint32_t) ALT2},
    /*   7 */ {_PORTA_, PA7, _OUT, 0, (uint32_t) GPIO | (uint32_t) STRENGTH_ON},
    // clang-format on
};

#else

enum PinId
{
    /*   0 */ TEST_INDICATOR,  // PTA3
    /*   1 */ TEST_ROLE_PIN,   // PTB5
    /*   2 */ TEST_NEIGHBOUR,  // PTE8, same port as the checked pin
    /*   3 */ SAFETY_TEST,     // PTE9
    /*   4 */ TEST_OTHER_PORT, // PTB9, same pin number as the checked pin
    /*   5 */ TEST_PWM_HS,     // PTD15, FTM1_CH0
    /*   6 */ TEST_PWM_LS,     // PTD16, FTM1_CH1
    /*   7 */ TEST_SAFE_OFF,   // PTA7, safe_off output (e.g. the enable of a gate driver)

    SAFETY_SAFE_STATE_PIN = TEST_INDICATOR,
    SAFETY_LOCKED_PIN     = TEST_ROLE_PIN,

    NUMBER_OF_INPUTS_AND_OUTPUTS = 8,
    PORT_UNAVAILABLE             = NUMBER_OF_INPUTS_AND_OUTPUTS,
};

#endif /* BSP_IO_PIN_CONFIGURATION == 1 */
