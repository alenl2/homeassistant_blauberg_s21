# Home Assistant - Blauberg S21 (Extended) - Custom Component

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

## Overview

This custom component enables local control and monitoring of your Blauberg S21 HVAC system directly within Home Assistant.

It is an extended version of **[jvitkauskas' original development](https://github.com/jvitkauskas/homeassistant_blauberg_s21)**. Many thanks for laying the foundation!

The major differences include:
- Expanded attributes: All temperatures, scheduler status, alarm code
- New control functions: Timer mode, boost mode, alarm reset, bypass/rotor mode
- Temperature sensors for each of the four air streams, with long-term statistics
- A slider for the custom (manual) fan speed percentage


## Entities

| Entity | Purpose |
| --- | --- |
| `climate.<device>` | Power, operating mode, target temperature and fan preset |
| `number.<device>_manual_fan_speed` | Fan speed percentage used by the "custom" fan mode |
| `sensor.<device>_supply_temperature` etc. | The four heat-exchanger temperatures |
| `sensor.<device>_humidity` | Extract air humidity, if the unit has the sensor |
| `sensor.<device>_supply_fan_speed` | Fan speeds, air pressures and the filter countdown |
| `switch.<device>_boost_mode` | Boost |
| `switch.<device>_timer_mode` | Main timer |
| `switch.<device>_schedule_mode` | Weekly schedule |
| `select.<device>_bypass_mode` | Bypass / rotor mode (only if the unit has one) |
| `button.<device>_reset_filter_timer` | Reset the filter countdown |
| `button.<device>_reset_alarm` | Clear the alarm state |

Entities that describe how the unit is running rather than what it is doing —
fan speeds, air pressures and the filter countdown — are in Home Assistant's
diagnostic category, so they stay out of the way on the device page.

### Manual (custom) fan speed

The climate entity's fan modes select the unit's **preset** speeds — `low`,
`medium`, `high` on a 3-speed unit, or the numbered presets on units configured
for more levels. Choosing `custom` switches the unit into its manual speed mode,
where it runs at a percentage instead of a preset.

`number.<device>_manual_fan_speed` sets that percentage. It can be adjusted at
any time; the unit stores the value and simply ignores it while running a preset
speed. Its `active` attribute tells you whether the unit is currently acting on
it.

### Sensors

Four sensors expose the heat exchanger's air streams, using the standard
ventilation naming:

| Sensor | Air stream |
| --- | --- |
| Supply outdoor temperature | Outdoor air entering the unit |
| Supply temperature | Air the unit delivers to the rooms |
| Extract temperature | Air drawn out of the rooms |
| Extract outlet temperature | Air the unit exhausts outside |

Alongside them: extract air humidity, both fan speeds in rpm, both air pressures
and the number of days left on the filter timer.

All of them carry a state class, so Home Assistant records long-term statistics.
The climate entity's `current_temperature` mirrors the supply temperature.

A few notes on hardware that is not always fitted:

- The humidity sensor entity is only created if the unit reports a reading.
- The air pressure sensors read a constant `0` unless the optional pressure
  sensors are installed. Pascals is assumed, as the conventional unit for
  ventilation static pressure; the registers themselves do not state a unit.


## Installation and Configuration

As this integration is not part of Home Assistant Core, you have to download it first into your Home Assistant installation. 

### Download via HACS
Click the following button to open the download page for this integration in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=marni-xyz&repository=homeassistant_blauberg_s21&category=integration)

### Alternative manual installation
1. Copy the `blauberg_s21_ext` folder (with all files) from this repository to the `custom_components` directory of your Home Assistant installation.
2. Restart your HA instance.

### Setup
Configuration is done in the HA UI

1. Navigate to "Configuration" → "Integrations" in the Home Assistant web interface.
2. Click the + button (at the bottom right) and search for "Blauberg S21 (Extended)".
3. After configuration, your device with a climate entity, some buttons and switches will appear in "Configuration" → "Integrations".


## Report issues

If you have any issues with this integration, please [open an issue](../../issues).

Make sure to include debug logs. See https://www.home-assistant.io/integrations/logger/ for more information on how to enable debug logs.

```
logger:
  default: info
  logs:
    custom_components.blauberg_s21_ext: debug
```

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)


## Dependency

The Modbus client ([pybls21](https://github.com/marni-xyz/pybls21)) is vendored
into this component under `custom_components/blauberg_s21_ext/pybls21/`, so the
only external requirement is `pymodbus`.

### Notes on the Modbus connection

The S21 accepts **exactly one** TCP connection at a time and answers any further
connection attempt with a TCP reset. This component therefore holds the socket
only for the duration of a single read or write and closes it again immediately,
which keeps the unit reachable for other clients such as the vendor app.

If a client stops using its socket without closing it, the unit keeps that single
slot reserved for as long as the socket stays open — which can lock everything
else out long after the client is gone. The component therefore closes the socket
on `EVENT_HOMEASSISTANT_STOP` (Home Assistant does not unload integrations on
shutdown, so this has to be done explicitly), and it rides out short outages
instead of immediately flagging the entities unavailable.

---

[buymecoffee]: https://www.buymeacoffee.com/marnixyz
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/marni-xyz/homeassistant_blauberg_s21.svg?style=for-the-badge
[commits]: https://github.com/marni-xyz/homeassistant_blauberg_s21/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/marni-xyz/homeassistant_blauberg_s21.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-%40marni-xyz.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/v/release/marni-xyz/homeassistant_blauberg_s21.svg?style=for-the-badge
[releases]: https://github.com/marni-xyz/homeassistant_blauberg_s21/releases
[user_profile]: https://github.com/marni-xyz
