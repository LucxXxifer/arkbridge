module("luci.controller.arkbridge", package.seeall)

function index()
	local page = entry({"admin", "services", "arkbridge"},
		cbi("arkbridge/overview"), _("ArkBridge"), 62)
	page.acl_depends = { "luci-app-arkbridge" }
	page.dependent = false

	local detect = entry({"admin", "services", "arkbridge", "detect"},
		call("action_detect"))
	detect.leaf = true
	detect.acl_depends = { "luci-app-arkbridge" }
	local status = entry({"admin", "services", "arkbridge", "status"},
		call("action_status"))
	status.leaf = true
	status.acl_depends = { "luci-app-arkbridge" }
end

function action_detect()
	local sys = require "luci.sys"
	local http = require "luci.http"
	http.prepare_content("application/json")
	http.write(sys.exec("/usr/libexec/arkbridge-detect 2>/dev/null") or "{}")
end

function action_status()
	local sys = require "luci.sys"
	local http = require "luci.http"
	http.prepare_content("application/json")
	http.write(sys.exec("/usr/libexec/arkbridge status 2>/dev/null") or "{}")
end
