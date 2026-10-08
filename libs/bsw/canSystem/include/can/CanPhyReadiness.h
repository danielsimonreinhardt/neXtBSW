#pragma once

#include "bsp/can/canTransceiver/CanPhy.h"

#include <atomic>

namespace can
{

/**
 * Observer of the readiness of the CAN transceivers (role can_phy, COM-23). Implemented by the CAN system.
 *
 * The readiness itself is still read with ::bios::CanPhy::getPhyErrorStatus() (CanPhyGate::isReady); this only says
 * *when* it is worth reading: a provider whose readiness can change (a SBC that polls its transceiver status, a
 * driver of a transceiver with an error pin) reports every change, so the CAN system does not have to sample the
 * readiness faster than its own supervision cycle. Until COM-23 the CAN system asked every phy every millisecond
 * (COM-22), which every board paid for, also one with passive transceivers only.
 */
class ICanPhyReadinessListener
{
public:
    ICanPhyReadinessListener()                                           = default;
    ICanPhyReadinessListener(ICanPhyReadinessListener const&)            = delete;
    ICanPhyReadinessListener& operator=(ICanPhyReadinessListener const&) = delete;

    /**
     * The readiness \p phy reports may have changed. Called in the task (or interrupt) of the provider: the
     * listener only hands the work over to its own task (e.g. ::async::execute), it neither blocks nor touches a bus
     * here. One call per change is enough; several calls before the listener's task runs may collapse into one
     * evaluation, which then reads the current readiness of every phy.
     */
    virtual void phyReadinessChanged(::bios::CanPhy& phy) = 0;

protected:
    ~ICanPhyReadinessListener() = default;
};

/**
 * Link between the providers of the role can_phy and the CAN system (COM-23): one listener per ECU, registered by
 * the CAN system while it runs. Header only and without board knowledge: a provider does not know whether a CAN
 * system exists (a board with SBC but without CAN, or before the CAN system started), and the CAN system does not
 * know which providers report - it keeps its own supervision cycle as the fallback for a provider that does not.
 *
 * Contract of a provider whose readiness can change: call notify() after every change of what its
 * getPhyErrorStatus() answers, in its own task, after the new value is visible to that call. A provider whose
 * readiness never changes (passive transceiver, ::bios::CanPhyDummy) never calls it and costs nothing.
 *
 * Only plain loads and stores of one pointer: no lock, and no compare-and-swap, which ARMv6-M (Cortex-M0+) does not
 * have. The listener is registered and removed by one task (the CAN system's), notify() may run in any task.
 */
class CanPhyReadiness
{
public:
    CanPhyReadiness() = delete;

    /** Registers \p listener; false (nothing changed) if another listener is registered already. */
    static bool setListener(ICanPhyReadinessListener& listener)
    {
        ICanPhyReadinessListener* const current = _listener.load(std::memory_order_acquire);
        if ((current != nullptr) && (current != &listener))
        {
            return false;
        }
        _listener.store(&listener, std::memory_order_release);
        return true;
    }

    /** Removes \p listener if it is the registered one. */
    static void removeListener(ICanPhyReadinessListener& listener)
    {
        if (_listener.load(std::memory_order_acquire) == &listener)
        {
            _listener.store(nullptr, std::memory_order_release);
        }
    }

    static bool hasListener() { return _listener.load(std::memory_order_acquire) != nullptr; }

    /** Provider side: the readiness of \p phy changed. Without a listener nothing happens. */
    static void notify(::bios::CanPhy& phy)
    {
        ICanPhyReadinessListener* const listener = _listener.load(std::memory_order_acquire);
        if (listener != nullptr)
        {
            listener->phyReadinessChanged(phy);
        }
    }

private:
    static inline std::atomic<ICanPhyReadinessListener*> _listener{nullptr};
};

} // namespace can
