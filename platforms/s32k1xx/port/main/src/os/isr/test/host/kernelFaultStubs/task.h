/* SYS-80 host test stub of the kernel's task API: a task control block with number and name, and the one kernel
 * function the stack overflow hook calls. Nothing else of the kernel exists here, so the reaction path cannot call the
 * kernel (it would not link). */
#pragma once

#include <cstdint>

struct TestTaskControlBlock
{
    uint32_t number;
    char name[10];
};

using TaskHandle_t = TestTaskControlBlock*;
using UBaseType_t  = unsigned long;

namespace test_kernel
{
extern int taskNumberReads;
}

inline UBaseType_t uxTaskGetTaskNumber(TaskHandle_t const task)
{
    ++::test_kernel::taskNumberReads;
    return static_cast<UBaseType_t>(task->number);
}
