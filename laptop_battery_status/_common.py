"""Exceptions and small helpers shared by the platform backends.

This module exists (instead of putting the exceptions in ``__init__.py``) so
that the backend modules can import the exceptions without a circular import.
"""

from typing import Any, Dict, Optional

# The dictionary that every backend's get_status() returns. Its values have
# mixed types, so Dict[str, Any] is as precise as this can usefully get. See
# make_status() for what each key means.
Status = Dict[str, Any]


class BatteryStatusError(RuntimeError):
    """Base class for all exceptions raised by the laptop_battery_status package.

    Subclasses RuntimeError so that ``except RuntimeError`` in existing code
    still catches these.
    """


class NoBatteryError(BatteryStatusError):
    """Raised when the machine has no battery (say, a desktop computer).

    Also raised when every battery the operating system knows about reports
    that it isn't currently installed.
    """


class UnsupportedPlatformError(BatteryStatusError):
    """Raised when running on an operating system this package can't read."""


def make_status(battery_present: Any,
                plugged_in: Optional[bool],
                percent: Optional[float],
                minutes_left: Optional[float],
                charging: Optional[bool]) -> Status:
    """Return the dictionary that every backend's get_status() must produce.

    * battery_present - True/False, whether a battery is installed.
    * plugged_in      - True/False, or None if AC status is unknown.
    * percent         - float from 0.0 to 100.0, or None if unknown.
    * minutes_left    - float minutes of runtime left while discharging, or
                        None if unknown or not currently discharging.
    * charging        - True/False, or None if unknown.
    """
    if percent is not None:
        # Clamp, since some firmware happily reports values like 101%.
        percent = float(percent)
        if percent < 0.0:
            percent = 0.0
        elif percent > 100.0:
            percent = 100.0
    if minutes_left is not None:
        minutes_left = float(minutes_left)
        if minutes_left < 0.0:
            minutes_left = None
    return {
        'battery_present': bool(battery_present),
        'plugged_in': plugged_in,
        'percent': percent,
        'minutes_left': minutes_left,
        'charging': charging,
    }
