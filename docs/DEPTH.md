# 深度登记 pricelift（促销价格弹性与增量测算）

- 规模：中型（7 个功能模块 / 7 个页面 / 80 个文件 / 90 条测试 / 18 条陷阱）
- 核心规则一：**对数线性弹性**——`ln q = a + b·ln p` 用最小二乘估计，先对 x 与 y 双中心化再求比值（漏掉中心化斜率会变正）；0 销量默认按 `ln(q+0.5)` 统一平移，可切到剔除；输出斜率、标准误、t 值与 R²，`|t| < 1.96` 记为不显著。
- 核心规则二：**增量测算与区间**——基线 = 促销前 14 天日均 × 促销有效天数 × 对照组同期倍数；增量 = 实际 − 基线（Decimal，2 位）；区间宽度用弹性斜率的 delta 法给出，斜率标准误为 0 时区间收成一点。
- 核心规则三：**实验设计**——统计单位是「一天一条日销售额」；`k = z(1−α/2) + z(功效)`，`最小实验期 n = (A² + sqrt(A⁴ + 4·A²·m))/2`（`A = k·cv/目标效应`，`m = 14` 天基线窗）；方差用 HC1 稳健标准误与朴素标准误的比值放大；缺日必须按三种补法各算一遍，结论不一致就标出来。
- 使用者与工作流：牧原乳品区域销售部 6 人，每周十余个促销，增量靠经验拍。现在导入日销与促销日历即可得到「弹性 + 增量 + 区间 + 五账」，再按下一期目标效应算实验期、对照与预期区间，每条结论都能追到行号与原因码。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 采信 | 导入日销/促销/对照组三份文件，看到逐文件诊断与空态结论 | POST /api/import、页面 / | `app/modules/m1_ingest/service.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/sniff.py` | `tests/test_ingest.py` |
| M2 归价 | 查看列契约、换算规则、促销版本与生效窗口，试算单条价格 | GET /api/contract、POST /api/price/preview、页面 /contract | `app/modules/m2_price/contract.py` `app/modules/m2_price/coerce.py` `app/modules/m2_price/versions.py` | `tests/test_price.py` |
| M3 质量隔离 | 看严重度分布与隔离行，导出隔离 CSV | GET /api/quarantine、GET /api/quarantine.csv、页面 /quarantine | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` `app/modules/m3_quality/metrics.py` | `tests/test_quality.py` |
| M4 弹性 | 看每个商品与每个渠道的弹性、标准误、R²、显著性 | GET /api/elasticity、页面 /elasticity | `app/modules/m4_elastic/prepare.py` `app/modules/m4_elastic/ols.py` `app/modules/m4_elastic/fit.py` | `tests/test_elasticity.py` |
| M5 增量与批次 | 触发增量测算、看区间与渠道分摊、查批次历史与幂等重跑 | POST /api/lift、GET /api/batches、页面 /lift | `app/modules/m5_lift/baseline.py` `app/modules/m5_lift/ci.py` `app/modules/m5_lift/spread.py` `app/modules/m5_lift/service.py` | `tests/test_lift.py` |
| M6 报告导出 | 核对五账，导出增量/弹性/五账/运行清单 | GET /api/report、GET /api/export、页面 /report | `app/modules/m6_report/csv_out.py` `app/modules/m6_report/manifest_out.py` `app/modules/m6_report/service.py` | `tests/test_report.py` |
| M7 实验设计 | 给目标效应与功效，算最小实验期、预算下可识别效应、对照建议与预期区间 | POST /api/design、GET /api/design、页面 /design | `app/modules/m7_design/power.py` `app/modules/m7_design/robust.py` `app/modules/m7_design/impute.py` `app/modules/m7_design/control.py` `app/modules/m7_design/service.py` | `tests/test_design.py` `tests/test_design_api.py` |

端到端：`tests/test_e2e.py`（真实样例 → 隔离 → 弹性 → 增量 → 报告 → 设计 → 导出，含 422 失败输入与幂等重跑）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 日销文件声明 utf-8，字节其实是 GB18030 | 按声明解码直接抛，或 `replace` 后中文列名乱码 | 声明只作首个候选，严格解码失败回退 gb18030，并记 `E_DECODE_FALLBACK` | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_gb18030_falls_back_and_keeps_chinese_header` |
| TRAP-02 | 导出的 CSV 带 UTF-8 BOM | 首列名变成不可见字符前缀，按列名取值全空 | 解码前剥离 BOM 并留 `bom_stripped` 标记 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_bom_stripped_and_duplicate_header_counted` |
| TRAP-03 | 商品名称单元格内含换行 | `splitlines()` 把一条记录切成两条，列全错位 | 交给 `csv` 模块按引号流式解析，记录数与物理行数分开统计 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_quoted_newline_keeps_one_record` |
| TRAP-04 | 分段导出时每段前重复表头 | 表头被当成一条数据，销量列的文本混进合计 | 识别与表头同签名的行并计分段数 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_bom_stripped_and_duplicate_header_counted` |
| TRAP-05 | 行尾多一个分隔符，列数比表头多一列 | 静默补 `None`，价格与销量错位 | 读行时严格列数校验（`E_COLUMN_COUNT`），归价阶段进隔离区 | `app/modules/m1_ingest/reader.py` `app/modules/m2_price/service.py` | `tests/test_ingest.py::test_column_count_mismatch_flagged` |
| TRAP-06 | `1,234.50`、`1，234.50`、`１２.８０` 混在单价列 | `float()` 抛 ValueError，或把全角当字符串 | 全角转半角 + 去千分位 + 去单位后缀，再按 Decimal 化 | `app/modules/m2_price/coerce.py` | `tests/test_price.py::test_thousands_and_fullwidth_price` |
| TRAP-07 | `-` / `N/A` / `未知` 混在单价与销量列 | 被当成 0，合计与弹性都被压低 | 哨兵清单集中定义，返回 `None`；哨兵不是 0 销量 | `app/modules/m2_price/coerce.py` | `tests/test_price.py::test_sentinel_price_is_three_state` |
| TRAP-10 | 上千行销售额用 float 累加 | 合计差 0.01，五账永远不平 | 单价与销售额一律 Decimal，折算后按最小刻度量化 | `app/modules/m2_price/coerce.py` | `tests/test_price.py::test_tax_conversion_keeps_four_decimals` |
| TRAP-14 | 同一商品同一渠道同一天两条不同销量 | 后写覆盖，或整批丢弃，增量凭空变化 | 按行号定序保留首见行，其余进隔离区并给 `Q_DUP_DAY` | `app/modules/m3_quality/rules.py` | `tests/test_quality.py::test_duplicate_day_keeps_first_line` |
| TRAP-15 | 一个商品按元/件报，礼盒按元/箱报 | 合计差 12 倍，弹性被拉到离谱 | 盒规表集中定义，先折成元/件再算 | `app/modules/m2_price/coerce.py` | `tests/test_price.py::test_pack_unit_converted_to_piece` |
| TRAP-16 | 导出被中断，末行只有半行 | 半行被当有效数据，凭空多一天销量 | 末行不完整或引号未闭合时整批拒绝，返回 422 与行号 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_truncated_tail_rejected_with_code` |
| TRAP-18 | 0 字节文件 / 仅表头 / 有行但一行都不合法 | 三种都回「成功导入 0 行」 | 三种空态分开，各返回不同结论码 | `app/modules/m1_ingest/service.py` | `tests/test_ingest.py::test_empty_three_states_are_distinct` |
| TRAP-19 | 同一促销两个版本同时生效 | 结果取决于文件里的书写顺序 | 按版本号升序逐日覆盖，与书写顺序无关 | `app/modules/m2_price/versions.py` | `tests/test_price.py::test_rule_order_independence_on_versions` |
| TRAP-23 | 上一版结束日与下一版开始日是同一天 | 闭区间两头都算，这一天被计两次 | 声明左闭右开 `[开始日, 结束日)`，相接处不重复 | `app/modules/m2_price/versions.py` | `tests/test_price.py::test_window_is_half_open_at_end` |
| TRAP-24 | 渠道分摊各自四舍五入到 0.01 | 分摊合计比总额多一分钱 | 最大余额法：先取下沿再按余数补最小单位，合计恒等 | `app/modules/m5_lift/spread.py` | `tests/test_lift.py::test_largest_remainder_sums_to_total_exactly` |
| TRAP-25 | 两版活动价的区间重叠 | 取到哪版看运气，重叠日重复计天数 | 重叠天数显式标出（`Q_PROMO_OVERLAP`），按版本号大者生效 | `app/modules/m2_price/versions.py` | `tests/test_price.py::test_version_overlap_marked_and_highest_wins` |
| TRAP-27 | 促销期内缺 1-2 天，缺日按 0 补 | 日均被压低、波动被放大，预期区间从「不含 0」翻成「含 0」 | 三种补法（丢弃 / 按 0 / 按均值）各算一遍，区间含 0 或所需天数极差超 10% 就记 `D_IMPUTE_SENSITIVE` 并逐行给出数值 | `app/modules/m7_design/impute.py` | `tests/test_design.py::test_impute_sensitivity_flips_on_light_missing` |
| TRAP-28 | 候选对照在实验期内自己也在促销 | 同期倍数把它自己的促销算进基线，增量被抵掉 | 先按实验期（左闭右开）判重叠，有重叠直接标 `排除`（`D_CONTROL_OVERLAP`），只从「可用」里选对照 | `app/modules/m7_design/control.py` | `tests/test_design.py::test_control_excludes_candidate_with_promo_in_experiment_window` |

## 对账规则

- 行账：数据记录 377 = 入模行 369 + 剔除行 8
- 天数账：促销天数 34 = 有效 32 + 缺日 1 + 剔除 1
- 金额账：实际销售额 = 基线销售额 + 增量销售额（Decimal，2 位）
- 促销账：促销总数 4 = 已测算 2 + 被阻断 1 + 未生效 1
- 分摊账：增量合计 = 各渠道分摊合计（最大余额法补齐尾差）

五账在 `GET /api/report`、`/report` 页面与 `/api/export?kind=reconcile` 中显示；
任一账差额非 0 时结论列写「不平」，页面上的五账结论也随之变红。

## 设计账

- 实验期账：实验窗天数 = `max(预算天数, 最小实验期)`，端点左闭右开 `[开始日, 开始日 + 天数)`。
- 方差账：`方差放大 = max(1, (SE_HC1 / SE_朴素)²)`，`最小实验期` 用放大后的变异系数算，`朴素天数` 同时保留，两者差额写在页面上。
- 插补账：三种补法各一行（日均 / 波动 / 所需天数 / 预算下功效 / 可识别效应 / 预期区间），一行不合格不隐藏。
- 对照账：候选逐个给水平比、前置趋势差、实验期重叠天数、偏误与结论；只从「可用」里挑，挑不出记 `D_NO_CONTROL`。

## 反例与不变量

- 反例（朴素实现会挂）：`tests/test_elasticity.py::test_naive_uncentered_slope_differs`（漏掉中心化，斜率从 -0.79 变成 +1.99）、`tests/test_price.py::test_naive_float_parse_would_raise`、`tests/test_ingest.py::test_naive_splitlines_breaks_record_count`、`tests/test_design.py::test_hc1_ratio_and_variance_factor_on_heteroskedastic_series`（按朴素标准误算 14 天，按稳健标准误要 20 天）、`tests/test_lift.py::test_baseline_blocks_when_control_mapping_missing`（没有映射就当倍数 1.0，品类走势被吞掉）。
- 边界：`tests/test_lift.py::test_baseline_blocks_when_control_mapping_missing`、`tests/test_price.py::test_zero_length_promo_range_rejected`（区间长度 0 必须拒绝）、`tests/test_quality.py::test_coverage_gate_is_inclusive_at_threshold`（占比正好等于门槛仍进测算）、`tests/test_design.py::test_control_excludes_candidate_with_promo_in_experiment_window`（对照促销结束日正好等于实验开始日不算重叠）。
- 不变量：`tests/test_lift.py::test_idempotent_rerun_adds_no_rows`（幂等重跑行数不变）、`tests/test_lift.py::test_interval_collapses_when_standard_error_is_zero`（区间收点）、`tests/test_quality.py::test_day_account_identity_holds`（天数账守恒）、`tests/test_lift.py::test_allocate_keeps_total_for_real_promotions`（分摊守恒）、`tests/test_report.py::test_reconcile_stays_balanced_under_drop_mode`（换 0 销量处理方式后五账仍平）、`tests/test_design.py::test_interval_contains_zero_when_power_is_low`（功效低于一半时区间必含 0）、`tests/test_design.py::test_design_window_is_half_open`（实验窗左闭右开）。
- 端到端：`tests/test_e2e.py::test_full_pipeline_end_to_end`。

## 已知不覆盖

- 只做单商品对单价格的自身弹性，不含交叉价格弹性与促销互相挤占（同期多个 SKU 同时促销时会高估各自增量）。
- 对照组的品类映射写在 `app/config.py` 的 `CONTROL_MAP` 里，只有 2 组映射；没有映射的商品在增量阶段直接阻断（`A_NO_CONTROL`），设计阶段改用同渠道候选打分，不做品类自动匹配。
- 弹性估计是线性对数模型，不做固定效应、季节虚拟变量或面板估计；45 天窗口内以星期因子自然波动体现。
- 实验设计的功效按正态近似算（`z` 分位 + `Φ`），不按 t 分布做小样本校正；也不做序贯检验与中途看数。
- 设计只给单商品单渠道的实验，不排多商品同时实验的预算分配，也不做实验期的库存与档期冲突检查。
- 促销价高于常规价的场景只给警告（`P_PRICE_HIGH`），不阻断——换规格与错行无法自动区分。
- 中文数字（「满三百减五十」这类）不在本期解析范围，`备注` 列只展示不参与计算。
