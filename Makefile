.PHONY: test shellcheck

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

shellcheck:
	@command -v shellcheck >/dev/null || { echo "shellcheck is not installed" >&2; exit 2; }
	@shellcheck --severity=error package/arkbridge-usb/files/etc/init.d/usb-uplink \
		package/arkbridge-usb/files/etc/hotplug.d/net/90-usb-uplink \
		package/arkbridge-usb/files/etc/hotplug.d/usb/90-usb-uplink-mode-switch \
		package/arkbridge-usb/files/usr/sbin/usb-uplink \
		package/arkbridge-usb/files/usr/libexec/usb-uplinkd \
		package/arkbridge-usb/files/usr/libexec/usb-uplink-failoverd \
		package/arkbridge-usb/files/usr/libexec/usb-uplink-modeswitch \
		package/arkbridge-usb/files/usr/libexec/usb-uplink-client-path \
		package/arkbridge/files/etc/init.d/arkbridge \
		package/arkbridge/files/usr/libexec/arkbridge \
		package/arkbridge/files/usr/libexec/arkbridge-detect
