# laptop_battery_status

Reports the battery charge, the time remaining, and whether the computer is
plugged in. Works on Windows, macOS, and Linux, and uses nothing but the
Python standard library. Runs on Python 3.5 and later.

## Installation

    pip install laptop_battery_status

## Quickstart

```python
>>> import laptop_battery_status as lbs
>>> lbs.is_plugged_in()
True
>>> lbs.is_charging()
True
>>> lbs.battery_level()
87.0
>>> lbs.battery_minutes_remaining()
inf
```

Unplug the laptop and the same calls report the runtime left:

```python
>>> lbs.is_plugged_in()
False
>>> lbs.battery_minutes_remaining()
272.0
```

## Functions

| Function | Returns |
| --- | --- |
| `is_plugged_in()` | `True` if running on AC power, otherwise `False`. |
| `battery_level()` | A `float` from `0.0` to `100.0` for the percent of charge left. |
| `battery_minutes_remaining()` | A `float` of the minutes of runtime left, or `float('inf')` while plugged in or while the estimate is unknown. |
| `is_charging()` | `True` if the battery is actively charging, otherwise `False`. |
| `battery_present()` | `True` if a battery is installed, otherwise `False`. |

`battery_minutes_remaining()` returns `float('inf')` whenever the computer
is plugged in, because the runtime isn't limited by the battery then, and
also when the operating system hasn't worked out an estimate yet. Note that
a plugged-in computer whose battery is already full is *not* charging, so
`is_plugged_in()` can return `True` while `is_charging()` returns `False`.

## Machines with no battery

The battery functions raise `NoBatteryError` on a desktop computer or in a
virtual machine. Call `battery_present()` first if that's a case your program
needs to handle:

```python
import laptop_battery_status

if laptop_battery_status.battery_present():
    print('Battery at %s%%' % (laptop_battery_status.battery_level(),))
else:
    print('This computer runs on wall power.')
```

`is_plugged_in()` is the exception: it works fine on a desktop, where it
always returns `True`.

## Unknown values

`battery_level()` returns `None` when a battery is installed but the
operating system reports the charge as unknown, so check it for `None`
before doing arithmetic on it.

`battery_minutes_remaining()` returns `float('inf')` when the operating
system hasn't worked out an estimate yet, which is common for the first
minute or two after unplugging. That means `float('inf')` covers both
"plugged in" and "not known yet"; call `is_plugged_in()` if your program
needs to tell those two apart.

## Exceptions

All exceptions subclass `BatteryStatusError`, which subclasses `RuntimeError`.

* `NoBatteryError` - no battery is installed.
* `UnsupportedPlatformError` - the operating system isn't Windows, macOS, or Linux.
* `BatteryStatusError` - the power information couldn't be read, or the OS
  reported the AC power or charging state as unknown.

## How it works

No third party packages are used.

* **Windows** calls the Win32 `GetSystemPowerStatus()` function through `ctypes`.
* **macOS** calls the IOKit `IOPowerSources` functions through `ctypes`, and
  falls back to parsing the output of `pmset -g batt`.
* **Linux** reads the files under `/sys/class/power_supply`, and falls back to
  parsing the output of `upower` and then `acpi`. The kernel doesn't provide a
  time estimate, so the remaining minutes are calculated from the current
  charge and the rate it's being drawn at, which makes the number jumpier than
  the one in a desktop environment's battery menu.

Machines with more than one battery are reported as a single combined battery.

## License

MIT
