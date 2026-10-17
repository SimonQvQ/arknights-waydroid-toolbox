#ifndef WAYDROID_CAPTURE_PROTOCOL_H
#define WAYDROID_CAPTURE_PROTOCOL_H
#include <stdint.h>
#define CAP_MAGIC 0x57444331u
#define CAP_WIDTH 1280u
#define CAP_HEIGHT 720u
#define CAP_BYTES (CAP_WIDTH * CAP_HEIGHT * 4u)
/* Same-machine IPC, little endian. Every C request captures a new frame. */
typedef struct {
    uint32_t magic, status, width, height, bytes, reserved;
    uint64_t sequence, capture_start_ns, capture_end_ns;
} CapHeader;
#endif
