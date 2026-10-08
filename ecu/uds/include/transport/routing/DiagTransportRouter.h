/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// Derived from OpenBSW TransportRouterSimple. That router has three fixed 4095-byte buffers
// (12.3 KB), more than the free SRAM of the S32K144. The buffer size here follows
// TransportConfiguration::DIAG_PAYLOAD_SIZE.

#pragma once

#include <etl/intrusive_list.h>
#include <etl/uncopyable.h>
#include <transport/AbstractTransportLayer.h>
#include <transport/ITransportMessageProvidingListener.h>
#include <transport/TransportConfiguration.h>
#include <transport/TransportMessage.h>

#include <cstdint>

namespace transport
{
/**
 * Routes diagnostic messages between one bus (CAN_0) and the local UDS server (SELFDIAG).
 */
class DiagTransportRouter
: public ITransportMessageProvidingListener
, public etl::uncopyable
{
public:
    DiagTransportRouter();
    DiagTransportRouter(DiagTransportRouter const&)            = delete;
    DiagTransportRouter& operator=(DiagTransportRouter const&) = delete;

    static uint8_t const NUM_BUFFERS
        = TransportConfiguration::NUMBER_OF_FULL_SIZE_TRANSPORT_MESSAGES;
    static uint8_t const NUM_FUNCTIONAL_BUFFERS = 2U;
    static uint16_t const BUFFER_SIZE           = TransportConfiguration::DIAG_PAYLOAD_SIZE;
    static uint16_t const FUNCTIONAL_BUFFER_SIZE
        = TransportConfiguration::MAX_FUNCTIONAL_MESSAGE_PAYLOAD_SIZE;

    void init();
    void shutdown();

    ErrorCode getTransportMessage(
        uint8_t srcBusId,
        uint16_t sourceAddress,
        uint16_t targetId,
        uint16_t size,
        ::etl::span<uint8_t const> const& peek,
        TransportMessage*& pTransportMessage) override;

    void releaseTransportMessage(TransportMessage& transportMessage) override;

    ReceiveResult messageReceived(
        uint8_t sourceBusId,
        TransportMessage& transportMessage,
        ITransportMessageProcessedListener* pNotificationListener) override;

    void dump() override;

    void addTransportLayer(AbstractTransportLayer& transportLayer);
    void removeTransportLayer(AbstractTransportLayer& transportLayer);

    /** Number of buffers currently handed out (diagnostics and tests). */
    uint8_t getLockedCount() const;

    /** Number of rejected buffer requests (too large or none free). */
    uint32_t getAllocationFailures() const { return _allocationFailures; }

private:
    void forwardMessageToTransportLayer(
        TransportMessage& transportMessage,
        uint8_t destBusId,
        ITransportMessageProcessedListener* pNotificationListener,
        AbstractTransportLayer::ErrorCode& result);

    typedef ::etl::intrusive_list<AbstractTransportLayer, etl::bidirectional_link<0>>
        TransportLayerList;

    bool _locked[NUM_BUFFERS];
    bool _functionalLocked[NUM_FUNCTIONAL_BUFFERS];
    uint8_t _buffer[NUM_BUFFERS][BUFFER_SIZE];
    uint8_t _functionalBuffer[NUM_FUNCTIONAL_BUFFERS][FUNCTIONAL_BUFFER_SIZE];
    TransportMessage _message[NUM_BUFFERS];
    TransportMessage _functionalMessage[NUM_FUNCTIONAL_BUFFERS];
    TransportLayerList _transportLayers;
    uint8_t _busIdToReply;
    uint32_t _allocationFailures;
};

} // namespace transport
