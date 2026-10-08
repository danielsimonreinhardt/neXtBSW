/* Host test stub of mcu/mcu.h for RomCheckHostTest (SYS-38): a register model of the S32K1 CRC unit.
 *
 * Names, bit positions and the register layout follow external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/
 * nxp/S32K148_CRC.h (identical on the S32K144). DATA is a proxy: a 32-bit write with CTRL[WAS]=1 loads the seed, with
 * WAS=0 it feeds one word; a read returns the result with the TOTR and FXOR transformation of CTRL. The model
 * processes a written word as [RM] describes it: TOT transposes it first, then the unit shifts it in from bit 31
 * down. Only the configuration RomCheck uses (32 bit, TOT 0/1, TOTR 0/2) is modelled; anything else counts as
 * unsupported and fails the test.
 *
 * SYS-44: faults of the unit for the known-answer test (a result stuck at one value, a polynomial bit lost), a hook
 * called at every fed word (a flash write that begins during a pass) and FTFC FSTAT with CCIF (a flash command).
 */

#pragma once

#include <cstdint>

#define CRC_CTRL_TCRC(x) ((static_cast<uint32_t>(x) << 24U) & 0x01000000U)
#define CRC_CTRL_WAS(x)  ((static_cast<uint32_t>(x) << 25U) & 0x02000000U)
#define CRC_CTRL_FXOR(x) ((static_cast<uint32_t>(x) << 26U) & 0x04000000U)
#define CRC_CTRL_TOTR(x) ((static_cast<uint32_t>(x) << 28U) & 0x30000000U)
#define CRC_CTRL_TOT(x)  ((static_cast<uint32_t>(x) << 30U) & 0xC0000000U)

namespace crc_model
{
inline uint32_t reflect(uint32_t value, unsigned bits)
{
    uint32_t result = 0U;
    for (unsigned i = 0U; i < bits; ++i)
    {
        result = (result << 1U) | (value & 1U);
        value >>= 1U;
    }
    return result;
}

inline uint32_t transposeBitsInBytes(uint32_t const value)
{
    uint32_t result = 0U;
    for (unsigned byte = 0U; byte < 4U; ++byte)
    {
        result |= reflect((value >> (8U * byte)) & 0xFFU, 8U) << (8U * byte);
    }
    return result;
}

struct State
{
    uint32_t crc         = 0U;
    uint32_t dataWrites  = 0U; // words fed (WAS=0)
    uint32_t seedWrites  = 0U;
    bool unsupported     = false;
    // SYS-44: faults and hook
    bool stuck           = false; // DATA reads stuckValue whatever was fed
    uint32_t stuckValue  = 0U;
    uint32_t polyFault   = 0U;    // bits of GPOLY the unit ignores
    void (*onData)(uint32_t dataWrites) = nullptr; // called after every fed word
};

inline State& state()
{
    static State s;
    return s;
}
} // namespace crc_model

struct CRC_Type;

class CrcDataRegister
{
public:
    explicit CrcDataRegister(CRC_Type& owner) : _owner(owner) {}

    CrcDataRegister& operator=(uint32_t value);
    operator uint32_t() const;

private:
    CRC_Type& _owner;
};

struct CRC_Type
{
    struct
    {
        CrcDataRegister DATA;
    } DATAu;

    uint32_t GPOLY = 0U;
    uint32_t CTRL  = 0U;

    CRC_Type() : DATAu{CrcDataRegister(*this)} {}
};

inline CrcDataRegister& CrcDataRegister::operator=(uint32_t const value)
{
    auto& s = ::crc_model::state();
    uint32_t const ctrl = _owner.CTRL;
    if ((ctrl & CRC_CTRL_TCRC(1)) == 0U)
    {
        s.unsupported = true; // 16-bit mode is not used by RomCheck
    }
    uint32_t const tot = (ctrl >> 30U) & 3U;
    uint32_t word      = value;
    if (tot == 1U)
    {
        word = ::crc_model::transposeBitsInBytes(value);
    }
    else if (tot != 0U)
    {
        s.unsupported = true;
    }
    if ((ctrl & CRC_CTRL_WAS(1)) != 0U)
    {
        s.crc = word;
        ++s.seedWrites;
        return *this;
    }
    s.crc ^= word;
    uint32_t const poly = _owner.GPOLY & ~s.polyFault;
    for (unsigned i = 0U; i < 32U; ++i)
    {
        s.crc = ((s.crc & 0x80000000U) != 0U) ? ((s.crc << 1U) ^ poly) : (s.crc << 1U);
    }
    ++s.dataWrites;
    if (s.onData != nullptr)
    {
        s.onData(s.dataWrites);
    }
    return *this;
}

inline CrcDataRegister::operator uint32_t() const
{
    auto& s = ::crc_model::state();
    if (s.stuck)
    {
        return s.stuckValue;
    }
    uint32_t const ctrl = _owner.CTRL;
    uint32_t const totr = (ctrl >> 28U) & 3U;
    uint32_t result     = s.crc;
    if (totr == 2U)
    {
        result = ::crc_model::reflect(result, 32U);
    }
    else if (totr != 0U)
    {
        s.unsupported = true;
    }
    if ((ctrl & CRC_CTRL_FXOR(1)) != 0U)
    {
        result = ~result;
    }
    return result;
}

inline CRC_Type* crcInstance()
{
    static CRC_Type unit;
    return &unit;
}

#define CRC (::crcInstance())

// SYS-44: flash module status (FTFC FSTAT); CCIF set = no command running (reset value)
#define FTFC_FSTAT_CCIF_MASK (0x80U)

struct FTFC_Type
{
    uint8_t FSTAT = FTFC_FSTAT_CCIF_MASK;
};

inline FTFC_Type* ftfcInstance()
{
    static FTFC_Type unit;
    return &unit;
}

#define FTFC (::ftfcInstance())
