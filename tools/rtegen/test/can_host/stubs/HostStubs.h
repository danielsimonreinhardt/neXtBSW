// Minimal host replacements of the OpenBSW interfaces used by the generated RteConfig.cpp.
// Only the members the generated code touches; the target build checks the real signatures.

#pragma once

#include <cstdint>
#include <cstring>

namespace bsp
{
enum BspReturnCode
{
    BSP_OK,
    BSP_ERROR,
};
} // namespace bsp

namespace bios
{
struct DigitalInput
{
    enum DigitalInputId
    {
        BUTTON1,
    };
    static bool value;
    static ::bsp::BspReturnCode get(DigitalInputId, bool& result)
    {
        result = value;
        return ::bsp::BSP_OK;
    }
};

struct AnalogInput
{
    enum
    {
        Ai_MOTOR_NTC,
        // IO-18: channels of the sensor host test (tools/rtegen/test/sensor_host)
        Ai_SENSE_TSCURRENT,
        Ai_PROXY_ANALOG,
        Ai_CURRENTSENSE_KL30B,
        // COM-32: channels of buildtest_s32k148_min (tools/rtegen/test/diag_bsw)
        Ai_POTI,
        Ai_BANDGAP,
        Ai_CHANNEL_COUNT,
    };
    static inline uint16_t raw[Ai_CHANNEL_COUNT] = {};
    static inline bool failing                  = false;
    static inline unsigned reads                = 0U;
    static ::bsp::BspReturnCode get(uint16_t const channel, uint16_t& result)
    {
        ++reads;
        if (failing)
        {
            return ::bsp::BSP_ERROR;
        }
        result = raw[channel];
        return ::bsp::BSP_OK;
    }
};

struct AnalogInputScale
{
    static uint32_t value;
    static inline bool failing = false; // COM-32: a failed conversion
    static ::bsp::BspReturnCode get(uint16_t, uint32_t& result)
    {
        if (failing)
        {
            return ::bsp::BSP_ERROR;
        }
        result = value;
        return ::bsp::BSP_OK;
    }
};

struct Output
{
    enum OutputId
    {
        LED1,
        LED2,
    };
    static uint8_t level[2];
    static ::bsp::BspReturnCode set(OutputId chan, uint8_t vol, bool = true)
    {
        level[chan] = vol;
        return ::bsp::BSP_OK;
    }
};
} // namespace bios

namespace async
{
struct LockType
{
    LockType()
    {
        ++depth;
        ++count;
    }
    ~LockType() { --depth; }
    static int depth;
    static inline int count = 0; // locks taken (RTE-02 multitask test)
};
} // namespace async

namespace busid
{
// bus ids of the project (generated busid/busIdConfiguration.h, ARCH-07): the demo project has CAN0
static constexpr uint8_t CAN0 = 2;
} // namespace busid

namespace can
{
struct CanId
{
    static constexpr uint32_t EXTENDED_QUALIFIER_BIT     = 0x80000000U;
    static constexpr uint32_t FORCE_NON_FD_QUALIFIER_BIT = 0x20000000U;
    static constexpr uint32_t MAX_RAW_EXTENDED_ID        = 0x1fffffffU;

    static bool isExtended(uint32_t id) { return (id & EXTENDED_QUALIFIER_BIT) != 0U; }
};

class CANFrame
{
public:
    CANFrame(uint32_t id, uint8_t const payload[], uint8_t length) : _id(id), _length(length)
    {
        (void)memset(_payload, 0, sizeof(_payload));
        (void)memcpy(_payload, payload, length);
    }

    uint32_t getId() const { return _id; }
    uint8_t const* getPayload() const { return _payload; }
    uint8_t getPayloadLength() const { return _length; }

private:
    uint32_t _id;
    uint8_t _payload[8];
    uint8_t _length;
};

class IFilter
{
public:
    virtual bool match(uint32_t filterId) const = 0;

protected:
    ~IFilter() = default;
};

class BitFieldFilter : public IFilter
{
public:
    void add(uint32_t id) { added[addedCount++ % 8U] = id; }
    bool match(uint32_t) const override { return false; }

    uint32_t added[8]    = {0U};
    uint32_t addedCount  = 0U;
};

class ICANFrameListener
{
public:
    virtual void frameReceived(CANFrame const& frame) = 0;
    virtual IFilter& getFilter()                     = 0;

protected:
    ~ICANFrameListener() = default;
};

class ICANTransceiverStateListener
{
public:
    enum class CANTransceiverState : uint8_t
    {
        ACTIVE,
        PASSIVE,
        BUS_OFF,
    };
};

class ICanTransceiver
{
public:
    enum class State
    {
        CLOSED,
        INITIALIZED,
        WAKING,
        OPEN,
        MUTED,
    };

    enum class ErrorCode
    {
        CAN_ERR_OK,
        CAN_ERR_TX_FAIL,
        CAN_ERR_ILLEGAL_STATE,
    };

    virtual State getState() const                          = 0;
    virtual ICANTransceiverStateListener::CANTransceiverState getCANTransceiverState() const = 0;
    virtual ErrorCode write(CANFrame const& frame)            = 0;
    virtual void addCANFrameListener(ICANFrameListener& listener) = 0;

protected:
    ~ICanTransceiver() = default;
};

class ICanSystem
{
public:
    virtual ICanTransceiver* getCanTransceiver(uint8_t busId) = 0;

protected:
    ~ICanSystem() = default;
};
} // namespace can
