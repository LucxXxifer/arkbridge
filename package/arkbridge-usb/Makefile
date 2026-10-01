include $(TOPDIR)/rules.mk

PKG_NAME:=usb-uplink
PKG_RELEASE:=3

include $(INCLUDE_DIR)/package.mk

define Package/usb-uplink
  SECTION:=net
  CATEGORY:=Network
	TITLE:=USB uplink discovery and fail-closed IPv4 failover coordinator
  URL:=https://github.com/LucxXxifer/arkbridge
  DEPENDS:=+ubus +netifd +uci +procd +jsonfilter +ip-full +usb-modeswitch +kmod-usb-net-cdc-ether
endef

define Package/usb-uplink/description
	Discovers an allowlisted USB network device and attaches it to a
	netifd-managed DHCP interface without bridging it into the LAN. The
	default mode is standby and never installs a default route. Explicit
	failover route control is disabled by default and owns only verified routes.
	Optional USB mode switching is disabled by default and only runs for
	explicitly configured pre-switch USB identities.
endef

define Build/Compile
endef

define Package/usb-uplink/conffiles
/etc/config/usb_uplink
endef

define Package/usb-uplink/install
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/etc/hotplug.d/net
	$(INSTALL_DIR) $(1)/etc/hotplug.d/usb
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_DIR) $(1)/usr/libexec
	$(INSTALL_CONF) ./files/etc/config/usb_uplink $(1)/etc/config/usb_uplink
	$(INSTALL_BIN) ./files/etc/init.d/usb-uplink $(1)/etc/init.d/usb-uplink
	$(INSTALL_BIN) ./files/etc/hotplug.d/net/90-usb-uplink $(1)/etc/hotplug.d/net/90-usb-uplink
	$(INSTALL_BIN) ./files/etc/hotplug.d/usb/90-usb-uplink-mode-switch $(1)/etc/hotplug.d/usb/90-usb-uplink-mode-switch
	$(INSTALL_BIN) ./files/usr/sbin/usb-uplink $(1)/usr/sbin/usb-uplink
	$(INSTALL_BIN) ./files/usr/libexec/usb-uplinkd $(1)/usr/libexec/usb-uplinkd
	$(INSTALL_BIN) ./files/usr/libexec/usb-uplink-failoverd $(1)/usr/libexec/usb-uplink-failoverd
	$(INSTALL_BIN) ./files/usr/libexec/usb-uplink-modeswitch $(1)/usr/libexec/usb-uplink-modeswitch
	$(INSTALL_BIN) ./files/usr/libexec/usb-uplink-client-path $(1)/usr/libexec/usb-uplink-client-path
endef

$(eval $(call BuildPackage,usb-uplink))
