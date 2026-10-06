"""laptop_battery_status - report the battery status on Windows, macOS, and Linux.

    >>> import laptop_battery_status
    >>> laptop_battery_status.is_plugged_in()
    True
    >>> laptop_battery_status.battery_level()
    87.0
    >>> laptop_battery_status.battery_minutes_remaining()
    inf

Only the Python standard library is used. Windows reads the Win32 API
directly through ctypes, macOS reads IOKit through ctypes, and Linux reads
/sys/class/power_supply. macOS and Linux fall back to the pmset, upower, and
acpi commands if the direct route doesn't work.

Machines without a battery raise NoBatteryError from the battery functions.
Values the operating system reports as unknown come back as None, except for
battery_minutes_remaining(), which reports them as float('inf').
"""

import sys
from typing import Optional

from ._common import (
    BatteryStatusError, NoBatteryError, Status, UnsupportedPlatformError)

__version__ = '0.2.0'

__all__ = [
    'is_plugged_in',
    'battery_level',
    'battery_minutes_remaining',
    'is_charging',
    'battery_present',
    'BatteryStatusError',
    'NoBatteryError',
    'UnsupportedPlatformError',
]


def _get_status() -> Status:
    """Return the platform backend's status dictionary. See _common.make_status().

    The backend module is imported here rather than at the top of the file so
    that the ctypes setup in _windows.py and _macos.py only runs on the
    platform it belongs to.

    Raises UnsupportedPlatformError on anything that isn't Windows, macOS,
    or Linux.
    """
    if sys.platform == 'win32' or sys.platform == 'cygwin':
        from . import _windows as backend
    elif sys.platform == 'darwin':
        from . import _macos as backend
    elif sys.platform.startswith('linux'):
        from . import _linux as backend
    else:
        raise UnsupportedPlatformError(
            'laptop_battery_status does not support the %r platform.' % (sys.platform,))
    return backend.get_status()


def battery_present() -> bool:
    """Return True if a battery is installed, otherwise False.

    Unlike the other functions in this module, this one never raises
    NoBatteryError. Call it first if you need to know whether the rest of
    this module will work on the current machine.
    """
    return _get_status()['battery_present']


def is_plugged_in() -> bool:
    """Return True if the computer is running on AC power, otherwise False.

    This works on desktop computers with no battery at all, where it always
    returns True.

    Raises BatteryStatusError if the operating system says the AC power
    status is unknown.
    """
    status = _get_status()
    if status['plugged_in'] is None:
        raise BatteryStatusError('The AC power status is unknown on this machine.')
    return status['plugged_in']


def battery_level() -> Optional[float]:
    """Return the battery charge as a float from 0.0 to 100.0 percent.

    Returns None in the uncommon case that a battery is installed but the
    operating system reports its charge as unknown.

    Raises NoBatteryError if there is no battery.
    """
    status = _get_status()
    if not status['battery_present']:
        raise NoBatteryError('This computer does not have a battery installed.')
    return status['percent']


def battery_minutes_remaining() -> float:
    """Return the number of minutes of battery life remaining, as a float.

    float('inf') is returned in the two cases where there is no finite
    number of minutes to report:

    * The computer is plugged in, so the runtime isn't limited by the
      battery.
    * The operating system hasn't produced an estimate yet, which is common
      for the first minute or two after unplugging.

    Raises NoBatteryError if there is no battery.
    """
    status = _get_status()
    if not status['battery_present']:
        raise NoBatteryError('This computer does not have a battery installed.')
    if status['plugged_in'] or status['minutes_left'] is None:
        return float('inf')
    return status['minutes_left']


def is_charging() -> bool:
    """Return True if the battery is currently charging, otherwise False.

    A plugged-in computer whose battery is already full is not charging, so
    this returns False while is_plugged_in() returns True.

    Raises NoBatteryError if there is no battery, and BatteryStatusError if
    the charging state is unknown.
    """
    status = _get_status()
    if not status['battery_present']:
        raise NoBatteryError('This computer does not have a battery installed.')
    if status['charging'] is None:
        raise BatteryStatusError('The battery charging state is unknown on this machine.')
    return status['charging']
