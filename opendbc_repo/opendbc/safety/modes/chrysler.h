#pragma once

#include "opendbc/safety/safety_declarations.h"

typedef struct {
  const unsigned int EPS_2;
  const unsigned int ESP_1;
  const unsigned int ESP_8;
  const unsigned int ECM_5;
  const unsigned int DAS_3;
  const unsigned int DAS_6;
  const unsigned int LKAS_COMMAND;
  const unsigned int CRUISE_BUTTONS;
  const unsigned int LKAS_HEARTBIT;
  const unsigned int Center_Stack_2;
  const unsigned int TRACTION_BUTTON;
} ChryslerAddrs;

typedef enum {
  CHRYSLER_RAM_DT,
  CHRYSLER_RAM_HD,
  CHRYSLER_PACIFICA,  // plus Jeep
} ChryslerPlatform;
static ChryslerPlatform chrysler_platform;
static const ChryslerAddrs *chrysler_addrs;

// RamPilot longitudinal (Ram DT only, param CHRYSLER_PARAM_RAMPILOT_LONG). The factory cruise module (DASM, bus 2)
// keeps computing DAS_3. While controls are allowed, the comma's DAS_3 replaces the factory frame on bus 0, and it
// may only add braking to a fresh factory frame: never less deceleration, never more engine torque. A factory frame
// with a deceleration request or brake preparation is never replaced: it reaches bus 0 itself.
#define CHRYSLER_ACC_DECEL_MIN 2558         // ACC_DECEL raw, -3.5 m/s^2 (0.004885 m/s^2 per bit, -16 m/s^2 offset)
#define CHRYSLER_ACC_DECEL_ZERO 3275        // ACC_DECEL raw, -0.002 m/s^2: the weakest deceleration the comma requests
#define CHRYSLER_TORQUE_INACTIVE 2000       // ENGINE_TORQUE_REQUEST raw, 0 Nm (0.25 Nm per bit, -500 Nm offset)
#define CHRYSLER_DAS_3_MAX_AGE 40000U       // us, newest factory frame a comma frame may copy: two frames at 50 Hz
#define CHRYSLER_DAS_3_PREV_MAX_AGE 60000U  // us, the factory frame before it
#define CHRYSLER_DAS_3_STOCK_EXPIRY 100000U // us, a stored factory frame older than this is forgotten
#define CHRYSLER_DAS_3_TX_TIMEOUT 60000U    // us, after the comma's last attempt the factory frame is passed again
#define CHRYSLER_DAS_3_DELIVERY_TIMEOUT 60000U  // us, longest time with no DAS_3 on bus 0 while the comma replaces it
#define CHRYSLER_DAS_3_MAX_REFUSALS 3U      // refused attempts in a row that hand bus 0 back to the factory
#define CHRYSLER_DAS_3_EMERGENCY_TIME 2000000U  // us, factory frames pass directly after a factory emergency frame
// DAS_3 byte 6 bits that make a factory frame an emergency frame: COLLISION_BRK_PREP (0x01) and ACC_BRK_PREP (0x02).
// In 9.9 h of recorded drives ACC_BRK_PREP appeared once, with cruise off and the driver braking hard.
#define CHRYSLER_DAS_3_EMERGENCY_BITS 0x03U
// ACC_DECEL raw, -2.5 m/s^2: a factory deceleration request stronger than this is an emergency frame. The factory's
// hardest request in 9.9 h of recorded drives was raw 2834 (-2.16 m/s^2).
#define CHRYSLER_ACC_DECEL_EMERGENCY 2764
// DAS_3 byte 6 bits of brake preparation, COLLISION_BRK_PREP (0x01) and ACC_BRK_PREP (0x02): a factory frame with
// either one, or with a deceleration request, always reaches bus 0 itself
#define CHRYSLER_DAS_3_BRAKE_PREP_BITS 0x03U

typedef struct {
  uint8_t data[8];        // bytes 0-7 (byte 7 is the checksum)
  uint8_t counter;        // COUNTER
  int engine_torque;      // ENGINE_TORQUE_REQUEST raw: 0.25 Nm per bit, -500 Nm offset
  int engine_torque_max;  // ENGINE_TORQUE_REQUEST_MAX
  int accel;              // ACC_DECEL raw: 0.004885 m/s^2 per bit, -16 m/s^2 offset
  int decel_req;          // ACC_DECEL_REQ
} ChryslerDas3;

typedef struct {
  ChryslerDas3 frame;
  uint32_t ts;   // receive time [us]
  bool seen;
  bool blocked;  // the fwd hook kept it off bus 0
} ChryslerDas3Stock;

static bool chrysler_rampilot_long = false;
static ChryslerDas3Stock chrysler_das_3_stock[2];        // valid factory DAS_3 frames: [0] newest, [1] the one before
static bool chrysler_das_3_stock_forwarded = false;     // the fwd hook passed the factory DAS_3 the rx hook gets next
static bool chrysler_das_3_stock_blocked = false;       // the fwd hook blocked the factory DAS_3 the rx hook gets next
static bool chrysler_das_3_bus0_counter_valid = false;  // a DAS_3 reached bus 0 since init
static uint8_t chrysler_das_3_bus0_counter = 0U;         // COUNTER of the last DAS_3 on bus 0, forwarded or the comma's
static uint32_t chrysler_das_3_bus0_ts = 0U;             // time it reached bus 0 [us]
static bool chrysler_das_3_tx_seen = false;             // the comma tried to send a DAS_3 within CHRYSLER_DAS_3_TX_TIMEOUT
static uint32_t chrysler_das_3_tx_ts = 0U;               // time of its last attempt, accepted or not [us]
static uint32_t chrysler_das_3_refusals = 0U;            // comma DAS_3 refused in a row while controls are allowed
static bool chrysler_das_3_fallback = false;            // latched by refusals or the delivery timeout; ends the engagement
static bool chrysler_das_3_emergency_seen = false;      // a factory emergency frame arrived within CHRYSLER_DAS_3_EMERGENCY_TIME
static uint32_t chrysler_das_3_emergency_ts = 0U;        // receive time of the last one [us]

static uint32_t chrysler_get_checksum(const CANPacket_t *msg) {
  int checksum_byte = GET_LEN(msg) - 1U;
  return (uint8_t)(msg->data[checksum_byte]);
}

static uint32_t chrysler_compute_checksum(const CANPacket_t *msg) {
  // TODO: clean this up
  // http://illmatics.com/Remote%20Car%20Hacking.pdf
  uint8_t checksum = 0xFFU;
  int len = GET_LEN(msg);
  for (int j = 0; j < (len - 1); j++) {
    uint8_t shift = 0x80U;
    uint8_t curr = (uint8_t)msg->data[j];
    for (int i=0; i<8; i++) {
      uint8_t bit_sum = curr & shift;
      uint8_t temp_chk = checksum & 0x80U;
      if (bit_sum != 0U) {
        bit_sum = 0x1C;
        if (temp_chk != 0U) {
          bit_sum = 1;
        }
        checksum = checksum << 1;
        temp_chk = checksum | 1U;
        bit_sum ^= temp_chk;
      } else {
        if (temp_chk != 0U) {
          bit_sum = 0x1D;
        }
        checksum = checksum << 1;
        bit_sum ^= checksum;
      }
      checksum = bit_sum;
      shift = shift >> 1;
    }
  }
  return (uint8_t)(~checksum);
}

static uint8_t chrysler_get_counter(const CANPacket_t *msg) {
  return (uint8_t)(msg->data[6] >> 4);
}

static void chrysler_das_3_decode(const CANPacket_t *msg, ChryslerDas3 *das_3) {
  for (int i = 0; i < 8; i++) {
    das_3->data[i] = msg->data[i];
  }
  das_3->counter = msg->data[6] >> 4;
  das_3->engine_torque = ((msg->data[0] & 0x1FU) << 8) | msg->data[1];
  das_3->engine_torque_max = msg->data[0] >> 7;
  das_3->accel = ((msg->data[2] & 0xFU) << 8) | msg->data[3];
  das_3->decel_req = (msg->data[4] >> 4) & 0x7U;
}

// Clear the seen flags of timestamps older than their window, before reading the flags. A cleared flag stays cleared
// until a new event, so an old timestamp never reads as fresh again when the 32-bit microsecond timer wraps (every
// 71.6 minutes).
static void chrysler_das_3_expire(void) {
  const uint32_t ts = microsecond_timer_get();
  if (get_ts_elapsed(ts, chrysler_das_3_tx_ts) > CHRYSLER_DAS_3_TX_TIMEOUT) {
    chrysler_das_3_tx_seen = false;
  }
  if (get_ts_elapsed(ts, chrysler_das_3_emergency_ts) > CHRYSLER_DAS_3_EMERGENCY_TIME) {
    chrysler_das_3_emergency_seen = false;
  }
  for (int i = 0; i < 2; i++) {
    if (get_ts_elapsed(ts, chrysler_das_3_stock[i].ts) > CHRYSLER_DAS_3_STOCK_EXPIRY) {
      chrysler_das_3_stock[i].seen = false;
    }
  }
}

// the refusal count and latch last only while controls are allowed: the next engagement starts clean
static void chrysler_das_3_fallback_update(void) {
  if (!controls_allowed) {
    chrysler_das_3_refusals = 0U;
    chrysler_das_3_fallback = false;
  }
}

// Hand bus 0 back to the factory: the latch ends the engagement. With controls not allowed the fwd hook passes the
// factory DAS_3 and the host sees controls not allowed; engaging again needs a new factory ACC_ACTIVE rising edge.
static void chrysler_das_3_fallback_set(void) {
  chrysler_das_3_fallback = true;
  controls_allowed = false;
}

// a DAS_3 with this COUNTER moves the truck's DAS_3 COUNTER forward: 1 to 7 steps ahead of the last DAS_3 on bus 0
// (the forward half of the 4-bit ring), or no DAS_3 has reached bus 0 yet. A repeat (0) or a step back (8 to 15) doesn't.
static bool chrysler_das_3_counter_ahead(uint8_t counter) {
  const uint32_t step = ((uint32_t)counter + 16U - (uint32_t)chrysler_das_3_bus0_counter) & 0xFU;
  return !chrysler_das_3_bus0_counter_valid || ((step >= 1U) && (step <= 7U));
}

// RamPilot longitudinal: the comma's DAS_3 may only add braking to a fresh factory DAS_3
static bool chrysler_rampilot_long_das_3_allowed(const CANPacket_t *msg) {
  // Bits the comma copies unchanged from the factory frame: everything in bytes 0-6 except ENGINE_TORQUE_REQUEST,
  // ENGINE_TORQUE_REQUEST_MAX, ACC_DECEL and ACC_DECEL_REQ, including the bits no DBC signal defines
  const uint8_t CHRYSLER_DAS_3_COPY_MASK[7] = {
    0x60U,  // ACC_STANDSTILL, ACC_GO
    0x00U,  // ENGINE_TORQUE_REQUEST
    0xF0U,  // ACC_AVAILABLE, ACC_ACTIVE, undefined bit 22, DISABLE_FUEL_SHUTOFF
    0x00U,  // ACC_DECEL
    0x8FU,  // GR_MAX_REQ, undefined bit 39
    0xFFU,  // undefined bits 40-45, ACC_FAULTED
    0xFFU,  // COLLISION_BRK_PREP, ACC_BRK_PREP, undefined bits 50-51, COUNTER
  };

  ChryslerDas3 cmd;
  chrysler_das_3_decode(msg, &cmd);

  // Match the comma's frame by COUNTER to a fresh factory frame: the newest, or the one before it when the host
  // answered one frame late. Every rule below is checked against the matched factory frame.
  const ChryslerDas3Stock *latest = &chrysler_das_3_stock[0];
  const ChryslerDas3Stock *previous = &chrysler_das_3_stock[1];
  const uint32_t ts = microsecond_timer_get();
  const uint32_t latest_age = get_ts_elapsed(ts, latest->ts);
  const uint32_t previous_age = get_ts_elapsed(ts, previous->ts);
  const bool latest_fresh = latest->seen && (latest_age <= CHRYSLER_DAS_3_MAX_AGE);
  const bool previous_fresh = latest_fresh && previous->seen && (previous_age <= CHRYSLER_DAS_3_PREV_MAX_AGE);

  bool matched = false;
  const ChryslerDas3Stock *match = latest;
  if (latest_fresh && (cmd.counter == latest->frame.counter)) {
    matched = true;
  } else if (previous_fresh && (cmd.counter == previous->frame.counter)) {
    matched = true;
    match = previous;
  } else {
  }
  const ChryslerDas3 *stock = &match->frame;

  bool exact = true;  // byte for byte the matched factory frame, checksum included
  for (int i = 0; i < 8; i++) {
    if (cmd.data[i] != stock->data[i]) {
      exact = false;
    }
  }

  // after repeated refusals the factory has bus 0 until controls are not allowed
  bool violation = !chrysler_rampilot_long || !matched || chrysler_das_3_fallback;

  // the truck's modules discard a frame with a bad checksum: that is no replacement for the factory frame
  violation |= chrysler_compute_checksum(msg) != chrysler_get_checksum(msg);

  // the truck's DAS_3 COUNTER only moves forward: missed frames are tolerated, repeats and backward steps are refused
  violation |= !chrysler_das_3_counter_ahead(cmd.counter);

  // While factory emergency frames pass directly to bus 0, and while controls are not allowed, the comma may only
  // stand in for a factory frame the fwd hook blocked: the one that started the emergency, or the one that disengaged
  violation |= (chrysler_das_3_emergency_seen || !controls_allowed) && !match->blocked;

  // While longitudinal actuation is not allowed (controls not allowed, or the driver presses the gas), only the factory
  // frame itself. With controls not allowed it fills the gap the blocked factory frame left on bus 0.
  violation |= !get_longitudinal_allowed() && !exact;

  if (controls_allowed) {
    // COUNTER, ACC_AVAILABLE, ACC_ACTIVE, ACC_FAULTED, COLLISION_BRK_PREP, ACC_BRK_PREP, GR_MAX_REQ,
    // DISABLE_FUEL_SHUTOFF, ACC_STANDSTILL and ACC_GO equal the factory's
    for (int i = 0; i < 7; i++) {
      violation |= (cmd.data[i] & CHRYSLER_DAS_3_COPY_MASK[i]) != (stock->data[i] & CHRYSLER_DAS_3_COPY_MASK[i]);
    }

    // deceleration at least as strong as the factory's
    const bool stock_decel = stock->decel_req != 0;
    const bool cmd_decel = cmd.decel_req != 0;
    if (stock_decel) {
      // keep the factory request; the comma may only make it stronger. (The fwd hook passes a factory frame with a
      // deceleration request to bus 0 itself, so a copy of it is a repeat, refused by the COUNTER rule above.)
      violation |= cmd.decel_req != stock->decel_req;
      violation |= cmd.accel > stock->accel;
    } else if (cmd_decel) {
      // deceleration the factory does not request: -3.5 to 0 m/s^2, with engine torque at most 0 Nm
      violation |= cmd.decel_req != 1;
      violation |= max_limit_check(cmd.accel, CHRYSLER_ACC_DECEL_ZERO, CHRYSLER_ACC_DECEL_MIN);
      violation |= cmd.engine_torque > MIN(CHRYSLER_TORQUE_INACTIVE, stock->engine_torque);
    } else {
      // no deceleration request: ACC_DECEL as the factory's
      violation |= cmd.accel != stock->accel;
    }

    // the comma alone never commands more than -3.5 m/s^2; a stronger factory request is copied
    violation |= cmd.accel < MIN(CHRYSLER_ACC_DECEL_MIN, stock->accel);

    // engine torque never above the factory request
    violation |= cmd.engine_torque > stock->engine_torque;
    violation |= cmd.engine_torque_max > stock->engine_torque_max;
  }

  if (!violation) {
    chrysler_das_3_bus0_counter = cmd.counter;
    chrysler_das_3_bus0_counter_valid = true;
    chrysler_das_3_bus0_ts = ts;
  }
  return !violation;
}

static void chrysler_rx_hook(const CANPacket_t *msg) {
  // Measured EPS torque
  if ((msg->bus == 0U) && (msg->addr == chrysler_addrs->EPS_2)) {
    int torque_meas_new = ((msg->data[4] & 0x7U) << 8) + msg->data[5] - 1024U;
    update_sample(&torque_meas, torque_meas_new);
  }

  // enter controls on rising edge of ACC, exit controls on ACC off
  const unsigned int das_3_bus = (chrysler_platform == CHRYSLER_PACIFICA) ? 0U : 2U;
  if ((msg->bus == das_3_bus) && (msg->addr == chrysler_addrs->DAS_3)) {
    bool cruise_engaged = GET_BIT(msg, 21U);
    pcm_cruise_check(cruise_engaged);
    acc_main_on = GET_BIT(msg, 20U);

    // RamPilot longitudinal: keep the two newest factory frames (they passed the checksum and counter checks to get
    // here). The fwd hook ran first for this frame; if it passed it to bus 0, the truck has seen its COUNTER.
    if (chrysler_rampilot_long) {
      chrysler_das_3_stock[1] = chrysler_das_3_stock[0];
      ChryslerDas3Stock *latest = &chrysler_das_3_stock[0];
      chrysler_das_3_decode(msg, &latest->frame);
      latest->ts = microsecond_timer_get();
      latest->seen = true;
      latest->blocked = chrysler_das_3_stock_blocked;
      if (chrysler_das_3_stock_forwarded) {
        chrysler_das_3_bus0_counter = latest->frame.counter;
        chrysler_das_3_bus0_counter_valid = true;
        chrysler_das_3_bus0_ts = latest->ts;
      }
      chrysler_das_3_stock_forwarded = false;
      chrysler_das_3_stock_blocked = false;

      // a factory emergency frame (collision or ACC brake prep, or braking stronger than -2.5 m/s^2) opens a window in
      // which the factory frames pass directly to bus 0; each one extends it
      const bool collision_prep = (latest->frame.data[6] & CHRYSLER_DAS_3_EMERGENCY_BITS) != 0U;
      const bool hard_braking = (latest->frame.decel_req != 0) && (latest->frame.accel < CHRYSLER_ACC_DECEL_EMERGENCY);
      if (collision_prep || hard_braking) {
        chrysler_das_3_emergency_ts = latest->ts;
        chrysler_das_3_emergency_seen = true;
      }
    }
  }

  // TODO: use the same message for both
  // update vehicle moving
  if ((chrysler_platform != CHRYSLER_PACIFICA) && (msg->bus == 0U) && (msg->addr == chrysler_addrs->ESP_8)) {
    vehicle_moving = ((msg->data[4] << 8) + msg->data[5]) != 0U;
  }
  if ((chrysler_platform == CHRYSLER_PACIFICA) && (msg->bus == 0U) && (msg->addr == 514U)) {
    int speed_l = (msg->data[0] << 4) + (msg->data[1] >> 4);
    int speed_r = (msg->data[2] << 4) + (msg->data[3] >> 4);
    vehicle_moving = (speed_l != 0) || (speed_r != 0);
  }

  // exit controls on rising edge of gas press
  if ((msg->bus == 0U) && (msg->addr == chrysler_addrs->ECM_5)) {
    gas_pressed = msg->data[0U] != 0U;
  }

  // exit controls on rising edge of brake press
  if ((msg->bus == 0U) && (msg->addr == chrysler_addrs->ESP_1)) {
    brake_pressed = ((msg->data[0U] & 0xFU) >> 2U) == 1U;
  }

  if ((chrysler_platform == CHRYSLER_PACIFICA) && (msg->bus == 0U) && (msg->addr == chrysler_addrs->TRACTION_BUTTON)) {
    mads_button_press = GET_BIT(msg, 53U) ? MADS_BUTTON_PRESSED : MADS_BUTTON_NOT_PRESSED;
  }

  if ((chrysler_platform != CHRYSLER_PACIFICA) && (msg->bus == 0U)) {
    if (msg->addr == chrysler_addrs->Center_Stack_2) {
      mads_button_press = GET_BIT(msg, 57U) ? MADS_BUTTON_PRESSED : MADS_BUTTON_NOT_PRESSED;
    }
  }
}

static bool chrysler_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits CHRYSLER_STEERING_LIMITS = {
    .max_torque = 261,
    .max_rt_delta = 112,
    .max_rate_up = 3,
    .max_rate_down = 3,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  const TorqueSteeringLimits CHRYSLER_RAM_DT_STEERING_LIMITS = {
    .max_torque = 350,
    .max_rt_delta = 112,
    .max_rate_up = 6,
    .max_rate_down = 6,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  const TorqueSteeringLimits CHRYSLER_RAM_HD_STEERING_LIMITS = {
    .max_torque = 361,
    .max_rt_delta = 182,
    .max_rate_up = 14,
    .max_rate_down = 14,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  bool tx = true;

  // STEERING
  if (msg->addr == chrysler_addrs->LKAS_COMMAND) {
    int start_byte = (chrysler_platform == CHRYSLER_PACIFICA) ? 0 : 1;
    int desired_torque = ((msg->data[start_byte] & 0x7U) << 8) | msg->data[start_byte + 1];
    desired_torque -= 1024;

    const TorqueSteeringLimits limits = (chrysler_platform == CHRYSLER_PACIFICA) ? CHRYSLER_STEERING_LIMITS :
                                        (chrysler_platform == CHRYSLER_RAM_DT) ? CHRYSLER_RAM_DT_STEERING_LIMITS : CHRYSLER_RAM_HD_STEERING_LIMITS;

    bool steer_req = (chrysler_platform == CHRYSLER_PACIFICA) ? GET_BIT(msg, 4U) : (msg->data[3] & 0x7U) == 2U;
    if (steer_torque_cmd_checks(desired_torque, steer_req, limits)) {
      tx = false;
    }
  }

  // FORCE CANCEL: only the cancel button press is allowed
  if (msg->addr == chrysler_addrs->CRUISE_BUTTONS) {
    const bool is_cancel = msg->data[0] == 1U;
    const bool is_resume = msg->data[0] == 0x10U;
    const bool allowed = is_cancel || (is_resume && controls_allowed);
    if (!allowed) {
      tx = false;
    }
  }

  // RamPilot longitudinal: DAS_3 is only in the TX list with the option on. Every attempt counts, accepted or not:
  // when they stop, the fwd hook passes the factory DAS_3 again.
  if (msg->addr == chrysler_addrs->DAS_3) {
    chrysler_das_3_expire();
    chrysler_das_3_tx_ts = microsecond_timer_get();
    chrysler_das_3_tx_seen = true;
    chrysler_das_3_fallback_update();
    // before an accepted copy moves it: would this copy move the truck's DAS_3 COUNTER forward?
    const bool ahead = chrysler_das_3_counter_ahead(chrysler_get_counter(msg));
    const bool allowed = chrysler_rampilot_long_das_3_allowed(msg);

    // Refusals in a row while controls are allowed, outside a factory emergency, hand bus 0 back to the factory. Only a
    // refused copy that would have moved the truck forward counts: after one that is behind or a repeat, the truck
    // already has that frame or a newer one.
    if (allowed) {
      chrysler_das_3_refusals = 0U;
    } else if (controls_allowed && !chrysler_das_3_emergency_seen && ahead &&
               (chrysler_das_3_refusals < CHRYSLER_DAS_3_MAX_REFUSALS)) {
      chrysler_das_3_refusals += 1U;
    } else {
    }
    if (chrysler_das_3_refusals >= CHRYSLER_DAS_3_MAX_REFUSALS) {
      chrysler_das_3_fallback_set();
    }

    if (!allowed) {
      tx = false;
    }
  }

  return tx;
}

// A factory DAS_3 that requests braking (ACC_DECEL_REQ) or brake preparation (COLLISION_BRK_PREP, ACC_BRK_PREP) is
// never blocked: the truck gets the first such frame directly, also when the host has stopped sending. So is one that
// isn't 8 bytes. Without the packet (a caller with only the address) nothing tells them apart, so no factory DAS_3 is
// blocked: plain factory cruise, and every comma copy is a repeat of a frame already on bus 0.
static bool chrysler_das_3_factory_priority(const CANPacket_t *packet) {
  bool priority = true;
  if ((packet != NULL) && (GET_LEN(packet) == 8U)) {
    ChryslerDas3 factory;
    chrysler_das_3_decode(packet, &factory);
    priority = (factory.decel_req != 0) || ((factory.data[6] & CHRYSLER_DAS_3_BRAKE_PREP_BITS) != 0U);
  }
  return priority;
}

static bool chrysler_fwd(int bus_num, int addr, const CANPacket_t *packet) {
  bool block_msg = false;

  // RamPilot longitudinal: while controls are allowed and the comma is sending DAS_3, its frame replaces the factory
  // frame on bus 0, except a factory frame with braking or brake preparation. The factory frame is passed again if the
  // comma goes quiet, during a factory emergency, and after a fallback latch (repeated refusals, or no DAS_3 on bus 0
  // for too long), which ends the engagement.
  if (chrysler_rampilot_long && (bus_num == 2) && (addr == (int)chrysler_addrs->DAS_3)) {
    chrysler_das_3_expire();
    chrysler_das_3_fallback_update();
    // while the comma replaces the factory frame, a DAS_3 must reach bus 0 at least every CHRYSLER_DAS_3_DELIVERY_TIMEOUT
    const uint32_t delivery_age = get_ts_elapsed(microsecond_timer_get(), chrysler_das_3_bus0_ts);
    if (controls_allowed && chrysler_das_3_tx_seen && chrysler_das_3_bus0_counter_valid &&
        (delivery_age > CHRYSLER_DAS_3_DELIVERY_TIMEOUT)) {
      chrysler_das_3_fallback_set();
    }
    const bool factory_priority = chrysler_das_3_factory_priority(packet);
    block_msg = controls_allowed && chrysler_das_3_tx_seen && !chrysler_das_3_fallback && !chrysler_das_3_emergency_seen &&
                !factory_priority;
    // the panda runs the rx hook on this frame next
    chrysler_das_3_stock_forwarded = !block_msg;
    chrysler_das_3_stock_blocked = block_msg;
  }

  return block_msg;
}

static bool chrysler_fwd_hook(int bus_num, int addr) {
  return chrysler_fwd(bus_num, addr, NULL);
}

static bool chrysler_fwd_packet_hook(int bus_num, const CANPacket_t *msg) {
  return chrysler_fwd(bus_num, (int)msg->addr, msg);
}

static safety_config chrysler_init(uint16_t param) {

  const uint32_t CHRYSLER_PARAM_RAM_DT = 1U;  // set for Ram DT platform
  const uint32_t CHRYSLER_PARAM_RAMPILOT_LONG = 4U;  // RamPilot longitudinal, Ram DT only

  // CAN messages for Chrysler/Jeep platforms
  static const ChryslerAddrs CHRYSLER_ADDRS = {
    .EPS_2            = 0x220,  // EPS driver input torque
    .ESP_1            = 0x140,  // Brake pedal and vehicle speed
    .ESP_8            = 0x11C,  // Brake pedal and vehicle speed
    .ECM_5            = 0x22F,  // Throttle position sensor
    .DAS_3            = 0x1F4,  // ACC engagement states from DASM
    .DAS_6            = 0x2A6,  // LKAS HUD and auto headlight control from DASM
    .LKAS_COMMAND     = 0x292,  // LKAS controls from DASM
    .CRUISE_BUTTONS   = 0x23B,  // Cruise control buttons
    .LKAS_HEARTBIT    = 0x2D9,  // LKAS HEARTBIT from DASM
    .TRACTION_BUTTON  = 0x330,  // Traction control button
  };

  // CAN messages for the 5th gen RAM DT platform
  static const ChryslerAddrs CHRYSLER_RAM_DT_ADDRS = {
    .EPS_2            = 0x31,   // EPS driver input torque
    .ESP_1            = 0x83,   // Brake pedal and vehicle speed
    .ESP_8            = 0x79,   // Brake pedal and vehicle speed
    .ECM_5            = 0x9D,   // Throttle position sensor
    .DAS_3            = 0x99,   // ACC engagement states from DASM
    .DAS_6            = 0xFA,   // LKAS HUD and auto headlight control from DASM
    .LKAS_COMMAND     = 0xA6,   // LKAS controls from DASM
    .CRUISE_BUTTONS   = 0xB1,   // Cruise control buttons
    .Center_Stack_2   = 0x28A,  // Center stack buttons2
  };

  static RxCheck chrysler_ram_dt_rx_checks[] = {
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.ESP_8, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.DAS_3, 2, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ADDRS.Center_Stack_2, 0, 8, 1U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static RxCheck chrysler_rx_checks[] = {
    {.msg = {{CHRYSLER_ADDRS.EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ADDRS.ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    //{.msg = {{ESP_8, 0, 8, .max_counter = 15U, .ignore_quality_flag = true, .frequency = 50U}}},
    {.msg = {{514, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ADDRS.ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ADDRS.DAS_3, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ADDRS.TRACTION_BUTTON, 0, 8, 1U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const CanMsg CHRYSLER_TX_MSGS[] = {
    {CHRYSLER_ADDRS.CRUISE_BUTTONS, 0, 3, .check_relay = false},
    {CHRYSLER_ADDRS.LKAS_COMMAND, 0, 6, .check_relay = true},
    {CHRYSLER_ADDRS.DAS_6, 0, 8, .check_relay = true},
    {CHRYSLER_ADDRS.LKAS_HEARTBIT, 0, 5, .check_relay = true},
  };

  static const CanMsg CHRYSLER_RAM_DT_TX_MSGS[] = {
    {CHRYSLER_RAM_DT_ADDRS.CRUISE_BUTTONS, 2, 3, .check_relay = false},
    {CHRYSLER_RAM_DT_ADDRS.LKAS_COMMAND, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_DT_ADDRS.DAS_6, 0, 8, .check_relay = true},
  };

  static const CanMsg CHRYSLER_RAM_DT_RAMPILOT_LONG_TX_MSGS[] = {
    {CHRYSLER_RAM_DT_ADDRS.CRUISE_BUTTONS, 2, 3, .check_relay = false},
    {CHRYSLER_RAM_DT_ADDRS.LKAS_COMMAND, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_DT_ADDRS.DAS_6, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_DT_ADDRS.DAS_3, 0, 8, .check_relay = true, .disable_static_blocking = true},  // blocked in chrysler_fwd
  };

#ifdef ALLOW_DEBUG
  // CAN messages for the 5th gen RAM HD platform
  static const ChryslerAddrs CHRYSLER_RAM_HD_ADDRS = {
    .EPS_2            = 0x220,  // EPS driver input torque
    .ESP_1            = 0x140,  // Brake pedal and vehicle speed
    .ESP_8            = 0x11C,  // Brake pedal and vehicle speed
    .ECM_5            = 0x22F,  // Throttle position sensor
    .DAS_3            = 0x1F4,  // ACC engagement states from DASM
    .DAS_6            = 0x275,  // LKAS HUD and auto headlight control from DASM
    .LKAS_COMMAND     = 0x276,  // LKAS controls from DASM
    .CRUISE_BUTTONS   = 0x23A,  // Cruise control buttons
    .Center_Stack_2   = 0x28A,  // Center stack buttons2
  };

  static RxCheck chrysler_ram_hd_rx_checks[] = {
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.ESP_8, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.DAS_3, 2, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ADDRS.Center_Stack_2, 0, 8, 1U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const CanMsg CHRYSLER_RAM_HD_TX_MSGS[] = {
    {CHRYSLER_RAM_HD_ADDRS.CRUISE_BUTTONS, 2, 3, .check_relay = false},
    {CHRYSLER_RAM_HD_ADDRS.LKAS_COMMAND, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_HD_ADDRS.DAS_6, 0, 8, .check_relay = true},
  };

  const uint32_t CHRYSLER_PARAM_RAM_HD = 2U;  // set for Ram HD platform
  bool enable_ram_hd = GET_FLAG(param, CHRYSLER_PARAM_RAM_HD);
#endif

  safety_config ret;

  bool enable_ram_dt = GET_FLAG(param, CHRYSLER_PARAM_RAM_DT);

  chrysler_rampilot_long = false;
  chrysler_das_3_stock[0] = (ChryslerDas3Stock){0};
  chrysler_das_3_stock[1] = (ChryslerDas3Stock){0};
  chrysler_das_3_stock_forwarded = false;
  chrysler_das_3_stock_blocked = false;
  chrysler_das_3_bus0_counter_valid = false;
  chrysler_das_3_bus0_ts = 0U;
  chrysler_das_3_tx_seen = false;
  chrysler_das_3_tx_ts = 0U;
  chrysler_das_3_refusals = 0U;
  chrysler_das_3_fallback = false;
  chrysler_das_3_emergency_seen = false;
  chrysler_das_3_emergency_ts = 0U;

  if (enable_ram_dt) {
    chrysler_platform = CHRYSLER_RAM_DT;
    chrysler_addrs = &CHRYSLER_RAM_DT_ADDRS;
    chrysler_rampilot_long = GET_FLAG(param, CHRYSLER_PARAM_RAMPILOT_LONG);
    if (chrysler_rampilot_long) {
      ret = BUILD_SAFETY_CFG(chrysler_ram_dt_rx_checks, CHRYSLER_RAM_DT_RAMPILOT_LONG_TX_MSGS);
    } else {
      ret = BUILD_SAFETY_CFG(chrysler_ram_dt_rx_checks, CHRYSLER_RAM_DT_TX_MSGS);
    }
#ifdef ALLOW_DEBUG
  } else if (enable_ram_hd) {
    chrysler_platform = CHRYSLER_RAM_HD;
    chrysler_addrs = &CHRYSLER_RAM_HD_ADDRS;
    ret = BUILD_SAFETY_CFG(chrysler_ram_hd_rx_checks, CHRYSLER_RAM_HD_TX_MSGS);
#endif
  } else {
    chrysler_platform = CHRYSLER_PACIFICA;
    chrysler_addrs = &CHRYSLER_ADDRS;
    ret = BUILD_SAFETY_CFG(chrysler_rx_checks, CHRYSLER_TX_MSGS);
  }
  return ret;
}

const safety_hooks chrysler_hooks = {
  .init = chrysler_init,
  .rx = chrysler_rx_hook,
  .tx = chrysler_tx_hook,
  .fwd = chrysler_fwd_hook,
  .fwd_packet = chrysler_fwd_packet_hook,
  .get_counter = chrysler_get_counter,
  .get_checksum = chrysler_get_checksum,
  .compute_checksum = chrysler_compute_checksum,
};
