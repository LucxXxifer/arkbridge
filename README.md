# ArkBridge

[English](#english) | [繁體中文](#繁體中文)

Keep your network online. ArkBridge is a lightweight **WAN failover** for
OpenWrt / iStoreOS **side routers and main routers**: it keeps traffic on your
primary connection and automatically falls back to a USB / mobile-WiFi uplink
when the primary drops, then switches back when it recovers.

- Engine: `package/arkbridge` (`arkbridge`)
- LuCI panel: `package/luci-app-arkbridge` (`luci-app-arkbridge`)
- USB uplink discovery: `package/arkbridge-usb` (`usb-uplink`)

License: **CC BY-NC 4.0** — free to use, fork and remix with attribution; no
commercial use. See [LICENSE](LICENSE).

---

## English

### What it is

ArkBridge bridges a backup internet path into your router. Put a USB / mobile
WiFi (4G/5G) stick behind the router, and ArkBridge makes it a **fallback**
uplink — no manual cable swapping, no downtime when your main line blips.

It works as a **side router** (the main router stays the gateway and ArkBridge
is the fallback), as a **main router** (internet is the uplink), and can carry
an **aggregate** role where broadband and mobile coexist.

### Highlights

- **Automatic failover and fail-back.** Primary down → backup up; primary
  back → primary restored.
- **Smarter health checks.** Uses HTTPS (TCP/TLS) instead of plain ICMP, and
  probes the primary through its own routing table so recovery is detected even
  while you are on the backup.
- **Works alongside a transparent proxy** (e.g. shellcrash/clash): the probe
  measures the real path, not the proxy.
- **Device-agnostic.** Any interface that provides its own gateway can be the
  backup — a USB modem, a mobile WiFi, or a second line.
- **Simple, safe and readable.** UCI-configured, fail-closed by default, and
  every switch is verified and rolled back on failure.

### Modes

| Mode | Role | Primary | Backup |
|---|---|---|---|
| `side` | Side router (旁路由) | Main router on the LAN | USB / mobile uplink |
| `main` | Main router | Wired WAN (optional) | USB / mobile uplink |
| `aggregate` | Main router | Broadband | USB / mobile uplink |

### Panel

After installing the panel it appears under **Services → ArkBridge**:

- **Status** — current path (primary/backup), service state, probe health,
  default route and last switch, refreshed live.
- **Settings** — mode, enable, preferred and backup gateway/device, probe
  targets, and an **Auto-check** button that fills the fields from your live
  interfaces.

See [docs/arkbridge.md](docs/arkbridge.md) for details.

### Supported devices

Validated with a **Huawei E6878 / E6878-370 (5G Mobile WiFi)**
(`12d1:14db` → `cdc_ether`). Other USB modems / mobile WiFi are untested but
the engine is device-agnostic — see
[docs/devices.md](docs/devices.md) and add your model.

### Documentation

- [docs/arkbridge.md](docs/arkbridge.md) — engine and panel reference
- [docs/devices.md](docs/devices.md) — tested-device list
- [docs/related-projects.md](docs/related-projects.md) — comparison with mwan3 and others
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribute and test a new device

### Contributing

Contributions are welcome — new devices, bug fixes, docs. Fork the project,
open a pull request, and see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 繁體中文

讓網路不中斷。ArkBridge 是給 OpenWrt / iStoreOS **旁路由與主路由**使用的輕量
**WAN 故障轉移**：平時走主要線路，主線斷線或波動時自動切到 USB／移動 WiFi 上行
保底，恢復後自動切回。

- 引擎：`package/arkbridge`（`arkbridge`）
- LuCI 面板：`package/luci-app-arkbridge`（`luci-app-arkbridge`）
- USB 上行探索：`package/arkbridge-usb`（`usb-uplink`）

授權：**CC BY-NC 4.0** — 可自由使用、fork、二創；不得商業牟利；敬請標示來源。
見 [LICENSE](LICENSE)。

### 這是什麼

ArkBridge 把一條備援上網路徑接進你的路由器。只要在路由器後面接一支
USB／移動 WiFi（4G/5G），ArkBridge 就能把它變成**保底上行**——不必手動換線，
主線抖動時也不掉線。

它可作為**旁路由**（主路由仍是閘道，ArkBridge 負責保底）、**主路由**
（以 USB 上行上網），或**聚合**角色（寬頻與行動網路並存）。

### 特色

- **自動切換與切回。** 主線斷 → 走備援；主線恢復 → 自動切回。
- **更聰明的健康檢查。** 以 HTTPS（TCP/TLS）探測取代單純 ICMP，並讓主線探測
  走獨立路由表，即使已在備援也能偵測主線恢復。
- **可與透明代理共存**（如 shellcrash/clash）：探測量測的是真實路徑，而非代理。
- **與裝置無關。** 任何能提供自身閘道的介面都能當備援——USB 網卡、移動 WiFi
  或第二條線路皆可。
- **簡單、安全、好讀。** UCI 設定、預設 fail-closed，每次切換都經驗證，失敗自動回滾。

### 模式

| 模式 | 角色 | 主要 | 備援 |
|---|---|---|---|
| `side` | 旁路由 | LAN 上的主路由 | USB／移動上行 |
| `main` | 主路由 | 有線 WAN（可選） | USB／移動上行 |
| `aggregate` | 主路由 | 寬頻 | USB／移動上行 |

### 面板

安裝面板後，位於 **服務 → ArkBridge**：

- **狀態** — 目前路徑（primary/backup）、服務狀態、探測健康、預設路由與
  最近切換，即時更新。
- **設定** — 模式、啟用、主要與備援的閘道/裝置、探測目標，以及
  **Auto-check** 按鈕（依實際介面自動填入欄位）。

詳見 [docs/arkbridge.md](docs/arkbridge.md)。

### 支援裝置

已驗證 **Huawei E6878 / E6878-370（5G 隨行 WiFi）**（`12d1:14db` → `cdc_ether`）。
其他 USB 網卡／移動 WiFi 尚未實測，但引擎與裝置無關——見
[docs/devices.md](docs/devices.md)，歡迎登錄你的機型。

### 文件

- [docs/arkbridge.md](docs/arkbridge.md) — 引擎與面板參考
- [docs/devices.md](docs/devices.md) — 已測試裝置清單
- [docs/related-projects.md](docs/related-projects.md) — 與 mwan3 等專案的對比
- [CONTRIBUTING.md](CONTRIBUTING.md) — 貢獻方式與新裝置測試

### 貢獻

歡迎貢獻——新裝置支援、修 bug、文件。Fork 本專案、發 pull request，並參閱
[CONTRIBUTING.md](CONTRIBUTING.md)。
