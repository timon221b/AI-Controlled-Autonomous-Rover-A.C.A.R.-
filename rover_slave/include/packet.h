#pragma once
#include <stdint.h>

#define MAGIC_0          0xAB
#define MAGIC_1          0xCD
#define PROTOCOL_VER     0x02
#define MAX_SEQ_STEPS    16

// Packet types — master→slave
#define PKT_MOTION_STEP  0x01
#define PKT_SEQ_END      0x02
#define PKT_ABORT        0x03
#define PKT_HEARTBEAT    0x04
#define PKT_RESUME       0x05

// Packet types — slave→master
#define PKT_ACK_OK       0x10
#define PKT_ACK_REJECTED 0x11
#define PKT_SAFE_STATE   0x12
#define PKT_TELEMETRY    0x20

// Rejection reasons (carried in sequence_id field of ACK_REJECTED)
#define REJ_BAD_CRC      0x01
#define REJ_SAFETY_ACTIVE 0x02
#define REJ_QUEUE_FULL   0x03
#define REJ_BAD_VERSION  0x04

typedef enum : uint8_t {
    CMD_MOVE_FORWARD  = 0x01,
    CMD_MOVE_BACKWARD = 0x02,
    CMD_TURN_LEFT     = 0x03,
    CMD_TURN_RIGHT    = 0x04,
    CMD_SPIN_CW       = 0x05,
    CMD_SPIN_CCW      = 0x06,
    CMD_STOP          = 0x07,
    CMD_PAUSE         = 0x08,
} CommandType_t;

#define FLAG_IS_LAST_STEP    0x01
#define FLAG_REQUIRES_ACK    0x02
#define FLAG_OVERRIDE_SPEED  0x04

typedef enum : uint8_t {
    SAFE_NOMINAL        = 0x00,
    SAFE_OBSTACLE       = 0x01,
    SAFE_LOW_BATTERY    = 0x02,
    SAFE_HEARTBEAT_LOST = 0x03,
    SAFE_PACKET_ERROR   = 0x04,
    SAFE_ESTOP          = 0x05,
} SafetyState_t;

#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];
    uint8_t  version;
    uint8_t  packet_type;
    uint8_t  sequence_id;
    uint8_t  total_steps;
    uint8_t  step_index;
    uint8_t  command;
    uint16_t duration_ms;
    uint8_t  speed_pct;
    uint8_t  flags;
    uint16_t crc16;
} MotionPacket_t;  // 14 bytes

typedef struct {
    uint8_t  magic[2];
    uint8_t  version;
    uint8_t  packet_type;
    uint8_t  safety_state;
    uint8_t  battery_pct;
    uint16_t obstacle_front_cm;
    uint16_t obstacle_rear_cm;
    uint8_t  current_step;
    uint8_t  total_steps;
    uint16_t crc16;
} TelemetryPacket_t;  // 15 bytes
#pragma pack(pop)

#define MOTION_PACKET_SIZE    sizeof(MotionPacket_t)
#define TELEMETRY_PACKET_SIZE sizeof(TelemetryPacket_t)