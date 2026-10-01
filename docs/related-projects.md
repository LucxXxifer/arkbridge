# 類似專案對比 / Related projects comparison

本文件把社群中與本專案（`arkbridge` + Huawei E6878 USB 備援）功能相近的
專案做一次整理與對比，並記下「可取之處」。

## 參考來源

- mwan3（OpenWrt 官方事實標準多 WAN 管理器）：`mwan3` / `luci-app-mwan3`；
  已有 nftables 移植版 `dl12345/mwan3`、`dl12345/luci-app-mwan3`。
- Dariusz Więckiewicz，*Adding a second internet connection to a router with
  OpenWrt*（4G USB + mwan3 實作指南）：
  <https://dariusz.wieckiewicz.org/en/adding-second-internet-connection-router-openwrt>
- `GTANAdam/openwrt-wan-failover-script`（純 shell + cron + 狀態網頁，★12）
- `belliash/wanmonitor`（UCI 設定、單一檔案、支援 PPPoE/行動網路，★2，已封存）
- `pgaufillet/openwrt-ha-feed`（OpenWrt 路由器 HA 叢集：keepalived VRRP、設定同步、DHCP 租約同步）
- `larsonzh/owmwpprt`（基於 mwan3 的多 WAN 口策略路由分流，中國 ISP 網段庫）
- `andyfanybo/openwrt-mosdns-pbr-split`（MosDNS + pbr 國內外分流，國外走 4G/備用）
- `Ysurac/openmptcprouter`（MPTCP 多線聚合，需 VPS，★2510）
- `SmoothWAN/SmoothWAN`（Speedify 綁定/無縫切換，已日落，★346）
- `tmscahill/CamperWifi`（RV 多 WAN：Ethernet → Wi-Fi → iPhone USB tethering 故障轉移）
- `amaleky/WrtMate`、`bertrandmartel/openwrt-mwan-config`（配置工具/範例）

## 對比表

| 專案 | 方式 | 監測 | 切換對象 | 聚合 | 依賴 | 面板 | 與本專案差異 |
|---|---|---|---|---|---|---|---|
| **本專案** `arkbridge` | 自寫 shell + procd + LuCI | HTTPS 探測（策略表繞行、可繞過透明代理） | default route（旁路由或主路由） | 不做（僅警告） | uci/iptables/ip/curl | 有（Lua CBI） | 輕量、單一職責、來源/裝置無關、可主/旁路由 |
| `mwan3` | 官方 iptables/nftables 框架 | ping / tracking host / 連線數 | per-WAN 策略路由（mark + ip rule） | 有（負載均衡） | mwan3 + ipset/nft | luci-app-mwan3 | 最成熟、功能最全；設定較重、與 shellcrash/旁路由整合需處理 |
| `GTANAdam` failover script | 純 shell + cron | ping | 路線/default | 無 | 無（純腳本） | 內建狀態網頁 | 概念最接近；但硬編碼、無 UCI、無面板、無策略探測 |
| `wanmonitor` | shell + UCI（單檔） | 介面狀態 + 反覆 ping | 主/備 WAN | 有限（2 介面） | 無 | 無 | 好範例：UCI 化、可讀性高；但已封存、無面板、無 HTTPS 探測 |
| `openwrt-ha-feed` | keepalived VRRP 叢集 | VRRP 心跳 | 整台路由器（VIP） | 無 | 2+ 路由器 | 有 | 層級不同（裝置級 HA）；可互補 |
| `owmwpprt` | mwan3 配套腳本 | 由 mwan3 | per-WAN 網段分流 | 有 | mwan3/nft/ipset | 無（CLI） | 中國 ISP 網段庫很實用 |
| `mosdns-pbr-split` | MosDNS + pbr | — | 國內/國外分流 | 無 | fw4/nft、pbr | 部分 | 分流而非故障轉移；可作進階搭配 |
| `OpenMPTCProuter` | MPTCP + VPS | MPTCP | 多線聚合 | 有（真聚合） | VPS | 有 | 需伺服器；對「保底」過重 |
| `SmoothWAN` | Speedify 綁定 | Speedify | 綁定/切換 | 有 | Speedify 授權 | 有 | 需商業服務；已日落 |

## 本專案相對的定位

- **最輕量**：不引入 mwan3/ipset/nft，只用 `ip`/`iptables`/`uci`/`curl`，適合
  iStoreOS/京東雲這類 fw3 環境，也避免與 shellcrash 的規則互相打架。
- **探測方式不同**：以 **HTTPS（TCP/TLS）** 探測，且經**獨立策略表**，可在「已在
  備援」時仍正確偵測主線恢復；並可**繞過本機透明代理**（shellcrash），量測原始路徑。
- **明確的「主/旁路由」角色**：同一引擎支援主路由、旁路由（保底）、聚合（僅警告）。
- **裝置無關**：不限 Huawei，任何能提供獨立上游閘道的介面皆可。

## 可取之處（值得借鏡）

1. **mwan3 的成熟度**：mark bitmask + 多層 ip rule（per-interface 三層 + 全域策略）
   是處理「多 WAN + 分流 + 負載均衡」的完整典範；若要長期維護多線，值得直接採用
   或參考其規則分層。其 nftables 移植版解決了 23.05+ `ipset` 不可用的問題。
2. **wanmonitor 的 UCI 單檔設定**：所有參數集中在 `/etc/config/wanmonitor`，
   可讀性高、易於文件化（本專案已採用 UCI）。
3. **GTANAdam 的狀態網頁**：提供一個簡單的「目前走哪條線」狀態頁，對使用者體驗很
   友善（本專案可考慮加一個 `/status` 端點或面板狀態顯示）。
4. **HA feed 的設定/DHCP 同步**：把「多台路由器」也視為可用性的一環；本專案目前是
   單機故障轉移，這是另一個層次的可靠度。
5. **owmwpprt / mosdns+pbr 的分流**：若需求是「國內走主線、國外走備援/代理」，
   這種分流比單純故障轉移更貼近日常；可作為本專案「保底」之外的進階選項。
6. **OpenMPTCProuter/SmoothWAN 的聚合**：真聚合需要伺服器或商業服務；本專案刻意
   不做聚合、只警告延遲風險，方向正確（避免行動鏈路亂序與 IP 變動）。
7. **設定分層與停用語意**：多數專案都把「監測參數」與「切換策略」分開（interval、
   失敗次數、恢復次數、冷卻）；本專案已具備，但可再文件化得更清楚。

## 建議的後續（可選）

- 在 LuCI 面板加一個**狀態顯示**（目前 primary/backup、最近切換時間），參考
  GTANAdam 的狀態頁與 mwan3 的 Status 頁。
- README 增列「與 mwan3 的取捨」段落：若要聚合/分流 → mwan3；只要保底且要跟
  shellcrash 共存 → 本專案。
- 可增加 `backup_probe_targets` 的建議值與範例（真實對外探測）。
