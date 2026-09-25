#include "queue.h"

// Circular buffer — head is the next-to-pop index, count tracks occupancy.
static Command s_buf[QUEUE_CAPACITY];
static uint8_t s_head = 0;
static uint8_t s_count = 0;

void queueClear() {
  s_head = 0;
  s_count = 0;
}

bool queuePush(const Command& cmd) {
  if (s_count >= QUEUE_CAPACITY) {
    return false;  // overflow — silently discard
  }
  s_buf[(s_head + s_count) % QUEUE_CAPACITY] = cmd;
  s_count++;
  return true;
}

bool queuePop(Command* out) {
  if (s_count == 0) {
    return false;
  }
  *out = s_buf[s_head];
  s_head = (uint8_t)((s_head + 1) % QUEUE_CAPACITY);
  s_count--;
  return true;
}

bool queueIsEmpty() {
  return s_count == 0;
}

uint8_t queueCount() {
  return s_count;
}

bool queuePeekTail(Command* out) {
  if (s_count == 0) {
    return false;
  }
  *out = s_buf[(s_head + s_count - 1) % QUEUE_CAPACITY];
  return true;
}

bool queueReplaceTail(const Command& cmd) {
  if (s_count == 0) {
    return false;
  }
  s_buf[(s_head + s_count - 1) % QUEUE_CAPACITY] = cmd;
  return true;
}
