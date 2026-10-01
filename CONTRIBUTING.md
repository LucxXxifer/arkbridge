# Contributing / 貢獻指南

歡迎貢獻 ArkBridge：新裝置支援、修 bug、文件改進。無論是 issue 或 pull
request 都很歡迎。

Contributions to ArkBridge are welcome: new devices, bug fixes and docs.
Issues and pull requests are all appreciated.

## 授權（重要）/ License (important)

本專案採用 **CC BY-NC 4.0**（見 [LICENSE](LICENSE)）：

- 可自由使用、修改、fork、二創；
- **不得商業牟利**；
- 轉載／改作／fork／發佈**必須標示來源與作者**（本 repo 連結）。

This project is **CC BY-NC 4.0** (see [LICENSE](LICENSE)): free to use, modify,
fork and remix; **no commercial use**; **attribution is required** for any
redistribution or derivative (link back to this repo).

## 如何嘗試支援一台新裝置 / How to test a new device

1. 把裝置以 USB 接到 OpenWrt 路由器，觀察它是否變成網路介面：
   - `lsusb`（或看 `/sys/bus/usb/devices/*/idVendor`、`idProduct`）；
   - `ip -br link`（找 `eth*` / `usb*` / `wwan*`）；
   - `cat /sys/class/net/<dev>/device/uevent` 看綁定的 driver
     （`cdc_ether` / `cdc_ncm` / `rndis_host`…）。
2. 若它只以 USB 儲存 / CD-ROM 出現，先用 `usb_modeswitch` / `usbmode` 切到
   網路模式，之後會重新枚舉為網路介面。
3. 確認路由器能取得**帶閘道的 DHCP**：
   - `udhcpc -i <dev>` 或 netifd 上線後，`ip route show dev <dev>` 要看得到
     `default via <gw>`；沒有閘道就不算可用。
4. 把這些填入 `usb-uplink` 的 allowlist（`allowed_ids`、`allowed_drivers`），
   再在 LuCI 面板 (`Services -> ArkBridge`) 按 **Auto-check**，
   或手動填入 preferred/backup 的 gateway 與 device。
5. 驗證能上網（路由器本身、以及 LAN client），並確認主線失效時可切到備援、
   恢復時可切回。

## 回報 / Reporting

若要回報新裝置是否可用，請附：

- 裝置型號 / firmware；
- USB `idVendor:idProduct`（開機前後、切換模式前後都要）；
- driver（`cdc_ether` / `cdc_ncm` / `rndis_host` / MBIM…）；
- 是否有取得帶閘道的 DHCP（`ip route show dev <dev>`）；
- OpenWrt / 路由器型號與版本；
- 成功或失敗的具體輸出。

歡迎以 issue 或 pull request 的形式提供；若你的機型可用，請一併把它的 ID 加進
`usb-uplink` 的 allowlist，並在 [docs/devices.md](docs/devices.md) 的表格新增一列
（型號、USB ID、driver、是否需 modeswitch、是否有帶閘道的 DHCP、OpenWrt 版本、
結果），註明來源。

請把你的機型登錄到 [已測試裝置清單 / docs/devices.md](docs/devices.md)。
