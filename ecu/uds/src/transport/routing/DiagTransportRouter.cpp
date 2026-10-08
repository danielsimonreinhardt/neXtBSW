/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// Derived from OpenBSW TransportRouterSimple (see header).

#include "transport/routing/DiagTransportRouter.h"

#include <async/Async.h>
#include <busid/BusId.h>
#include <transport/TpRouterLogger.h>

// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg): Logger API uses C-style varargs.

namespace transport
{
using ::util::logger::Logger;
using ::util::logger::TPROUTER;

DiagTransportRouter::DiagTransportRouter()
: _transportLayers(), _busIdToReply(::busid::SELFDIAG), _allocationFailures(0U)
{
    for (uint8_t i = 0U; i < NUM_BUFFERS; i++)
    {
        _locked[i] = false;
        _message[i].init(_buffer[i], BUFFER_SIZE);
    }
    for (uint8_t i = 0U; i < NUM_FUNCTIONAL_BUFFERS; i++)
    {
        _functionalLocked[i] = false;
        _functionalMessage[i].init(_functionalBuffer[i], FUNCTIONAL_BUFFER_SIZE);
    }
}

void DiagTransportRouter::init() { _transportLayers.clear(); }

void DiagTransportRouter::shutdown() { _transportLayers.clear(); }

ITransportMessageProvidingListener::ErrorCode DiagTransportRouter::getTransportMessage(
    uint8_t const srcBusId,
    uint16_t const sourceAddress,
    uint16_t const targetId,
    uint16_t const size,
    ::etl::span<uint8_t const> const& /* peek */,
    TransportMessage*& pTransportMessage)
{
    Logger::debug(
        TPROUTER,
        "DiagTransportRouter::getTransportMessage : sourceAddress 0x%x, targetId 0x%x, size %d",
        sourceAddress,
        targetId,
        size);
    pTransportMessage = nullptr;

    ::async::LockType const lockGuard;
    uint16_t const targetId2Byte
        = TransportConfiguration::is1ByteDiagAddressBus(srcBusId)
              ? TransportConfiguration::convert1ByteAddressTo2Byte(targetId)
              : targetId;

    if (TransportConfiguration::isFunctionalAddress(targetId2Byte)
        && (size <= FUNCTIONAL_BUFFER_SIZE))
    {
        for (uint8_t i = 0U; i < NUM_FUNCTIONAL_BUFFERS; i++)
        {
            if (!_functionalLocked[i])
            {
                _functionalMessage[i].init(_functionalBuffer[i], FUNCTIONAL_BUFFER_SIZE);
                pTransportMessage    = &_functionalMessage[i];
                _functionalLocked[i] = true;
                return ErrorCode::TPMSG_OK;
            }
        }
    }

    if (size > BUFFER_SIZE)
    {
        ++_allocationFailures;
        return ITransportMessageProvider::ErrorCode::TPMSG_SIZE_TOO_LARGE;
    }

    for (uint8_t i = 0U; i < NUM_BUFFERS; i++)
    {
        if (!_locked[i])
        {
            _message[i].init(_buffer[i], BUFFER_SIZE);
            pTransportMessage = &_message[i];
            _locked[i]        = true;
            return ErrorCode::TPMSG_OK;
        }
    }

    ++_allocationFailures;
    return ErrorCode::TPMSG_NO_MSG_AVAILABLE;
}

void DiagTransportRouter::releaseTransportMessage(TransportMessage& transportMessage)
{
    ::async::LockType const lockGuard;
    for (uint8_t i = 0U; i < NUM_BUFFERS; i++)
    {
        if (&transportMessage == &_message[i])
        {
            _locked[i] = false;
            return;
        }
    }
    for (uint8_t i = 0U; i < NUM_FUNCTIONAL_BUFFERS; i++)
    {
        if (&transportMessage == &_functionalMessage[i])
        {
            _functionalLocked[i] = false;
            return;
        }
    }
}

ITransportMessageProvidingListener::ReceiveResult DiagTransportRouter::messageReceived(
    uint8_t const sourceBusId,
    TransportMessage& transportMessage,
    ITransportMessageProcessedListener* const pNotificationListener)
{
    AbstractTransportLayer::ErrorCode result(AbstractTransportLayer::ErrorCode::TP_OK);

    if (TransportConfiguration::is1ByteDiagAddressBus(sourceBusId))
    {
        transportMessage.setSourceAddress(
            TransportConfiguration::convert1ByteAddressTo2Byte(transportMessage.getSourceId()));
        transportMessage.setTargetAddress(
            TransportConfiguration::convert1ByteAddressTo2Byte(transportMessage.getTargetId()));
    }

    // Every bus except the local one is external: a request from there goes to the local UDS server and
    // its response back to the bus it came from. Which buses exist follows from the project (ARCH-07).
    if (sourceBusId != ::busid::SELFDIAG)
    {
        _busIdToReply = sourceBusId;
        forwardMessageToTransportLayer(
            transportMessage, ::busid::SELFDIAG, pNotificationListener, result);
    }
    else if (_busIdToReply != ::busid::SELFDIAG)
    {
        forwardMessageToTransportLayer(
            transportMessage, _busIdToReply, pNotificationListener, result);
    }
    else
    {
        result = AbstractTransportLayer::ErrorCode::TP_SEND_FAIL;
    }

    if (result != AbstractTransportLayer::ErrorCode::TP_OK)
    {
        return ReceiveResult::RECEIVED_ERROR;
    }
    return ReceiveResult::RECEIVED_NO_ERROR;
}

void DiagTransportRouter::dump() {}

void DiagTransportRouter::addTransportLayer(AbstractTransportLayer& transportLayer)
{
    for (auto itr = _transportLayers.begin(); itr != _transportLayers.end(); ++itr)
    {
        if (itr->getBusId() == transportLayer.getBusId())
        {
            Logger::error(
                TPROUTER,
                "TpLayer for bus %s must not be registered multiple times",
                ::common::busid::BusIdTraits::getName(itr->getBusId()));
            return;
        }
    }
    transportLayer.fProvidingListenerHelper.fpMessageListener = this;
    transportLayer.fProvidingListenerHelper.fpMessageProvider = this;
    _transportLayers.push_back(transportLayer);
}

void DiagTransportRouter::removeTransportLayer(AbstractTransportLayer& transportLayer)
{
    if (!_transportLayers.contains_node(transportLayer))
    {
        return;
    }
    transportLayer.fProvidingListenerHelper.fpMessageListener = nullptr;
    transportLayer.fProvidingListenerHelper.fpMessageProvider = nullptr;
    _transportLayers.erase(transportLayer);
}

uint8_t DiagTransportRouter::getLockedCount() const
{
    uint8_t count = 0U;
    for (uint8_t i = 0U; i < NUM_BUFFERS; i++)
    {
        count = static_cast<uint8_t>(count + (_locked[i] ? 1U : 0U));
    }
    for (uint8_t i = 0U; i < NUM_FUNCTIONAL_BUFFERS; i++)
    {
        count = static_cast<uint8_t>(count + (_functionalLocked[i] ? 1U : 0U));
    }
    return count;
}

void DiagTransportRouter::forwardMessageToTransportLayer(
    TransportMessage& transportMessage,
    uint8_t const destBusId,
    ITransportMessageProcessedListener* const pNotificationListener,
    AbstractTransportLayer::ErrorCode& result)
{
    if (TransportConfiguration::is1ByteDiagAddressBus(destBusId))
    {
        transportMessage.setSourceAddress(
            TransportConfiguration::convert2ByteAddressTo1Byte(transportMessage.getSourceId()));
        transportMessage.setTargetAddress(
            TransportConfiguration::convert2ByteAddressTo1Byte(transportMessage.getTargetId()));
    }

    for (auto itr = _transportLayers.begin(); itr != _transportLayers.end(); ++itr)
    {
        if (itr->getBusId() == destBusId)
        {
            result = itr->send(transportMessage, pNotificationListener);
            break;
        }
    }
}

} // namespace transport

// NOLINTEND(cppcoreguidelines-pro-type-vararg)
