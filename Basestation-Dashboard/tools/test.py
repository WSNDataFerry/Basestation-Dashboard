from pymavlink import mavutil

m = mavutil.mavlink_connection('/dev/ttyUSB0', baud=57600)
m.wait_heartbeat()

for msg_id in (33, 24, 74, 30):  # GLOBAL_POSITION_INT, GPS_RAW_INT, VFR_HUD, ATTITUDE
    m.mav.command_long_send(m.target_system, m.target_component, 511, 0, msg_id, 200000, 0, 0, 0, 0, 0)
    print('Requested message intervals (33, 24, 74, 30) -> 5Hz')