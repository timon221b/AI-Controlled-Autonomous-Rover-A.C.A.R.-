#include <Arduino.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "packet.h"
#include "crc16.h"
#include "config.h"

#define LED_PIN 2

uint8_t SLAVE_MAC[6] = {0x00, 0x70, 0x07, 0x3A, 0x3E, 0xC8};

static QueueHandle_t     txQueue;
static SemaphoreHandle_t ackSemaphore;
static volatile bool     lastSendSuccess = false;
static volatile bool     espnowConnected = false;

void onDataSent(const uint8_t *mac, esp_now_send_status_t status)
{
    lastSendSuccess = (status == ESP_NOW_SEND_SUCCESS);
    if (lastSendSuccess && !espnowConnected)
    {
        espnowConnected = true;
        digitalWrite(LED_PIN, HIGH);
    }
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(ackSemaphore, &woken);
    portYIELD_FROM_ISR(woken);
}

#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *data, int len)
#else
void onDataReceived(const uint8_t *mac, const uint8_t *data, int len)
#endif
{
    if (len != (int)MOTION_PACKET_SIZE) return;
    MotionPacket_t pkt;
    memcpy(&pkt, data, len);
    if (pkt.magic[0] != MAGIC_0 || pkt.magic[1] != MAGIC_1) return;

    if (pkt.packet_type == PKT_ACK_OK)
        Serial.printf("ACK:OK,%d\n", pkt.sequence_id);
    else if (pkt.packet_type == PKT_ACK_REJECTED)
        Serial.printf("ACK:REJECTED,%d\n", pkt.sequence_id);
    else if (pkt.packet_type == PKT_SAFE_STATE)
        Serial.printf("SAFE_STATE:%d\n", pkt.sequence_id);
}

void ledTask(void *pvParams)
{
    while (1)
    {
        if (!espnowConnected)
        {
            digitalWrite(LED_PIN, !digitalRead(LED_PIN));
            vTaskDelay(pdMS_TO_TICKS(300));
        }
        else
            vTaskDelay(pdMS_TO_TICKS(500));
    }
}

void serialRxTask(void *pvParams)
{
    MotionPacket_t pkt;
    uint8_t *buf = (uint8_t *)&pkt;
    while (1)
    {
        if (Serial.available() >= (int)MOTION_PACKET_SIZE)
        {
            size_t got = Serial.readBytes(buf, MOTION_PACKET_SIZE);
            if (got != MOTION_PACKET_SIZE)               { Serial.println("ERR:PARTIAL_PACKET"); continue; }
            if (buf[0] != MAGIC_0 || buf[1] != MAGIC_1) { Serial.println("ERR:BAD_MAGIC");       continue; }
            if (!packet_crc_valid(&pkt))                 { Serial.println("ERR:BAD_CRC");         continue; }
            if (xQueueSend(txQueue, &pkt, pdMS_TO_TICKS(100)) != pdTRUE)
                Serial.println("ERR:TX_QUEUE_FULL");
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void espnowTxTask(void *pvParams)
{
    MotionPacket_t pkt;
    while (1)
    {
        if (xQueueReceive(txQueue, &pkt, portMAX_DELAY) != pdTRUE) continue;
        bool acked = false;
        for (int attempt = 0; attempt < ESPNOW_MAX_RETRIES; attempt++)
        {
            esp_now_send(SLAVE_MAC, (uint8_t *)&pkt, MOTION_PACKET_SIZE);
            if (xSemaphoreTake(ackSemaphore, pdMS_TO_TICKS(200)))
                if (lastSendSuccess) { acked = true; break; }
            vTaskDelay(pdMS_TO_TICKS(ESPNOW_RETRY_DELAY_MS));
        }
        if (!acked) Serial.println("ERR:ESPNOW_SEND_FAILED");
    }
}

void heartbeatTask(void *pvParams)
{
    MotionPacket_t hb;
    memset(&hb, 0, sizeof(hb));
    hb.magic[0]    = MAGIC_0;
    hb.magic[1]    = MAGIC_1;
    hb.version     = PROTOCOL_VER;
    hb.packet_type = PKT_HEARTBEAT;
    hb.sequence_id = 0x00;
    packet_crc_set(&hb);
    while (1)
    {
        esp_now_send(SLAVE_MAC, (uint8_t *)&hb, MOTION_PACKET_SIZE);
        esp_err_t result = esp_now_send(SLAVE_MAC, (uint8_t *)&hb, MOTION_PACKET_SIZE);
        Serial.printf("HB_SEND:%d\n", result);
        vTaskDelay(pdMS_TO_TICKS(1000));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void setup()
{
    Serial.begin(115200);
    Serial.setTimeout(200);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    Serial.printf("MASTER MAC: %02X:%02X:%02X:%02X:%02X:%02X\n",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    txQueue      = xQueueCreate(32, sizeof(MotionPacket_t));
    ackSemaphore = xSemaphoreCreateBinary();

    // Clean WiFi init using pure IDF
    esp_wifi_stop();
    esp_wifi_deinit();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();
    uint8_t ch; wifi_second_chan_t sc;
    esp_wifi_get_channel(&ch, &sc);
    Serial.printf("MASTER CHANNEL: %d\n", ch);
    esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);

    if (esp_now_init() != ESP_OK)
    {
        Serial.println("FATAL:ESPNOW_INIT_FAILED");
        ESP.restart();
    }
    esp_now_register_send_cb(onDataSent);
    esp_now_register_recv_cb(onDataReceived);

    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, SLAVE_MAC, 6);
    peer.channel = 0;
    peer.encrypt = false;
    if (esp_now_add_peer(&peer) != ESP_OK)
    {
        Serial.println("FATAL:PEER_ADD_FAILED");
        ESP.restart();
    }

    xTaskCreatePinnedToCore(ledTask,       "LED",       1024,            nullptr, 1,             nullptr, 0);
    xTaskCreatePinnedToCore(serialRxTask,  "SerialRX",  STACK_ESPNOW,    nullptr, PRI_ESPNOW_RX, nullptr, 1);
    xTaskCreatePinnedToCore(espnowTxTask,  "ESPNowTX",  STACK_ESPNOW,    nullptr, PRI_MOTION,    nullptr, 0);
    xTaskCreatePinnedToCore(heartbeatTask, "Heartbeat", STACK_HEARTBEAT, nullptr, PRI_HEARTBEAT, nullptr, 0);

    Serial.println("MASTER:READY");
}

void loop() { vTaskDelay(portMAX_DELAY); }