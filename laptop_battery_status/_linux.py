"""Linux backend.

The /sys/class/power_supply files are read directly. If those aren't usable
(inside some containers, for instance) the output of the `upower` and then
the `acpi` commands is parsed instead.
"""

import os
import re
import subprocess
from typing import Dict, List, Optional

from ._common import BatteryStatusError, Status, make_status

POWER_SUPPLY_DIR = '/sys/class/power_supply'

# Supply types in the sysfs "type" file that provide wall power.
_MAINS_TYPES = ('Mains', 'USB', 'UPS', 'Wireless')


def get_status() -> Status:
    """Return the status dictionary for this machine. See _common.make_status().

    Tries the sysfs, upower, and acpi readers in turn, using the first one
    that reports anything usable. Raises BatteryStatusError, listing what
    each reader complained about, only when all three come up empty.
    """
    errors = []
    for reader in (_get_status_sysfs, _get_status_upower, _get_status_acpi):
        try:
            status = reader()
        except Exception as error:
            errors.append('%s: %s' % (reader.__name__, error))
            continue
        if status is not None:
            return status
        errors.append('%s: no power supply information found' % (reader.__name__,))
    raise BatteryStatusError(
        'Could not read the power status on this Linux machine. ' + '; '.join(errors))


# The /sys/class/power_supply route.


def _read_file(*path_parts: str) -> Optional[str]:
    """Return the stripped contents of a sysfs file, or None if unreadable.

    Reads of sysfs files can fail with OSError even when the file exists,
    such as when a driver doesn't implement that attribute.
    """
    try:
        with open(os.path.join(*path_parts)) as file_object:
            return file_object.read().strip()
    except (IOError, OSError):
        return None


def _read_int(*path_parts: str) -> Optional[int]:
    """Return the contents of a sysfs file as an int, or None if that fails.

    None covers an unreadable file and a file whose contents aren't an
    integer, since neither is useful to a caller.
    """
    value = _read_file(*path_parts)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _get_status_sysfs() -> Optional[Status]:
    """Return the status dictionary from /sys/class/power_supply.

    Returns None, rather than raising, when the directory is missing or
    holds nothing useful, which is get_status()'s signal to move on to the
    next reader. This happens inside some containers.
    """
    if not os.path.isdir(POWER_SUPPLY_DIR):
        return None

    try:
        entries = sorted(os.listdir(POWER_SUPPLY_DIR))
    except OSError:
        return None

    batteries = []
    mains_found = False
    mains_online = False

    for entry in entries:
        path = os.path.join(POWER_SUPPLY_DIR, entry)
        supply_type = _read_file(path, 'type')

        if supply_type == 'Battery':
            # A "Device" scope means a peripheral's battery, such as a
            # wireless mouse, and has nothing to do with this computer.
            if _read_file(path, 'scope') == 'Device':
                continue
            batteries.append(path)
        elif supply_type in _MAINS_TYPES:
            online = _read_int(path, 'online')
            if online is not None:
                mains_found = True
                if online:
                    mains_online = True

    if not batteries:
        if not mains_found:
            return None  # Nothing here is useful, so try the next reader.
        return make_status(False, mains_online, None, None, None)

    percent = _sysfs_percent(batteries)
    states = [_read_file(path, 'status') for path in batteries]
    discharging = 'Discharging' in states
    charging = 'Charging' in states

    if mains_found:
        plugged_in = mains_online
    elif discharging:
        plugged_in = False
    elif charging or 'Full' in states or 'Not charging' in states:
        plugged_in = True
    else:
        plugged_in = None

    minutes_left = _sysfs_minutes_left(batteries) if discharging else None

    return make_status(True, plugged_in, percent, minutes_left, charging)


def _sysfs_percent(batteries: List[str]) -> Optional[float]:
    """Return the combined charge of the batteries as a percentage, or None."""
    # energy_* is in microwatt-hours and charge_* is in microamp-hours. Either
    # one gives the right ratio, so long as both halves come from the same pair.
    for now_name, full_name in (('energy_now', 'energy_full'),
                                ('charge_now', 'charge_full')):
        total_now = 0
        total_full = 0
        for path in batteries:
            now = _read_int(path, now_name)
            full = _read_int(path, full_name)
            if now is None or not full:
                total_full = 0
                break
            total_now += now
            total_full += full
        if total_full:
            return total_now * 100.0 / total_full

    # Fall back to the kernel's own percentage, which is all that some
    # drivers report. Averaging is the best that can be done without knowing
    # each battery's capacity.
    raw_percentages = [_read_int(path, 'capacity') for path in batteries]
    percentages = [value for value in raw_percentages if value is not None]
    if not percentages:
        return None
    return sum(percentages) / float(len(percentages))


def _sysfs_minutes_left(batteries: List[str]) -> Optional[float]:
    """Return the minutes of runtime left, or None if it can't be worked out."""
    # The kernel doesn't report a time estimate, so it's charge divided by the
    # rate it's being drawn at. Both halves of a pair use the same units
    # (microwatt-hours over microwatts, or microamp-hours over microamps), so
    # the units cancel out and leave hours.
    for now_name, rate_name in (('energy_now', 'power_now'),
                                ('charge_now', 'current_now')):
        total_now = 0
        total_rate = 0
        usable = True
        for path in batteries:
            now = _read_int(path, now_name)
            rate = _read_int(path, rate_name)
            if now is None or rate is None:
                usable = False
                break
            total_now += now
            total_rate += abs(rate)  # Some drivers report a negative rate.
        if usable and total_rate > 0:
            return total_now * 60.0 / total_rate
    return None


# The `upower` fallback route. The DisplayDevice is UPower's own summary of
# every battery on the machine, so there's no need to enumerate them:
#
#     $ upower -i /org/freedesktop/UPower/devices/DisplayDevice
#       native-path:          dbus
#       power supply:         yes
#         state:               discharging
#         percentage:          87%
#         time to empty:       4.3 hours

_UPOWER_DISPLAY_DEVICE = '/org/freedesktop/UPower/devices/DisplayDevice'
_UPOWER_FIELD_RE = re.compile(r'^\s*([\w -]+?):\s+(.*?)\s*$')
_UPOWER_TIME_RE = re.compile(r'([\d.]+)\s*(\w+)')

_SECONDS_PER_UNIT = {
    'second': 1.0, 'seconds': 1.0,
    'minute': 60.0, 'minutes': 60.0,
    'hour': 3600.0, 'hours': 3600.0,
    'day': 86400.0, 'days': 86400.0,
}


def _upower_fields(device_path: str) -> Dict[str, str]:
    """Return the "name: value" lines of `upower -i` as a dictionary of strings.

    Raises CalledProcessError if the upower command itself fails.
    """
    output = subprocess.check_output(
        ['upower', '-i', device_path], universal_newlines=True, stderr=subprocess.DEVNULL)
    fields = {}
    for line in output.splitlines():
        match = _UPOWER_FIELD_RE.match(line)
        if match is not None:
            fields[match.group(1).strip()] = match.group(2)
    return fields


def _get_status_upower() -> Optional[Status]:
    """Return the status dictionary by parsing the `upower -i` output.

    Returns None when upower reports no usable state, which is
    get_status()'s signal to move on to the next reader.
    """
    fields = _upower_fields(_UPOWER_DISPLAY_DEVICE)

    state = fields.get('state', '').lower()
    if not state or state == 'unknown':
        return None

    percent = None
    if 'percentage' in fields:
        try:
            percent = float(fields['percentage'].rstrip('%'))
        except ValueError:
            percent = None

    minutes_left = None
    if state == 'discharging':
        match = _UPOWER_TIME_RE.search(fields.get('time to empty', ''))
        if match is not None:
            seconds = _SECONDS_PER_UNIT.get(match.group(2).lower())
            if seconds is not None:
                minutes_left = float(match.group(1)) * seconds / 60.0

    charging = state == 'charging'
    plugged_in = state != 'discharging'

    return make_status(True, plugged_in, percent, minutes_left, charging)


# The `acpi` fallback route:
#
#     $ acpi -b -a
#     Battery 0: Discharging, 87%, 04:32:15 remaining
#     Adapter 0: on-line

_ACPI_BATTERY_RE = re.compile(r'^Battery\s+\d+:\s*(.*)$', re.IGNORECASE)
_ACPI_ADAPTER_RE = re.compile(r'^Adapter\s+\d+:\s*(\S+)', re.IGNORECASE)
_ACPI_PERCENT_RE = re.compile(r'(\d+)%')
_ACPI_TIME_RE = re.compile(r'(\d+):(\d{2}):(\d{2})')


def _get_status_acpi() -> Optional[Status]:
    """Return the status dictionary by parsing the `acpi -b -a` output.

    Returns None when the output mentions neither a battery nor an adapter,
    which is get_status()'s signal that this reader had nothing to offer.
    """
    output = subprocess.check_output(
        ['acpi', '-b', '-a'], universal_newlines=True, stderr=subprocess.DEVNULL)

    plugged_in = None
    percent = None
    minutes_left = None
    charging = None
    discharging = False
    battery_found = False

    for line in output.splitlines():
        line = line.strip()

        adapter_match = _ACPI_ADAPTER_RE.match(line)
        if adapter_match is not None:
            plugged_in = adapter_match.group(1).lower().startswith('on')
            continue

        battery_match = _ACPI_BATTERY_RE.match(line)
        if battery_match is None or battery_found:
            continue  # Only the first battery gets reported.

        # A battery line is comma separated, as in
        # "Battery 0: Discharging, 87%, 04:32:15 remaining".
        parts = [part.strip() for part in battery_match.group(1).split(',')]
        state = parts[0].lower()
        rest = ' '.join(parts[1:])

        percent_match = _ACPI_PERCENT_RE.search(rest)
        if state == 'unknown' and percent_match is None:
            continue  # An empty battery slot.
        battery_found = True

        if percent_match is not None:
            percent = float(percent_match.group(1))
        charging = state == 'charging'
        discharging = state == 'discharging'

        # The same "hh:mm:ss" is time until empty when discharging and time
        # until full when charging, so only the discharging one is useful.
        time_match = _ACPI_TIME_RE.search(rest)
        if state == 'discharging' and time_match is not None:
            minutes_left = (int(time_match.group(1)) * 60
                            + int(time_match.group(2))
                            + int(time_match.group(3)) / 60.0)

    if not battery_found:
        if plugged_in is None:
            return None
        return make_status(False, plugged_in, None, None, None)

    if plugged_in is None:
        # There was no "Adapter" line, so infer AC power from the battery.
        if charging:
            plugged_in = True
        elif discharging:
            plugged_in = False

    return make_status(True, plugged_in, percent, minutes_left, charging)
