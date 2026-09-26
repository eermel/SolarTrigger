#!/usr/bin/env bash
set -euo pipefail

# Install the SolarTrigger ZWO EAF HID hot-plug rule and repair an EAF that is
# already connected but was left unbound by the kernel.
#
# The ZWO EAF (03c3:1f10) exposes a USB HID interface. On affected Raspberry
# Pi kernels the interface can remain Driver=[none]; libEAFFocuser then reports
# EAFGetNum() == 0 even though lsusb sees the device.

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

RULE_PATH="/etc/udev/rules.d/99-solartrigger-zwo-eaf-hid.rules"
USBHID_BIND="/sys/bus/usb/drivers/usbhid/bind"

if [[ ! -e "$USBHID_BIND" ]]; then
    echo "ERROR: usbhid bind endpoint is unavailable: $USBHID_BIND" >&2
    exit 1
fi

cat > "$RULE_PATH" <<\'UDEVRULES\'
# SolarTrigger - ZWO EAF (03c3:1f10)
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="03c3", ATTR{idProduct}=="1f10", GROUP="users", MODE="0666"
# Bind only an unclaimed HID interface belonging to this exact VID/PID.
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface", ATTR{bInterfaceClass}=="03", ATTRS{idVendor}=="03c3", ATTRS{idProduct}=="1f10", DRIVER=="", RUN+="/bin/sh -c \'echo %k > /sys/bus/usb/drivers/usbhid/bind\'"
UDEVRULES

chmod 0644 "$RULE_PATH"
udevadm control --reload-rules

bound=0
repaired=0

for iface in /sys/bus/usb/devices/*:*; do
    [[ -e "$iface" ]] || continue
    [[ "$(cat "$iface/bInterfaceClass" 2>/dev/null || true)" == "03" ]] || continue

    device="${iface%:*}"
    [[ "$(cat "$device/idVendor" 2>/dev/null || true)" == "03c3" ]] || continue
    [[ "$(cat "$device/idProduct" 2>/dev/null || true)" == "1f10" ]] || continue

    name="$(basename "$iface")"

    if [[ -L "$iface/driver" ]]; then
        driver="$(basename "$(readlink -f "$iface/driver")")"
        if [[ "$driver" == "usbhid" ]]; then
            echo "ZWO EAF HID already bound: $name -> usbhid"
            bound=$((bound + 1))
            continue
        fi
        echo "ERROR: refusing to steal ZWO EAF interface $name from driver $driver." >&2
        exit 1
    fi

    echo "$name" > "$USBHID_BIND"
    repaired=$((repaired + 1))

    if [[ ! -L "$iface/driver" ]] || [[ "$(basename "$(readlink -f "$iface/driver")")" != "usbhid" ]]; then
        echo "ERROR: usbhid did not claim ZWO EAF interface $name." >&2
        exit 1
    fi
    echo "ZWO EAF HID repaired: $name -> usbhid"
done

echo "Installed: $RULE_PATH"
if (( bound == 0 && repaired == 0 )); then
    echo "No connected ZWO EAF found; the udev rule will apply on hot-plug."
fi
