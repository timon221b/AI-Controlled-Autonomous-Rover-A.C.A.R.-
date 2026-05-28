#pragma once
#include <stdint.h>
#include <stddef.h>

inline uint16_t crc16_compute(const uint8_t* data, uint16_t length) {
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++)
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
    }
    return crc;
}

// Use offsetof so crc16 never has to be the last field
template<typename T>
inline bool packet_crc_valid(const T* pkt) {
    return crc16_compute((const uint8_t*)pkt, offsetof(T, crc16))
           == pkt->crc16;
}

template<typename T>
inline void packet_crc_set(T* pkt) {
    pkt->crc16 = crc16_compute((const uint8_t*)pkt, offsetof(T, crc16));
}