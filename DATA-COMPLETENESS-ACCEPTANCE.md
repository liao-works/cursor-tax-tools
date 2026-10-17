# DATA-COMPLETENESS-ACCEPTANCE

关税数据完整性专项验收记录 · 2026-10-17（Asia/Shanghai）

- 数据仓库：https://github.com/liao-works/cursor-tax-tools（集成分支 `feat/parallel-scraper`）
- 应用仓库：https://github.com/liao-works/liao-tools（`master`）
- **最终正式发布数据：[`data-20261017-41ac6ec18264d24f`](https://github.com/liao-works/cursor-tax-tools/releases/tag/data-20261017-41ac6ec18264d24f)**（[`latest-data`](https://github.com/liao-works/cursor-tax-tools/releases/tag/latest-data) 同字节；首轮发布 `data-20261017-243467fc824dca54` 经深检后由两轮修复迭代取代，全部版本化 tag 可回溯）
  - 501,174,272 字节，SHA-256 与 metadata 一致，SQLite `integrity_check=ok`，16,744 个官方可申报商品编码
  - 官方源版本：GB DBT `v4.0.1630`（as_of 2026-10-17）、XI 公共源 as_of 2026-10-17
  - 完整指标见 §6
- 桌面客户端：**v0.0.18**（含大文件下载修复 PR #31）

---

## 1. P0 — GB 企业条件税率（反倾销/反补贴）正确归属

### 1.1 修复内容（已合并）

| PR | 内容 |
| --- | --- |
| [#51](https://github.com/liao-works/cursor-tax-tools/pull/51) | 生产化同版本正规化条件组件 join（`measure-conditions` / `measure-condition-components` + 凭证、行动、条件代码、币值/计量单位字典），接入 `gb_remedies` → `enterprise_rates` 与发布元数据；weekly GB 采集新增条件组件步骤；发布器传入条件源并校验 |
| [#52](https://github.com/liao-works/cursor-tax-tools/pull/52) | 官方"新版本已注册、表未发布完"窗口期的操作员 `version_pin` 输入 + `discover_pinned_version` 独立表/CSVW 校验（不削弱"禁止自动回退"门槛） |
| [#53](https://github.com/liao-works/cursor-tax-tools/pull/53) | #52 heredoc 缩进修复 |
| [#54](https://github.com/liao-works/cursor-tax-tools/pull/54) | `download_table` 返回值残留引用修复（只有真实网络路径会执行；补真实路径单元测试） |
| [#50](https://github.com/liao-works/cursor-tax-tools/pull/50)（已关闭） | 只读证据 PR，被 #51 生产化取代；其证据结论被独立复现 |

### 1.2 版本消歧规则（关键难点）

同一公开措施 SID 在官方正规化导出中存在多个 tracked-model 修订；对 GB 反倾销措施，**较晚的修订经常是"结构保留、金额被清空"的克隆**（`common-version-groups.current_version_id` 在导出中对现代实体为空，"最新事务=当前"的朴素规则会把官网正在显示的税率错误清空）。

实现的规则（对 2026-10-17 当日 GOV.UK 官网页面逐一验证）：

> 同一条件谱系（measure SID + condition SID）内，选择**事务序最高的、组件含非空 duty amount 的修订**；金额为空的修订不产生税率（fail-closed），选择被标记 `amount_from_older_revision` 与两个事务序号供审计。

### 1.3 全量覆盖证据（v4.0.1629，正式发布内含）

| 指标 | 数值 |
| --- | --- |
| 扩展导出中 551/552/553/554 措施 SID | 9,159 |
| 含条件文本的 SID | 7,588 |
| 条件实体（SID+condition SID） | 17,729 |
| 解析出官方金额的实体 | **16,282**（其中 14,350 依赖"较早修订含金额"规则） |
| 无任何金额的实体（保持空白+计数，不伪造 0%） | 1,447 |
| 凭证/行动签名与扩展导出 `measure__conditions` 文本全量比对 | **7,588/7,588 一致，0 不匹配** |
| 未解析外链 | 0 |
| 进入发布的 GB 条件税率行 / 条件税率表达式 | 6,593 措施 / 14,861 条（中国适用过滤后） |

### 1.4 与同日英国官方页面对照（2026-10-17，GOV.UK 商品页条件弹窗）

| 案例 | 官网显示（当日） | 发布数据 | 结果 |
| --- | --- | --- | --- |
| `1516209821` GB 措施 `20272576`（8A43 Zhuoyue） | D008 → **14.79%**；无凭证 → **54.64%** | 相同 | ✅ 历史 15.68% 候选与"无金额最新修订"均被正确排除 |
| `1516209821` GB 措施 `20272621`（8A44 非抽样配合企业） | D008 → 14.79%；无凭证 → 54.64% | 相同 | ✅ |
| `1516209821` GB `8999`（其他所有海外出口商残余档） | 54.64% | 54.64% | ✅ Other 语义：明确标注残余档，不当作全体企业统一税率 |
| `1516209821` GB 阿根廷 C490（措施 `20041156`） | D017 → 0.00%；D018 → 28.20%；无凭证 → 28.20% | D018 28.2%、无凭证 28.2%；D017 官方导出无金额 → 空白+未解析原因 | ✅ 不把官方导出缺失的金额伪造为 0% |
| `1516209821` XI `89ED` EcoCeres Group | D008 → **10.00%**；无凭证 → **35.60%** | 相同（多条件分别列出） | ✅ |

### 1.5 生产管线接入

- weekly GB 全量（schedule + workflow_dispatch）第 11 步产出 `gb-weekly-condition-components.json`（版本、每表 SHA-256、覆盖计数、签名比对断言），随 artifact 归档。
- 发布器 `--gb-condition-components`：必须 `gb-condition-components-v2`、同 pinned 版本、missing SIDs 为空、签名不匹配为 0，否则**拒绝发布**；metadata 记录 `trade_remedy_coverage.GB.condition_rate_components_collected=true` + 条件源全部表哈希。
- 旧 artifact（无条件文件）仍可发布（`collected=false` + 既有 limitation 文案），保证过渡期管线稳定。

---

## 2. P0 — 完整性核查（GB/XI 官方目录、普通关税、条件税率、反倾销/反补贴）

正式发布 `data-20261017-243467fc824dca54` 的 metadata（可复核）：

| 指标 | GB | XI |
| --- | --- | --- |
| 官方可申报编码 | 16,742 | 16,730 |
| 有明确默认第三国关税（103） | 16,197 | 15,663 |
| 默认税率空缺（全部有 gap 审计记录） | 545 | 1,067 |
| rate_options 源行/投影行 | 70,754/70,754（完整） | 76,453/76,453（完整） |
| 贸易救济源行/中国适用投影行 | 9,945/7,615 | 26,200/19,420 |
| 条件税率行/表达式 | 6,593/14,861 | （既有 XI 条件税率保留） |

### 2.1 历史空默认税率 1,612 条重算与分类（每类均经当日官方页面对照核实）

| 地区 | 原因 | 数量 | 核实结论（抽样对照官网） |
| --- | --- | --- | --- |
| GB | 103 仅授权用途/附加代码条件（type 105、2500/2501 等） | 179 | 正确空白（如 0101291000 仅授权用途 0%，官网无无条件默认税率） |
| GB | 无任何 103 源行 | 7 | Chapter 99 特殊代码，官方无关税措施 |
| GB | 103 行不含中国（全部为 Channel Islands 1080 关税联盟行） | 359 | 官网同日仅显示 "UK-CD Customs Union n/a"，中国原产不适用 |
| XI | 103 存在但需条件/附加代码 | 458 | 正确空白（如 `1516209821` XI 1011 措施 R1652/24 带条件、无金额） |
| XI | 官方 XI 无 103 措施 | 588 | 官网当日核实（如 `2710191190` 仅有授权用途 0% + 低价包裹税 €3.00），**非采集缺口** |
| XI | 103 仅适用 BY/RU（对华排除） | 21 | 官网核实（如 `1507101000` 103 行仅 Belarus/Russia 50%） |

**结论：1,612 条空缺全部为官方源真实状态，未发现实际采集缺口；无静默丢失、无伪造 0%。** 审计文件随发布附带：[`rate-gap-audit.json`](https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/rate-gap-audit.json)（`by_region_reason` 与上表一致）。

### 2.2 版本与来源可追溯

- 每个发布记录 `source_versions`（GB DBT 版本+as_of、XI 目录 SHA+as_of）、`geographical_membership_source`、`xi_geographical_membership_source`（中国组解析证据）、GB 条件源全部 14 张官方表 SHA-256。
- 不可变版本 tag `data-YYYYMMDD-<hash>` 永久保留，`latest-data` 与之字节一致（watch 校验）。

---

## 3. P1 — 更新稳定性

审查了增量触发、周期全量、失效处理、重复任务、异常恢复、哈希校验、回滚七个维度，均有既有机制+测试；发现并修复 2 个实际缺陷：

| 缺陷 | 修复 |
| --- | --- |
| 桌面端 300 秒总超时无法下载 ~500MB 数据包（应用仓库 #31 描述的问题） | PR #31：45s 连接超时 + 150s 每块卡顿超时，保留大小+SHA-256 校验、原子安装、回滚。**已合并进 v0.0.18** |
| 生产 freshness watch 仍是 60MB 时代的库大小上限，对 ~500MB 正式发布直接报 "Release artifact too large"，完全失去日常完整性/新鲜度检查能力（run 39020429980 复现） | PR [#55](https://github.com/liao-works/cursor-tax-tools/pull/55)：上限提高至 1GiB；修复后 watch 对新发布 **通过**（run 39020783070：SHA-256 双副本一致、SQLite 完整、记录数一致、48h 新鲜度 OK） |

既有机制确认（测试覆盖见 `full-gb-xi-foundation.yml` CI，398 项离线测试）：

- **增量自动触发**：每日 00:30 UTC 官方源变化监视 → delta response → GB 复核 / XI 定点重取。
- **周期全量**：XI 每日 02:00 UTC 全量；GB 每周日 01:00 UTC 全量（另加操作员 `version_pin` 通道）。
- **失效日期处理**：`effective_status`（active/expired/future/unknown）贯穿 measures/rate_options/remedies；XI 已知失效重取 + 日期影响队列；GB compact impact queue。
- **重复任务**：每个 workflow 独立 concurrency group（排队不并行重复）；发布器对未变化字节哈希抑制重复客户端通知。
- **异常恢复**：XI checkpoint chain / run plan 重试 / drift guard / 篡改测试；GB 每周全量幂等重放。
- **哈希校验**：发布前 `file_hash` 与字节互验；watch 每日双副本（latest-data + 不可变 tag）SHA-256/大小/SQLite/记录数比对；客户端下载同样校验。
- **回滚**：不可变版本 tag 长期保留（客户端/运维可回指）；客户端原子安装+备份回滚。

---

## 4. P1 — 产品交付（v0.0.18）

- 应用仓库 PR [#31](https://github.com/liao-works/liao-tools/pull/31)（下载修复）已合并；PR #27-#30、#32（企业税率显示/条件税率区分）已合并。
- 发布 PR [#33](https://github.com/liao-works/liao-tools/pull/33)（版本 0.0.18）+ [#34](https://github.com/liao-works/liao-tools/pull/34)（release notes）已合并；tag `v0.0.18` 重新指向包含 release notes 的提交后重新触发多平台构建。
- 正式发布：https://github.com/liao-works/liao-tools/releases/tag/v0.0.18 （Windows x64 NSIS/MSI + macOS Apple Silicon）
- **Windows 实机验收（本机 win32 10.0.26300 x64，2026-10-17）**：
  1. 从官方 Release 下载 `liao-tools_0.0.18_x64-setup.exe`（9,948,242 字节，与发布资产一致）；
  2. NSIS 静默安装成功，`%LOCALAPPDATA%\liao-tools\liao-tools.exe` FileVersion/ProductVersion = **0.0.18**；
  3. 应用启动运行正常（进程与 WebView2 初始化、应用数据目录创建均验证）；
  4. 数据下载链路实测：按客户端完全相同的 URL（`latest-data/metadata.json` → 不可变版本 `tariffs.db`）下载 **496,603,136 字节耗时 97 秒，manifest 大小与 SHA-256 校验全部通过**——该网速下旧版 300 秒总超时已无裕量，v0.0.18 的"连接 45s + 每块 150s 卡顿超时"消除该故障模式；
  5. 查询结果与发布库逐字节核对：`1516209821` GB 默认 10.9%，8A43/8A44 条件税率（D008→14.79% / 无凭证→54.64%）、XI 89ED（D008→10.00% / 无凭证→35.60%）；`0101291000` GB/XI 默认"—"、GB 授权用途 0% 作为条件选项列出。
  - 说明：本会话没有桌面 GUI 自动化工具，应用内按钮级点选未执行；以上第 4-5 步覆盖了同一数据流（同 URL、同校验规则、同库字节），Rust 下载端逻辑由应用仓库 CI（check + tauri-windows 构建）验证。

---

## 5. 第二轮深化检验与采集（2026-10-17 下午）

对首轮发布继续做完整性深检，发现并修复两类真实缺口：

### 5.1 XI 选项 24,528 条 "unresolved" 全量定性（PR [#57](https://github.com/liao-works/cursor-tax-tools/pull/57)）

拆解首轮发布的 24,528 条 `unresolved` rate options（全部 XI、全部 `duty_expression` 未解析）后，对照官方 XI JSON:API 确认为两种不同官方状态：

- **管制类措施**（有机产品 750 / 附加 695 / 废弃物 755 / REACH 761 / F-气体 724 / CITES 710 / 统计 566 等 4.5 万行）：官方 duty_expression 记录存在但为空——**本来就没有关税**。现在标记为 `linked_but_empty`（applicability=other_measure），不再误标 unresolved；type 103 无任何可解析金额时仍保持 unresolved。
- **695 类附加关税**：真实条件税率挂在 `measure_conditions` 上（实证：`0101210000` 措施 `4171349` = 凭证 Y155 → **50.00%**，无凭证 → **0.00%**）。选项投影现在提取 `condition_rates`（表达式+凭证+要求），与救济投影同构。

正式效果（见 §6 最终发布）：`unresolved_rate_options` **24,528 → 0**；XI 选项条件税率 **3,749 项 / 7,620 条表达式**；`xi_control_options_without_duty_expression=24,553`（如实的"管制措施无税率"计数）。

### 5.2 GB 无金额条件的官方 JSON:API 回填（PR [#58](https://github.com/liao-works/cursor-tax-tools/pull/58)、[#59](https://github.com/liao-works/cursor-tax-tools/pull/59)）

官网显示 0.00% 而 DBT 正规化 CSV 导出**丢弃零值金额**（实证：`7208260000` 措施 `20229086` 官网 D008→0.00%/无→0.00%，版本化表中无金额；与 C490 D017 同模式）。官方同主机 JSON:API（`uk/api/commodities/{code}`，XI 公共采集已在 CI 使用同主机）保留真实金额。条件源构建新增受控回填：

- 仅对"选定修订无金额"的空白实体回填；严格 (凭证,行动,条件代码) 签名唯一匹配；**绝不覆盖正规化金额**；每次抓取 SHA-256 入证据；剩余空白显式计数。
- 本地全量真实回放（195 个受影响编码全部抓取）：**1,447 → 恢复 1,391（96.1%）**，仍空白 56，签名冲突 0，抓取失败 0；混合来源验证（20286181 保留 normalized 48.5%，同时回填缺失的 D008 0.00%）。
- 条件源 kind 升级 v3；发布器兼容 v2/v3（滚动发布安全）。

### 5.3 源版本推进

官方发布窗口结束后，GB 采集已从 `v4.0.1629` 推进到 **`v4.0.1630`**（无 pin 正常发现路径，`ready_for_full_data_replay`），XI 同日全量重采排队中（其完成后将再触发一次发布，属常规日更）。

---

## 6. 最终发布（本节取代 §0 的版本号）

**最新正式发布：[`data-20261017-41ac6ec18264d24f`](https://github.com/liao-works/cursor-tax-tools/releases/tag/data-20261017-41ac6ec18264d24f)**（`latest-data` 同字节）

- 501,174,272 字节，SHA-256 与 metadata 一致，SQLite integrity ok，16,744 编码；GB 源 `v4.0.1630`（as_of 2026-10-17）、XI as_of 2026-10-17。
- 生产 freshness watch（run 39032256790）✅。

| 指标 | 数值 |
| --- | --- |
| `unresolved_rate_options` | **0**（首轮 24,528） |
| XI 选项条件税率 | 3,749 项 / 7,620 条表达式 |
| 管制措施无税率（如实计数） | 24,553 |
| GB 条件税率行 / 表达式（中国适用投影） | 6,838 / 16,022（首轮 6,593/14,861） |
| GB 条件表达式总数（含回填） | 17,673（正规化 16,282 + JSON:API 回填 1,391） |
| JSON:API 回填 | 恢复 1,391 / 仍空白 56 / 冲突 0 / 失败 0 / 抓取 195 编码 |
| GB / XI 默认关税编码 | 16,197 / 15,663（**与首轮完全一致——条件金额零泄漏**） |
| gap 审计 | 1,613 条全部有因（v4.0.1630 下 GB no_china_applicable 359→360，官方源自身变化） |
| 抽查（与当日官网一致） | GB 8A43: D008 14.79%/无 54.64%；XI 89ED: 10.00%/35.60%；XI 695: Y155 50.00%/无 0.00%；0101291000 默认空 |

---

## 7. 交付 PR 汇总

| PR | 内容 |
| --- | --- |
| [#51](https://github.com/liao-works/cursor-tax-tools/pull/51) | GB 条件组件生产化（同版本 join + 版本消歧 + 管线接入） |
| [#52](https://github.com/liao-works/cursor-tax-tools/pull/52) / [#53](https://github.com/liao-works/cursor-tax-tools/pull/53) | 操作员 version_pin 通道 + heredoc 修复 |
| [#54](https://github.com/liao-works/cursor-tax-tools/pull/54) | download_table 真实路径修复 |
| [#55](https://github.com/liao-works/cursor-tax-tools/pull/55) | freshness watch 60MB→1GiB 上限修复 |
| [#56](https://github.com/liao-works/cursor-tax-tools/pull/56) | 首轮验收文档 |
| [#57](https://github.com/liao-works/cursor-tax-tools/pull/57) | XI 选项条件税率提取 + 管制措施 linked_but_empty 定性 |
| [#58](https://github.com/liao-works/cursor-tax-tools/pull/58) / [#59](https://github.com/liao-works/cursor-tax-tools/pull/59) | GB 空白条件金额官方 JSON:API 回填（kind v3）+ weekly 断言对齐 |

---

## 8. 剩余缺口（如实声明）

1. **GB 条件仍空白的 56 个实体——已逐个上网核查，确认为忠实空白，非缺口**（详见 §8.1）。
2. **`20272576` 等措施的官方最新修订清空了金额**：数据保留"最后一个含金额的官方状态"（与官网当日显示一致），带 `amount_from_older_revision` 审计标记；官方变更后每周全量自动跟随。
3. **措施数据集范围之外**：VAT、关税配额实时余额不在 DBT measures 数据集内，metadata `rate_scope` 已声明不在此认证范围。
4. XI "Other"（`8999`/`C999` 等）与 GB 残余档按"官方其他企业档位"呈现，不冒充全体企业税率；企业多条件税率均分列。
5. GB 条件源回填使用官方**实时** JSON:API（该数据无版本钉扎，与 XI 公共采集同性质），以 as_of+证据哈希约束；每周全量重采保证时效一致性。

### 8.1 56 个仍空白条件实体逐个核查记录（2026-10-17）

**构成**：56 = 14 个商品编码 × 2 个措施 × 2 条件。全部属于同一组措施——电动自行车反倾销措施（2026/21，2026-07-31 生效）下的两个**授权用途装配条款**：

| 商品编码（14 个，全部核查） | 措施 ×2 | 附加代码 |
| --- | --- | --- |
| 8714911031 / 8714911035 / 8714911039 / 8714913035 / 8714913039 / 8714930019 / 8714942099 / 8714949019 / 8714963090 / 8714991089 / 8714991099 / 8714995091 / 8714995099 / 8714999019 | 各含 SID 20286078-20286105 中的 2 个 | **8835**（辅助电机自行车装配）与 **C549**（非自行车车辆装配，需授权使用授权） |

**在线核查方式与结果**（当日）：

1. 官方 JSON:API（`uk/api/commodities/{code}`）逐编码核对 28 个措施：14/14 编码全部返回——措施级税率 **0.00%**，两个条件（D019/action 27、无凭证/action 07）的 `duty_expression` **官方即为空**。
2. 官网条件弹窗人工核对（`8714911031`，C549 与 8835 两行）：主格 **0.00%**；弹窗条件 D019="使用授权使用海关程序的授权（SI 2018/1249）"→ 动作 **"Apply the mentioned duty"（适用前述措施关税，即 0%）**；无凭证 → **"Measure not applicable"**。两个条件**设计上不含金额**。

**发布数据对照**：28/28 措施均投影措施级 `0%`（正确税率），条件无金额如实保留（不做 0% 伪造——条件语义是"有授权则适用措施 0%，无授权则措施不适用"，不是"0% 条件税率"）。

**结论**：56 个空白实体全部与官方来源一致，属管理性条件，无需修复。`jsonapi_still_blank_entities=56` 即为此审计数。

---

## 9. 复现方式

```bash
# 条件组件 join（与 weekly workflow 相同命令）
python -m src.target_duty.gb_condition_components \
  --version v4.0.1629 --gb-db gb-weekly-measures.db \
  --output gb-weekly-condition-components.json

# 发布（发布器）
python -m src.target_duty.desktop_release_bundle --gb-db ... \
  --gb-condition-components gb-weekly-condition-components.json ... \
  --output-db desktop/tariffs.db --output-metadata desktop/metadata.json

# 生产 watch
python -m src.target_duty.production_freshness_watch --max-age-hours 48 --output watch.json
```

离线测试：`python -m unittest discover -s tests -p 'test_*.py'`（Windows 本地有既存的临时文件句柄型报错，与本次改动无关；Linux CI 全绿）。
