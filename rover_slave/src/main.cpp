#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include <Arduino.h>
#include <esp_now.h>
#include <atomic>
#include <esp_wifi.h>
#include "packet.h"
#include "crc16.h"
#include "config.h"
#include "motors.h"

uint8_t MASTER_MAC[6] = {0xB8, 0xD6, 0x1A, 0x67, 0x76, 0x88};

#define LED_PIN 2

static QueueHandle_t motionQueue;
static QueueHandle_t espnowRxQueue;

static std::atomic<SafetyState_t> safetyState{SAFE_NOMINAL};
static std::atomic<uint32_t> lastHeartbeat{0};
static volatile bool motionCancelled   = false;
static volatile bool heartbeatReceived = false;
static volatile uint8_t currentStep   = 0;
static volatile uint8_t totalSteps    = 0;
static volatile uint32_t queueOverflowCount = 0;

void ledTask(void *pvParams)
{
    while (1)
    {
        digitalWrite(LED_PIN, heartbeatReceived ? HIGH : !digitalRead(LED_PIN));
        vTaskDelay(pdMS_TO_TICKS(heartbeatReceived ? 500 : 300));
    }
}

void enterSafeState(SafetyState_t reason)
{
    safetyState.store(reason, std::memory_order_release);
    motors_stop_all();
    motionCancelled = true;

    MotionPacket_t alert;
    memset(&alert, 0, sizeof(alert));
    alert.magic[0]    = MAGIC_0;
    alert.magic[1]    = MAGIC_1;
    alert.version     = PROTOCOL_VER;
    alert.packet_type = PKT_SAFE_STATE;
    alert.sequence_id = (uint8_t)reason;
    packet_crc_set(&alert);
    esp_now_send(MASTER_MAC, (uint8_t *)&alert, MOTION_PACKET_SIZE);
    Serial.printf("SAFE_STATE:%d\n", (int)reason);
}

bool isSafeToMove()
{
    return safetyState.load(std::memory_order_acquire) == SAFE_NOMINAL;
}

bool validateMotionPacket(const MotionPacket_t *pkt)
{
    if (pkt->magic[0] != MAGIC_0 || pkt->magic[1] != MAGIC_1) return false;
    if (pkt->version != PROTOCOL_VER)                          return false;
    if (pkt->speed_pct > MAX_SPEED_PCT)                        return false;
    if (pkt->duration_ms > MAX_DURATION_MS)                    return false;
    if (pkt->total_steps > MAX_SEQ_STEPS)                      return false;
    if (pkt->packet_type == PKT_MOTION_STEP &&
        pkt->step_index >= pkt->total_steps)                   return false;
    if (!packet_crc_valid(pkt))                                return false;
    return true;
}

void sendAck(uint8_t ack_type, uint8_t seq_id, uint8_t reason = 0)
{
    MotionPacket_t ack;
    memset(&ack, 0, sizeof(ack));
    ack.magic[0]    = MAGIC_0;
    ack.magic[1]    = MAGIC_1;
    ack.version     = PROTOCOL_VER;
    ack.packet_type = ack_type;
    ack.sequence_id = seq_id;
    ack.step_index  = reason;
    packet_crc_set(&ack);
    esp_now_send(MASTER_MAC, (uint8_t *)&ack, MOTION_PACKET_SIZE);
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
    BaseType_t woken = pdFALSE;
    if (xQueueSendFromISR(espnowRxQueue, &pkt, &woken) != pdTRUE)
        queueOverflowCount++;
    portYIELD_FROM_ISR(woken);
}

void espnowRxTask(void *pvParams)
{
    MotionPacket_t pkt;
    while (1)
    {
        if (xQueueReceive(espnowRxQueue, &pkt, portMAX_DELAY) != pdTRUE)
            continue;

        if (pkt.packet_type == PKT_HEARTBEAT)
        {
            lastHeartbeat.store(millis(), std::memory_order_release);
            heartbeatReceived = true;
            if (safetyState.load(std::memory_order_acquire) == SAFE_HEARTBEAT_LOST)
            {
                safetyState.store(SAFE_NOMINAL, std::memory_order_release);
                motionCancelled = false;
                Serial.println("STATE:HB_RECOVERED");
            }
            continue;
        }
        if (pkt.packet_type == PKT_RESUME)
        {
            if (packet_crc_valid(&pkt))
            {
                safetyState.store(SAFE_NOMINAL, std::memory_order_release);
                motionCancelled = false;
                Serial.println("STATE:RESUMED");
            }
            continue;
        }
        if (pkt.packet_type == PKT_ABORT)
        {
            enterSafeState(SAFE_ESTOP);
            sendAck(PKT_ACK_OK, pkt.sequence_id);
            continue;
        }
        if (pkt.packet_type == PKT_MOTION_STEP)
        {
            if (!validateMotionPacket(&pkt))
            {
                sendAck(PKT_ACK_REJECTED, pkt.sequence_id, REJ_BAD_CRC);
                enterSafeState(SAFE_PACKET_ERROR);
                continue;
            }
            if (!isSafeToMove())
            {
                sendAck(PKT_ACK_REJECTED, pkt.sequence_id, REJ_SAFETY_ACTIVE);
                continue;
            }
            if (xQueueSend(motionQueue, &pkt, pdMS_TO_TICKS(50)) != pdTRUE)
                sendAck(PKT_ACK_REJECTED, pkt.sequence_id, REJ_QUEUE_FULL);
            else
                sendAck(PKT_ACK_OK, pkt.sequence_id);
        }
    }
}

void motionExecutorTask(void *pvParams)
{
    MotionPacket_t pkt;
    while (1)
    {
        if (motionCancelled)
        {
            motionCancelled = false;
            MotionPacket_t discard;
            while (xQueueReceive(motionQueue, &discard, 0) == pdTRUE) {}
            motors_stop_all();
        }

        if (xQueueReceive(motionQueue, &pkt, pdMS_TO_TICKS(100)) != pdTRUE)
            continue;

        if (!isSafeToMove()) { motors_stop_all(); continue; }

        currentStep = pkt.step_index;
        totalSteps  = pkt.total_steps;
        Serial.printf("EXEC step %d/%d cmd=0x%02X dur=%dms\n",
                      pkt.step_index + 1, pkt.total_steps,
                      pkt.command, pkt.duration_ms);

        switch ((CommandType_t)pkt.command)
        {
        case CMD_MOVE_FORWARD:  motors_forward(pkt.speed_pct);    break;
        case CMD_MOVE_BACKWARD: motors_backward(pkt.speed_pct);   break;
        case CMD_TURN_LEFT:     motors_turn_left(pkt.speed_pct);  break;
        case CMD_TURN_RIGHT:    motors_turn_right(pkt.speed_pct); break;
        case CMD_SPIN_CW:       motors_spin_cw(pkt.speed_pct);    break;
        case CMD_SPIN_CCW:      motors_spin_ccw(pkt.speed_pct);   break;
        case CMD_STOP:          motors_stop_all(); continue;
        case CMD_PAUSE:         motors_stop_all(); break;
        default:                motors_stop_all(); continue;
        }

        uint32_t elapsed = 0;
        while (elapsed < pkt.duration_ms)
        {
            if (safetyState.load(std::memory_order_acquire) != SAFE_NOMINAL)
            {
                motors_stop_all();
                motionCancelled = true;
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(50));
            elapsed += 50;
        }
        motors_stop_all();
    }
}

void safetyMonitorTask(void *pvParams)
{
    Serial.println("SAFETY:WAITING_FOR_FIRST_HB");
    while (!heartbeatReceived) vTaskDelay(pdMS_TO_TICKS(100));
    Serial.println("SAFETY:WATCHDOG_ARMED");

    while (1)
    {
        if (safetyState.load(std::memory_order_acquire) == SAFE_NOMINAL)
        {
            if ((millis() - lastHeartbeat.load(std::memory_order_acquire)) > HEARTBEAT_TIMEOUT_MS)
                enterSafeState(SAFE_HEARTBEAT_LOST);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void telemetryTask(void *pvParams)
{
    TelemetryPacket_t pkt;
    while (1)
    {
        pkt.magic[0]          = MAGIC_0;
        pkt.magic[1]          = MAGIC_1;
        pkt.version           = PROTOCOL_VER;
        pkt.packet_type       = PKT_TELEMETRY;
        pkt.safety_state      = (uint8_t)safetyState.load();
        pkt.battery_pct       = 100;
        pkt.obstacle_front_cm = 999;
        pkt.obstacle_rear_cm  = 999;
        pkt.current_step      = currentStep;
        pkt.total_steps       = totalSteps;
        packet_crc_set(&pkt);
        esp_now_send(MASTER_MAC, (uint8_t *)&pkt, TELEMETRY_PACKET_SIZE);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

void setup()
{
    // Disable brownout detector — prevents resets from motor current spikes
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

    Serial.begin(115200);

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    Serial.printf("SLAVE MAC: %02X:%02X:%02X:%02X:%02X:%02X\n",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    motionQueue   = xQueueCreate(MAX_SEQ_STEPS, sizeof(MotionPacket_t));
    espnowRxQueue = xQueueCreate(32, sizeof(MotionPacket_t));

    motors_init();
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // Clean WiFi init using pure IDF
    esp_wifi_stop();
    esp_wifi_deinit();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();
    esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);

    if (esp_now_init() != ESP_OK)
    {
        Serial.println("FATAL:ESPNOW_INIT_FAILED");
        ESP.restart();
    }
    esp_now_register_recv_cb(onDataReceived);

    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, MASTER_MAC, 6);
    peer.channel = 0;
    peer.encrypt = false;
    if (esp_now_add_peer(&peer) != ESP_OK)
    {
        Serial.println("FATAL:PEER_ADD_FAILED");
        ESP.restart();
    }

    lastHeartbeat.store(0, std::memory_order_release);

    xTaskCreatePinnedToCore(safetyMonitorTask,  "Safety",    STACK_SAFETY,    nullptr, PRI_SAFETY,    nullptr, 0);
    xTaskCreatePinnedToCore(espnowRxTask,       "ESPNowRX",  STACK_ESPNOW,    nullptr, PRI_ESPNOW_RX, nullptr, 1);
    xTaskCreatePinnedToCore(motionExecutorTask, "Motion",    STACK_MOTION,    nullptr, PRI_MOTION,    nullptr, 1);
    xTaskCreatePinnedToCore(telemetryTask,      "Telemetry", 4096,            nullptr, PRI_TELEMETRY, nullptr, 1);
    xTaskCreatePinnedToCore(ledTask,            "LED",       2048,            nullptr, 1,             nullptr, 0);

    Serial.println("SLAVE:READY");
}

void loop() { vTaskDelay(portMAX_DELAY); }