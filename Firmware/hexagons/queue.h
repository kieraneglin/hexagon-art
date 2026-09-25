#pragma once
#include <stdint.h>

#include "comm.h"

// ---------------------------------------------------------------------------
// Command queue
// ---------------------------------------------------------------------------
//
// A fixed-capacity FIFO that holds staged commands awaiting a GO (G) trigger.
// All operations are O(1) and use no dynamic allocation.
//
// Typical usage:
//   queuePush(cmd)      — called for each stageable command received
//   queuePop(&cmd)      — called by the executor to dequeue one command
//   queueClear()        — called on CLEAR (C) or STOP (X)
//
// Capacity is set by QUEUE_CAPACITY. On overflow, push() returns false and
// the command is silently discarded (consistent with the comm error strategy).

#define QUEUE_CAPACITY 64

void queueClear();
bool queuePush(const Command& cmd);
bool queuePop(Command* out);
bool queueIsEmpty();
uint8_t queueCount();

// Inspect the most-recently-pushed entry without removing it.
// Returns false (and leaves *out untouched) if the queue is empty.
bool queuePeekTail(Command* out);

// Overwrite the most-recently-pushed entry in-place.
// Returns false (no-op) if the queue is empty.
bool queueReplaceTail(const Command& cmd);
