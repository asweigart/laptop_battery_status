"""macOS backend.

The IOPowerSources API in IOKit is read directly through ctypes. If that
fails for any reason, the output of the `pmset -g batt` command is parsed
instead.
"""

import ctypes
import ctypes.util
import re
import subprocess
from typing import Optional, Tuple

from ._common import BatteryStatusError, Status, make_status

_kCFStringEncodingUTF8 = 0x08000100
_kCFNumberSInt64Type = 4

# Keys in the dictionary that IOPSGetPowerSourceDescription() returns.
_KEY_IS_PRESENT = 'Is Present'
_KEY_IS_CHARGING = 'Is Charging'
_KEY_CURRENT_CAPACITY = 'Current Capacity'
_KEY_MAX_CAPACITY = 'Max Capacity'
_KEY_TIME_TO_EMPTY = 'Time to Empty'
_KEY_POWER_SOURCE_STATE = 'Power Source State'

_AC_POWER = 'AC Power'
_BATTERY_POWER = 'Battery Power'

_frameworks = None  # type: Optional[Tuple[ctypes.CDLL, ctypes.CDLL]]


def get_status() -> Status:
    """Return the status dictionary for this machine. See _common.make_status().

    Tries IOKit first and falls back to the pmset command. Raises
    BatteryStatusError only when both routes fail.
    """
    try:
        return _get_status_iokit()
    except Exception as iokit_error:
        try:
            return _get_status_pmset()
        except Exception:
            raise BatteryStatusError(
                'Could not read the power status from IOKit (%s) or from the '
                'pmset command.' % (iokit_error,))


# The IOKit route.


def _load_frameworks() -> Tuple[ctypes.CDLL, ctypes.CDLL]:
    """Return (CoreFoundation, IOKit) ctypes libraries with signatures set.

    The libraries are loaded once and then cached in the module-level
    _frameworks global, since declaring the signatures repeatedly is wasted
    work.
    """
    global _frameworks
    if _frameworks is not None:
        return _frameworks

    core_foundation = ctypes.CDLL(
        ctypes.util.find_library('CoreFoundation')
        or '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    iokit = ctypes.CDLL(
        ctypes.util.find_library('IOKit')
        or '/System/Library/Frameworks/IOKit.framework/IOKit')

    # Every pointer must be declared, or ctypes assumes the C int return type
    # and truncates 64-bit pointers to 32 bits.
    void_p = ctypes.c_void_p
    cf_index = ctypes.c_long  # CFIndex is a signed long.
    boolean = ctypes.c_ubyte  # CoreFoundation's Boolean is an unsigned char.

    core_foundation.CFRelease.argtypes = [void_p]
    core_foundation.CFRelease.restype = None
    core_foundation.CFStringCreateWithCString.argtypes = [void_p, ctypes.c_char_p, ctypes.c_uint32]
    core_foundation.CFStringCreateWithCString.restype = void_p
    core_foundation.CFStringGetCString.argtypes = [void_p, ctypes.c_char_p, cf_index, ctypes.c_uint32]
    core_foundation.CFStringGetCString.restype = boolean
    core_foundation.CFDictionaryGetValue.argtypes = [void_p, void_p]
    core_foundation.CFDictionaryGetValue.restype = void_p
    core_foundation.CFArrayGetCount.argtypes = [void_p]
    core_foundation.CFArrayGetCount.restype = cf_index
    core_foundation.CFArrayGetValueAtIndex.argtypes = [void_p, cf_index]
    core_foundation.CFArrayGetValueAtIndex.restype = void_p
    core_foundation.CFNumberGetValue.argtypes = [void_p, ctypes.c_int, void_p]
    core_foundation.CFNumberGetValue.restype = boolean
    core_foundation.CFBooleanGetValue.argtypes = [void_p]
    core_foundation.CFBooleanGetValue.restype = boolean

    iokit.IOPSCopyPowerSourcesInfo.argtypes = []
    iokit.IOPSCopyPowerSourcesInfo.restype = void_p
    iokit.IOPSCopyPowerSourcesList.argtypes = [void_p]
    iokit.IOPSCopyPowerSourcesList.restype = void_p
    iokit.IOPSGetPowerSourceDescription.argtypes = [void_p, void_p]
    iokit.IOPSGetPowerSourceDescription.restype = void_p
    iokit.IOPSGetProvidingPowerSourceType.argtypes = [void_p]
    iokit.IOPSGetProvidingPowerSourceType.restype = void_p

    _frameworks = (core_foundation, iokit)
    return _frameworks


def _to_str(core_foundation: ctypes.CDLL, cf_string: Optional[int]) -> Optional[str]:
    """Return a CFStringRef as a str, or None.

    None comes back for a NULL reference, and also for the unlikely case of
    a string too long for the 256-byte buffer.
    """
    if not cf_string:
        return None
    buffer = ctypes.create_string_buffer(256)
    if not core_foundation.CFStringGetCString(
            cf_string, buffer, ctypes.sizeof(buffer), _kCFStringEncodingUTF8):
        return None
    return buffer.value.decode('utf-8', 'replace')


def _dict_value(core_foundation: ctypes.CDLL, dictionary: int, key: str) -> Optional[int]:
    """Return the raw CFTypeRef for a string key, or None if it isn't there."""
    cf_key = core_foundation.CFStringCreateWithCString(
        None, key.encode('utf-8'), _kCFStringEncodingUTF8)
    if not cf_key:
        return None
    try:
        return core_foundation.CFDictionaryGetValue(dictionary, cf_key)
    finally:
        core_foundation.CFRelease(cf_key)


def _dict_int(core_foundation: ctypes.CDLL, dictionary: int, key: str) -> Optional[int]:
    """Return a CFNumber value as an int, or None if it isn't a readable number."""
    cf_number = _dict_value(core_foundation, dictionary, key)
    if not cf_number:
        return None
    result = ctypes.c_int64(0)
    if not core_foundation.CFNumberGetValue(
            cf_number, _kCFNumberSInt64Type, ctypes.byref(result)):
        return None
    return result.value


def _dict_bool(core_foundation: ctypes.CDLL, dictionary: int, key: str) -> Optional[bool]:
    """Return a CFBoolean value as a bool, or None if the key isn't there."""
    cf_boolean = _dict_value(core_foundation, dictionary, key)
    if not cf_boolean:
        return None
    return bool(core_foundation.CFBooleanGetValue(cf_boolean))


def _dict_str(core_foundation: ctypes.CDLL, dictionary: int, key: str) -> Optional[str]:
    """Return a CFString value as a str, or None if the key isn't there."""
    return _to_str(core_foundation, _dict_value(core_foundation, dictionary, key))


def _get_status_iokit() -> Status:
    """Return the status dictionary by reading IOKit's power source info.

    Raises BatteryStatusError if IOKit won't hand over the power source
    blob or list.
    """
    core_foundation, iokit = _load_frameworks()

    blob = iokit.IOPSCopyPowerSourcesInfo()
    if not blob:
        raise BatteryStatusError('IOPSCopyPowerSourcesInfo() returned NULL.')
    try:
        # This reports what the whole computer is running on, which is the
        # answer even on a desktop Mac with no power sources listed below.
        providing = _to_str(
            core_foundation, iokit.IOPSGetProvidingPowerSourceType(blob))
        if providing == _AC_POWER:
            plugged_in = True
        elif providing == _BATTERY_POWER:
            plugged_in = False
        else:
            plugged_in = None  # 'UPS Power', or something new.

        sources = iokit.IOPSCopyPowerSourcesList(blob)
        if not sources:
            raise BatteryStatusError('IOPSCopyPowerSourcesList() returned NULL.')
        try:
            description = None
            for i in range(core_foundation.CFArrayGetCount(sources)):
                source = core_foundation.CFArrayGetValueAtIndex(sources, i)
                candidate = iokit.IOPSGetPowerSourceDescription(blob, source)
                if not candidate:
                    continue
                if _dict_bool(core_foundation, candidate, _KEY_IS_PRESENT) is False:
                    continue
                description = candidate
                break

            if description is None:
                # No power sources at all means a desktop Mac.
                return make_status(False, plugged_in, None, None, None)

            current = _dict_int(core_foundation, description, _KEY_CURRENT_CAPACITY)
            maximum = _dict_int(core_foundation, description, _KEY_MAX_CAPACITY)
            if current is None or not maximum:
                percent = None
            else:
                percent = current * 100.0 / maximum

            charging = _dict_bool(core_foundation, description, _KEY_IS_CHARGING)

            state = _dict_str(core_foundation, description, _KEY_POWER_SOURCE_STATE)
            if plugged_in is None:
                if state == _AC_POWER:
                    plugged_in = True
                elif state == _BATTERY_POWER:
                    plugged_in = False

            # 'Time to Empty' is in minutes. It's -1 while macOS is still
            # working out an estimate, and 0 whenever the battery isn't the
            # thing being drawn from.
            minutes_left = _dict_int(core_foundation, description, _KEY_TIME_TO_EMPTY)
            if state != _BATTERY_POWER or minutes_left is None or minutes_left <= 0:
                minutes_left = None

            return make_status(True, plugged_in, percent, minutes_left, charging)
        finally:
            core_foundation.CFRelease(sources)
    finally:
        core_foundation.CFRelease(blob)


# The `pmset -g batt` fallback route. Its output looks like:
#
#     Now drawing from 'Battery Power'
#      -InternalBattery-0 (id=4653155)	87%; discharging; 4:32 remaining present: true
#
# A charged laptop says "100%; charged; 0:00 remaining", a machine that hasn't
# worked out an estimate yet says "(no estimate)", and a desktop prints only
# the "Now drawing from 'AC Power'" line.

_PMSET_BATTERY_RE = re.compile(
    r'(\d+)%;\s*([^;]+?);\s*(?:(\d+):(\d{2})\s*remaining|\(no estimate\))?', re.IGNORECASE)
_PMSET_PRESENT_RE = re.compile(r'present:\s*(\w+)', re.IGNORECASE)


def _get_status_pmset() -> Status:
    """Return the status dictionary by parsing the `pmset -g batt` output.

    Raises CalledProcessError if the pmset command itself fails.
    """
    output = subprocess.check_output(
        ['pmset', '-g', 'batt'], universal_newlines=True, stderr=subprocess.DEVNULL)

    plugged_in = None
    if "'AC Power'" in output:
        plugged_in = True
    elif "'Battery Power'" in output:
        plugged_in = False

    for line in output.splitlines():
        match = _PMSET_BATTERY_RE.search(line)
        if match is None:
            continue

        present_match = _PMSET_PRESENT_RE.search(line)
        if present_match is not None and present_match.group(1).lower() != 'true':
            continue

        percent = float(match.group(1))
        state = match.group(2).strip().lower()
        charging = state in ('charging', 'finishing charge', 'ac attached')

        if match.group(3) is None:
            minutes_left = None
        else:
            minutes_left = int(match.group(3)) * 60 + int(match.group(4))
            if minutes_left == 0:
                minutes_left = None  # pmset prints 0:00 when there's no estimate.
        if state != 'discharging':
            minutes_left = None

        return make_status(True, plugged_in, percent, minutes_left, charging)

    # No battery lines in the output means a desktop Mac.
    return make_status(False, plugged_in, None, None, None)
