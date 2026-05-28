#pragma once
#include <Arduino.h>
#include "config.h"

inline void motors_stop_all();

inline void motors_init() {
    pinMode(M_LEFT_IN1,  OUTPUT);
    pinMode(M_LEFT_IN2,  OUTPUT);
    pinMode(M_RIGHT_IN1, OUTPUT);
    pinMode(M_RIGHT_IN2, OUTPUT);
    motors_stop_all();
}

inline void motors_stop_all() {
    digitalWrite(M_LEFT_IN1,  HIGH); digitalWrite(M_LEFT_IN2,  HIGH);
    digitalWrite(M_RIGHT_IN1, HIGH); digitalWrite(M_RIGHT_IN2, HIGH);
}

inline void motors_forward(uint8_t) {
    digitalWrite(M_LEFT_IN1,  HIGH); digitalWrite(M_LEFT_IN2,  LOW);
    digitalWrite(M_RIGHT_IN1, HIGH); digitalWrite(M_RIGHT_IN2, LOW);
}

inline void motors_backward(uint8_t) {
    digitalWrite(M_LEFT_IN1,  LOW); digitalWrite(M_LEFT_IN2,  HIGH);
    digitalWrite(M_RIGHT_IN1, LOW); digitalWrite(M_RIGHT_IN2, HIGH);
}

inline void motors_turn_left(uint8_t) {
    digitalWrite(M_LEFT_IN1,  HIGH); digitalWrite(M_LEFT_IN2,  HIGH);
    digitalWrite(M_RIGHT_IN1, HIGH); digitalWrite(M_RIGHT_IN2, LOW);
}

inline void motors_turn_right(uint8_t) {
    digitalWrite(M_LEFT_IN1,  HIGH); digitalWrite(M_LEFT_IN2,  LOW);
    digitalWrite(M_RIGHT_IN1, HIGH); digitalWrite(M_RIGHT_IN2, HIGH);
}

inline void motors_spin_cw(uint8_t) {
    digitalWrite(M_LEFT_IN1,  HIGH); digitalWrite(M_LEFT_IN2,  LOW);
    digitalWrite(M_RIGHT_IN1, LOW);  digitalWrite(M_RIGHT_IN2, HIGH);
}

inline void motors_spin_ccw(uint8_t) {
    digitalWrite(M_LEFT_IN1,  LOW);  digitalWrite(M_LEFT_IN2,  HIGH);
    digitalWrite(M_RIGHT_IN1, HIGH); digitalWrite(M_RIGHT_IN2, LOW);
}