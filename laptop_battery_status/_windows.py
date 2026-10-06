"""Windows backend, using the Win32 GetSystemPowerStatus() function.

See the Microsoft documentation for GetSystemPowerStatus() and the
SYSTEM_POWER_STATUS structure it fills in.
"""

import ctypes
from ctypes import wintypes

from ._common import BatteryStatusError, Status, make_status

# ACLineStatus values:
_AC_OFFLINE = 0
_AC_ONLINE = 1

# BatteryFlag bit values. 1 (high), 2 (low), and 4 (critical) describe the
# charge level and aren't needed here, since BatteryLifePercent is better.
_BATTERY_FLAG_CHARGING = 8
_BATTERY_FLAG_NO_BATTERY = 128

# Both ACLineStatus and BatteryFlag use 255 to mean "unknown".
_UNKNOWN = 255

# BatteryLifeTime and BatteryFullLifeTime use -1 (as an unsigned DWORD) for
# "unknown".
_UNKNOWN_LIFETIME = 0xFFFFFFFF


class SYSTEM_POWER_STATUS(ctypes.Structure):
    # Note that these BYTE fields must be c_ubyte and not ctypes.wintypes.BYTE,
    # because wintypes.BYTE is a *signed* c_byte and would turn the 255
    # "unknown" value into -1.
    _fields_ = [
        ('ACLineStatus', ctypes.c_ubyte),
        ('BatteryFlag', ctypes.c_ubyte),
        ('BatteryLifePercent', ctypes.c_ubyte),
        ('SystemStatusFlag', ctypes.c_ubyte),  # Called Reserved1 before Windows 10.
        ('BatteryLifeTime', wintypes.DWORD),  # Seconds of runtime left.
        ('BatteryFullLifeTime', wintypes.DWORD),  # Seconds of runtime when full.
    ]


_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_GetSystemPowerStatus = _kernel32.GetSystemPowerStatus
_GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
_GetSystemPowerStatus.restype = wintypes.BOOL


def get_status() -> Status:
    """Return the status dictionary for this machine. See _common.make_status().

    Raises BatteryStatusError if the GetSystemPowerStatus() call fails.
    """
    power_status = SYSTEM_POWER_STATUS()
    if not _GetSystemPowerStatus(ctypes.byref(power_status)):
        raise BatteryStatusError(
            'The GetSystemPowerStatus() Win32 call failed: %s'
            % (ctypes.WinError(ctypes.get_last_error()),))

    ac_line_status = power_status.ACLineStatus
    if ac_line_status == _AC_ONLINE:
        plugged_in = True
    elif ac_line_status == _AC_OFFLINE:
        plugged_in = False
    else:
        plugged_in = None

    raw_percent = power_status.BatteryLifePercent
    percent = None if raw_percent == _UNKNOWN else raw_percent

    battery_flag = power_status.BatteryFlag
    if battery_flag == _UNKNOWN:
        # The flags are useless, so guess from whether a charge was reported.
        battery_present = percent is not None
        charging = None
    else:
        battery_present = not (battery_flag & _BATTERY_FLAG_NO_BATTERY)
        charging = bool(battery_flag & _BATTERY_FLAG_CHARGING)

    raw_lifetime = power_status.BatteryLifeTime
    if raw_lifetime == _UNKNOWN_LIFETIME:
        minutes_left = None
    else:
        minutes_left = raw_lifetime / 60.0

    return make_status(battery_present, plugged_in, percent, minutes_left, charging)
