# 已測試裝置清單 / Tested device list

本表登錄各型號「作為 USB 上行備援」的實測結果。歡迎以 PR 新增你的裝置。
This table records how each model behaves as a USB backup uplink. Add your
device via a pull request.

## 圖例 / Legend

- **USB ID**：開機（或切模式後）枚舉的 `idVendor:idProduct`。
- **Driver**：`cdc_ether` / `cdc_ncm` / `rndis_host` / `cdc_mbim` …
- **DHCP+GW**：是否取得**帶閘道**的 DHCP（`ip route show dev <dev>` 可見 `default via`）。
- **Result**：`working`（可用）/ `needs-modeswitch`（需先切模式）/ `partial` / `no`（不可用）/ `untested`。

## 表格 / Table

| Model | Type | USB ID | Driver | Mode switch | DHCP+GW | OpenWrt | Engine | Result | Reporter | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Huawei E6878 | 5G mobile WiFi | `12d1:14db` | `cdc_ether` | no (data cable) | yes | iStoreOS 24.10 | arkbridge | working | LucxXxifer | validated; `12d1:1c20` = storage, `12d1:1f01` = transitional |
| Huawei E6878-370 | 5G mobile WiFi | `12d1:14db` | `cdc_ether` | no | yes | iStoreOS 24.10 | arkbridge | working | LucxXxifer | same hardware family as E6878 |
| _(your device)_ | | | | | | | | untested | | |

## 新增方式 / How to add a row

1. 依 [CONTRIBUTING.md](../CONTRIBUTING.md) 的步驟測你的裝置。
2. 收集：型號、類型（4G/5G CPE / mobile WiFi / USB dongle）、USB ID（切模式前後）、
   driver、是否需要 modeswitch、是否有帶閘道的 DHCP、OpenWrt 版本。
3. 新增一列，`Result` 用上面的圖例；`Reporter` 填你的 GitHub 名稱。
4. 發 PR。

## 評估指引 / Likely to work?

- **很可能可用**：在 Linux/OpenWrt 上以 `cdc_ether` / `cdc_ncm` 出現網路介面，
  且拿到帶閘道的 DHCP。
- **需先切模式**：開機為 USB 儲存 / CD-ROM 的華為/中興複合型數據機，先用
  `usb_modeswitch` / `usbmode` 切成網路模式。
- **可能不行**：只支援 Wi-Fi 共享、只提供 Windows 專屬驅動（純 MBIM/NDIS），
  或被廠商工具鎖定的機型。RNDIS 在 OpenWrt 上比 CDC 不穩。

> 引擎本身與型號無關；只要能提供獨立上游閘道即可。此清單記錄的是「實測過」
> 的型號，未列入者一律視為未測試。
