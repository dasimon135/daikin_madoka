# Home Assistant Daikin Madoka

This repository provides **two ways** to control Daikin Madoka BRC1H Bluetooth thermostats:

1. **Home Assistant Custom Integration** (this directory) - Direct Bluetooth control
2. **ESPHome Components** (`esphome_components/`) - ESP32 proxy for remote control

![](images/madoka.png)

![](images/integration.png)  ![](images/climate.png) ![](images/entities.png)

## Choose Your Approach

### Option 1: Home Assistant Integration (Direct Bluetooth)

**Pros:**
- Direct connection from Home Assistant server
- No additional hardware required
- Lower latency

**Cons:**
- Home Assistant server must be within Bluetooth range
- Requires DBUS configuration for Docker/VM setups

See installation instructions below.

### Option 2: ESPHome Proxy (Remote Bluetooth)

**Pros:**
- Place ESP32 device anywhere within Bluetooth range
- Multiple thermostats via single ESP32
- Works with Home Assistant in Docker/VM without DBUS complexity

**Cons:**
- Requires ESP32 hardware (e.g., M5Stack Atom Lite)
- Additional device to maintain

See `esphome_components/README.md` for details.

---

## Home Assistant Integration Installation

Download folder and copy under "custom_components" folder in the Home Assistant configuration folder.

## Requirements

Due to the thermostat security constraints, is has to be manually paired with the system where HomeAssistant runs. This has only been tested in Linux, but the following steps should be easy to follow:

1. Disconnect the thermostat from any other device (thermostat Bluetooth menu, forget). This has to be done to make the device visible during the scanning.
2. On a terminal, run "bluetoothctl"
3. Type "agent KeyboardDisplay"
4. Type "remove <BRC1H_MAC_ADDRESS>". This step helps to remove unsucessful previous pairings and makes the device visible.
5. Type "scan on" and wait until the mac is listed.
6. Type "scan off"
7. Type "pair <BRC1H_MAC_ADDRESS>". You will be presented a confirmation prompt, accept it.
8. Go to the thermostat and accept the pairing code. It requires to do it fairly soon after the previous step or it will be cancelled.
9. The device is ready and you can start the integration in Home Assistant.

A dedicated Bluetooth adapter is desirable. If you run Home Assistant in a virtual machine, it makes it easiser for the device to be used. In VMWare, make sure to remove the checkbox "Share bluetooth devices with guests". This way, the adapter will become visible to the virtual machine and will use it without problem. 

## Usage

A new integration will be available under the name "Daikin Madoka". You have to provide the following details:

- Bluetooth MAC Address of the BRC1H device(s)
- Name of the Bluetooth adapter (usually hci0)

The integration will scan for the devices and will create the thermostat and the temperature sensor.
## Troubleshooting

* **The integration form shows an error "The device could not be found" next to the adapter field but "hcitool dev" lists the device" **

This could be a problem related to the configuration of the DBUS service. Make sure DBUS is installed in the host (it generally is) and that it is available to your homeassistant docker.

You can test it using *bleak* CLI tool *bleak-lescan* inside your instance. Follow these steps:

```
$ docker exec -ti <homeassistant_container_id> /bin/bash
$ bleak-lescan -i <adapter>
```

If the following error appears, DBUS is not available to the docker instance.
```
File "/usr/local/lib/python3.8/site-packages/bleak/backends/bluezdbus/scanner.py", line 90, in start
    self._bus = await client.connect(self._reactor, "system").asFuture(loop)
twisted.internet.error.ConnectError: An error occurred while connecting: Failed to connect to any bus address. Last error: An error occurred while connecting: 2: No such file or directory..
“Failed to connect to any bus address”
```
To make DBus available you have to link /var/run/dbus/system_bus_socket inside the container and also run docker in privileged mode. 

Modify your docker-compose.yml:
```
volumes:
  - /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket
privileged: true
```

Kudos to [Jose](https://community.home-assistant.io/u/jcsogo) for the solution.

## TODO
This document.
Icon and integration images.
