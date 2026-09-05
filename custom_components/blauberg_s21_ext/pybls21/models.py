from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional

TEMP_CELSIUS: str = "°C"


class ClimateEntityFeature(int, Enum):
    TARGET_TEMPERATURE = 1
    FAN_MODE = 8


class HVACMode(str, Enum):
    OFF =       "off"
    HEAT =      "heat"
    COOL =      "cool"
    AUTO =      "auto"
    FAN_ONLY =  "fan_only"


class HVACAction(str, Enum):
    COOLING =   "cooling"
    FAN =       "fan"
    HEATING =   "heating"
    IDLE =      "idle"
    OFF =       "off"


@dataclass
class ClimateDevice:
    available:                  bool
    name:                       str
    unique_id:                  str
    temperature_unit:           str
    precision:                  float
    current_temperature:        float
    target_temperature:         float
    target_temperature_step:    float
    max_temp:                   float
    min_temp:                   float
    hvac_mode:                  str
    hvac_action:                str
    hvac_modes:                 List[str]
    supported_features:         int
    manufacturer:               str
    is_boosting:                bool
    current_intake_temperature: float
    manual_fan_speed_percent:   int
    max_fan_level:              int
    filter_state:               int
    alarm_state:                int
    supply_fan_speed:           int
    extract_fan_speed:          int

    current_intake_temperature_out: float
    current_outlet_temperature_in:  float
    current_outlet_temperature_out: float
    is_timer:                       bool
    is_schedule_mode:               bool
    alarm_codes:                    list[int] = field(default_factory=list)

    current_humidity: Optional[float] = None
    fan_mode:         Optional[int] = None
    fan_modes:        Optional[List[int]] = None
    model:            Optional[str] = None
    sw_version:       Optional[str] = None

    filter_countdown:        Optional[int] = None
    timer_countdown:         Optional[str] = None
    pressure_air_incoming:   Optional[int] = None
    pressure_air_outgoing:   Optional[int] = None
    fan_level_schedule_mode: Optional[int] = None
    fan_level_manual_mode:   Optional[int] = None
    bypass_type:             Optional[int] = None
    bypass_mode:             Optional[int] = None
