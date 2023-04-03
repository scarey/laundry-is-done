# MIT License (MIT)
# Copyright (c) 2023 Stephen Carey
# https://opensource.org/licenses/MIT

# Micropython code for sensing when the washer/dryer is finished.

import time

from machine import SoftI2C, Pin
import uasyncio as asyncio
import mpu6050
from mqtt_as import MQTTClient
from mqtt_local import config
import _thread

# motion is indicated by the 3 accelerometer readings combined being greater than this threshold
sensitivity = 150
idle_counter = 0

# done means no motion within SAMPLE_SECS * MAX_IDLE_PERIODS seconds
SAMPLE_SECS = 10
MAX_IDLE_PERIODS = 6

client = None
command = None

BASE_TOPIC = 'esp32/washer'
DEVICE_STATUS_TOPIC = f'{BASE_TOPIC}/status'
ACTIVE_TOPIC = f'{BASE_TOPIC}/active/1'
READINGS_TOPIC = f'{BASE_TOPIC}/readings/1'
NOTIFY_TOPIC = f'{BASE_TOPIC}/notify'
COMMAND_TOPIC = f'{ACTIVE_TOPIC}/set'

is_active = False
accelerometer_changes = None
notification = None

i2c = SoftI2C(scl=Pin(16), sda=Pin(17))
accelerometer = mpu6050.accel(i2c)

last_readings = accelerometer.get_values()


def update_readings_thread():
    global accelerometer_changes, last_readings, idle_counter, command, notification
    while True:
        if is_active:
            accelerometer_values = accelerometer.get_values()
            # {'GyZ': -235, 'GyY': 296, 'GyX': 16, 'Tmp': 26.64764, 'AcZ': -1552, 'AcY': -412, 'AcX': 16892}
            change_x = abs(last_readings['GyX'] - accelerometer_values['GyX'])
            change_y = abs(last_readings['GyY'] - accelerometer_values['GyY'])
            change_z = abs(last_readings['GyZ'] - accelerometer_values['GyZ'])
            last_readings = accelerometer_values
            print("X:{}, Y:{}, Z:{}".format(change_x, change_y, change_z))
            accelerometer_changes = (change_x, change_y, change_z)
            if change_x + change_y + change_z > sensitivity:
                idle_counter = 0
            else:
                idle_counter += 1
            if idle_counter == MAX_IDLE_PERIODS:
                print("Done!")
                command = 'off'
                notification = "We're done!"

        time.sleep(SAMPLE_SECS)


def handle_incoming_message(topic, msg, retained):
    print(f'{topic}: {msg}')
    global command
    command = str(msg, 'UTF-8')


async def wifi_han(state):
    print('Wifi is ', 'up' if state else 'down')
    await asyncio.sleep(1)


# If you connect with clean_session True, must re-subscribe (MQTT spec 3.1.2.4)
async def conn_han(client):
    await client.subscribe(COMMAND_TOPIC, 0)
    await online()


async def online():
    await client.publish(DEVICE_STATUS_TOPIC, 'online', retain=True, qos=0)


async def main():
    await client.connect()
    await asyncio.sleep(2)  # Give broker time
    await online()
    global command, is_active, accelerometer_changes, idle_counter, notification
    while True:
        if command:
            is_active = True if command.lower() == 'on' else False
            if is_active:
                idle_counter = 0
                accelerometer_changes = None
            await client.publish(ACTIVE_TOPIC, command, True, 0)
            command = None
        if accelerometer_changes:
            await client.publish(READINGS_TOPIC, str(accelerometer_changes), True, 0)
            accelerometer_changes = None
        if notification:
            await client.publish(NOTIFY_TOPIC, notification, False, 0)
            notification = None
        await asyncio.sleep(1)


config['subs_cb'] = handle_incoming_message
config['connect_coro'] = conn_han
config['wifi_coro'] = wifi_han
config['will'] = [DEVICE_STATUS_TOPIC, 'offline', True, 0]

MQTTClient.DEBUG = False
client = MQTTClient(config)

try:
    _thread.start_new_thread(update_readings_thread, ())

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
finally:
    client.close()
    asyncio.stop()
