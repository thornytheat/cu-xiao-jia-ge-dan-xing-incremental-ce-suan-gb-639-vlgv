# pricelift · 促销价格弹性与增量测算

牧原乳品区域销售部内部工具：导入日销与促销日历，得到「价格弹性 → 促销增量 → 五账核对 → 下期实验设计」。
单体 FastAPI 服务 + Jinja2 服务端模板 + 原生 HTML/CSS/JS，无构建工具、无 CDN。

## 能力

- 三份输入（日销 / 促销日历 / 对照组）一次导入，逐文件诊断编码、BOM、分段表头、列数与截断
- 价格统一折成「元/件、不含税」：千分位、全角数字、元/箱、税率混用都能处理
- 促销版本按左闭右开区间与版本号优先级生效，重叠天数显式标出
- 质量隔离：缺日、重复日、异常价、哨兵、未知商品，全部带原因码与行号，可导出 CSV
- 对数线性弹性：斜率、标准误、t 值、R²，0 销量支持平移与剔除两种方式
- 增量测算：对照组基线 + 95% 区间，渠道分摊用最大余额法保证守恒
- 五账核对（行/天数/金额/促销/分摊）与运行清单导出
- 实验设计：按目标效应与功效算最小实验期、预算下可识别效应、对照建议与预期区间

## 模块

| 模块 | 动作 | 入口 | 页面 |
|---|---|---|---|
| M1 采信 | 导入并跑通全流程 | `POST /api/import` · `GET /api/datasets` | `/` |
| M2 归价 | 列契约、换算规则、促销版本、单条试算 | `GET /api/contract` · `POST /api/price/preview` | `/contract` |
| M3 质量隔离 | 原因码分布与隔离行 | `GET /api/quarantine` · `GET /api/quarantine.csv` | `/quarantine` |
| M4 弹性 | 分组弹性与显著性 | `GET /api/elasticity` | `/elasticity` |
| M5 增量与批次 | 增量、区间、渠道分摊、批次历史 | `POST /api/lift` · `GET /api/batches` | `/lift` |
| M6 报告导出 | 五账核对与 CSV/清单导出 | `GET /api/report` · `GET /api/export?kind=` | `/report` |
| M7 实验设计 | 最小实验期、可识别效应、对照建议、缺日敏感性 | `POST /api/design` · `GET /api/design` | `/design` |

## 核心规则

1. **弹性**：`ln q = a + b·ln p`，x 与 y 双中心化后求斜率；0 销量默认 `ln(q+0.5)` 统一平移，
   可切 `drop` 剔除并计数；`|t| ≥ 1.96` 才记显著。
2. **增量**：`基线 = 促销前 14 天日均 × 有效天数 × 对照组同期倍数`，
   `增量 = 实际 − 基线`，金额 Decimal 2 位；区间用斜率标准误的 delta 法给出。
3. **促销区间**：左闭右开 `[开始日, 结束日)`；同日多版时版本号大者生效，与书写顺序无关。
4. **实验设计**：`最小实验期 = (A² + sqrt(A⁴ + 4·A²·14))/2`，`A = (z(1−α/2) + z(功效))·cv/目标效应`；
   方差用 HC1 稳健标准误放大，缺日按三种补法各算一遍，对照按实验期重叠与前置趋势差打分。

详细定义见 `docs/DATA-DICTIONARY.md`，陷阱与测试映射见 `docs/DEPTH.md`。

## 启动

```bash
docker compose up -d --build
# 主入口
open http://127.0.0.1:18208/
```

本地直接跑：

```bash
pip install -r requirements.txt
uvicorn app.main:create_app --factory --port 18208
```

数据库是挂载在命名卷 `pricelift-data` 上的 SQLite；要重建就 `docker compose down -v`。

## 演示步骤

1. 打开 `/`，样例集选 `main`，0 销量处理选 `shift`，点「测算」。
2. `/quarantine` 看严重度与原因码分布，点「导出隔离行」拿到带行号的 CSV。
3. `/elasticity` 看按商品的弹性柱图与显著性；点「只看显著」可过滤。
4. `/lift` 看增量、区间与渠道分摊合计。
5. `/report` 核对五账，导出增量 / 弹性 / 五账 / 运行清单。
6. `/design` 填目标效应与预算天数，看最小实验期、预算下可识别效应、对照建议与三种缺日补法。

## 页面

| 路径 | 内容 |
|---|---|
| `/` | 本期结论、导入表单、输入文件、阶段计数、批次历史 |
| `/contract` | 换算规则、单条试算、列契约、税率与盒规、活动价版本与生效窗口 |
| `/quarantine` | 严重度横条、原因码分布、隔离行表、导出 |
| `/elasticity` | 弹性结论、按商品 CSS 柱图、全部估计表 |
| `/lift` | 增量结论、重算、增量明细、渠道分摊、批次 |
| `/report` | 结论、五账核对、指标、原因码、运行清单（可打印） |
| `/design` | 设计输入、结论、实验窗与波动、缺日插补、控制组候选、设计校验 |

## 目录

```text
app/                 main.py config.py errors.py reasons.py manifest.py store.py web.py
app/modules/         m1_ingest m2_price m3_quality m4_elastic m5_lift m6_report m7_design
app/templates/       base.html + 7 页
app/static/          theme.css layout.css components.css print.css app.js charts.js tables.js
docs/                DEPTH.md DATA-DICTIONARY.md
sample_data/         7 份样例（含 3 类以上脏数据）+ 说明
tests/               conftest.py 7 个模块测试 test_e2e.py fixtures/
```

## 已知不覆盖

- 不做交叉价格弹性与同品类促销互相挤占。
- 对照组映射手工维护在 `app/config.py` 的 `CONTROL_MAP`，没有映射的商品直接阻断。
- 弹性模型不含固定效应与季节虚拟变量。
- 中文数字型促销条件（「满三百减五十」）不解析。
- 实验设计的功效按正态近似算，不做 t 分布小样本校正、序贯检验与多商品预算分配。
