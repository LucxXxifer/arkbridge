include $(TOPDIR)/rules.mk

PKG_NAME:=arkbridge
PKG_VERSION:=0.2.4
PKG_RELEASE:=1

PKG_LICENSE:=CC-BY-NC-4.0
PKG_MAINTAINER:=LucxXxifer

include $(INCLUDE_DIR)/package.mk

define Package/arkbridge
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Generic side-router WAN failover (primary gateway to backup uplink)
  URL:=https://github.com/LucxXxifer/arkbridge
  DEPENDS:=+curl +ip-full +iptables +python3-base
endef

define Package/arkbridge/description
	A small, transparent WAN failover for an OpenWrt side router. It keeps
	traffic on a primary gateway and switches the default route to a backup
	uplink (for example a cellular/USB gateway) when the primary path stops
	working, then switches back automatically. Health is checked with HTTPS
	(TCP/TLS) through a dedicated policy-routing table so the primary path is
	still probed while traffic is on the backup. All values are configurable
	through UCI; nothing is hard coded.
endef

define Build/Compile
endef

define Package/arkbridge/conffiles
/etc/config/arkbridge
endef

define Package/arkbridge/install
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/usr/libexec
	$(INSTALL_CONF) ./files/etc/config/arkbridge $(1)/etc/config/arkbridge
	$(INSTALL_BIN) ./files/etc/init.d/arkbridge $(1)/etc/init.d/arkbridge
	$(INSTALL_BIN) ./files/usr/libexec/arkbridge $(1)/usr/libexec/arkbridge
	$(INSTALL_BIN) ./files/usr/libexec/arkbridge-detect $(1)/usr/libexec/arkbridge-detect
endef

$(eval $(call BuildPackage,arkbridge))
