"""Sensors that record another sensor's value at the time given by one of its attributes."""

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)
