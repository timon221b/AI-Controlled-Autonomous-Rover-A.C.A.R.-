#pragma once

#define ESPNOW_CHANNEL        1
#define ESPNOW_MAX_RETRIES    3
#define ESPNOW_RETRY_DELAY_MS 50

// Safety thresholds
#define OBSTACLE_STOP_CM      25
#define OBSTACLE_SLOW_CM      50
#define BATTERY_LOW_PCT       20
#define BATTERY_CRITICAL_PCT  10
#define HEARTBEAT_TIMEOUT_MS  8000

// Motion limits
#define MAX_DURATION_MS       8000
#define MAX_SPEED_PCT         100
#define DEFAULT_SPEED_PCT     60

// FreeRTOS stack sizes (words)
#define STACK_SAFETY      4096
#define STACK_ESPNOW      4096
#define STACK_MOTION      4096
#define STACK_ULTRASONIC  2048
#define STACK_TELEMETRY   2048
#define STACK_HEARTBEAT   2048

// Task priorities (higher = more urgent on ESP-IDF)
#define PRI_SAFETY       10
#define PRI_HEARTBEAT     9
#define PRI_ESPNOW_RX     7
#define PRI_ULTRASONIC    6
#define PRI_MOTION        5
#define PRI_TELEMETRY     3

// ── L298N Motor pins ──────────────────────────────────────────────
// Left motor
#define M_LEFT_IN1  25
#define M_LEFT_IN2  26
#define M_LEFT_EN   27    // PWM speed control

// Right motor
#define M_RIGHT_IN1 32
#define M_RIGHT_IN2 33
#define M_RIGHT_EN  14    // PWM speed control

// ── Ultrasonic (single, front-facing) ────────────────────────────
// GPIO4  → TRIG  (output capable)
// GPIO34 → ECHO  (input-only pin — correct for ECHO)
#define US_FRONT_TRIG  4
#define US_FRONT_ECHO  34

// Battery ADC (input-only pin — correct)
#define BATTERY_ADC_PIN 36