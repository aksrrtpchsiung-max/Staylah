# Falcon 评测方案（尚未执行数据集）

## 数据集

- `data/clarification_10.json`：A01–A10；轻度 3、中度 4、重度 3。只装配并调用 A graph 一次，使用独立的内存 checkpoint，**不会构建或调用 B、C，也不需要 PostgreSQL**。`must_ask_about` 是预期缺失的主题，不要求 A 第一轮问完所有主题；A 当前一轮最多问三个问题。
- `data/full_pipeline_20.json`：F01–F20；3–5 条条件 5 个案例、5–10 条条件 10 个案例、超过 10 条条件 5 个案例。`filters` 是由原文人工标注的条件；租售、区域、出租范围和价格也计入条件数。带 `(soft)` 的是偏好，不应按硬条件淘汰。部分偏好可能没有可靠的结构化字段，允许标记“未知”，不能凭空补齐。

两个数据集都只是用户请求和人工标注，没有虚构房源。每个样例使用独立 conversation ID，防止历史对话串样。

## 日后执行（本次未执行）

运行 A 数据集只需要 A 使用的 DeepSeek key；完整流程数据集还需要 PostgreSQL、B 的浏览器桥接、C 的网关 key，以及仓库根目录 `.env` / `runtime.toml` 都已按项目本来的方式配置。完整流程评测强制 `source_mode=live`，不会用模拟房源。

```bash
cd /Users/junboxia/Documents/ChatGPT/hackathon/evaluation
.venv/bin/python -m evaluation_suite.runner --suite clarification
.venv/bin/python -m evaluation_suite.runner --suite full
# 只运行单个案例：--case A01 或 --case F01
# 从 F03 接续，并保留同一结果目录里已完成的 F01/F02：
.venv/bin/python -m evaluation_suite.runner --suite full --start-at F03 \
  --resume-dir evaluation_runs/EvalF-full-20260922T134407Z-a48c4679
```

完整流程数据集在 A 给出确认摘要后，会自动发送“确认”，让该案例进入 B/C；**这只是测试流程自动化，不表示 A 的理解正确**。日志保存 A 的 profile 供人工检查。如果 A 要求额外澄清、B 回问、C 等待用户接受放宽条件，runner 会记录并停止该案例，不会代替用户编造答案或接受放宽条件。

日志默认放在被 Git 忽略的 `evaluation_runs/` 下：A 每例一份 JSON，另有 `dialogue.md` 与 `summary.csv`；完整流程使用 `EvalF-full-<timestamp>/`，每完成一例便立即写入 `Fxx.json`、`Fxx.md`，更新 `summary.csv` 与 `progress.md`，可边跑边查看。JSON 含用户请求、A 状态与档案；完整流程日志另含每次 B 搜索的完整 Listing/coverage、交给 C 的候选、retrieve 分数、evaluate/review、最终推荐对应的完整 Listing、各节点耗时和最终状态。C 不再执行硬条件 screen，B 候选数不等于人工确认合格数。日志可能含真实房源描述或个人资料，勿直接上传 GitHub；不会读取或记录 `.env` 的密钥值。

## 五项指标与判定标准

1. **A 需求理解／追问成功率**（只用 A01–A10）：首轮应处于 `awaiting_clarification`，`clarification_questions` 非空，且未生成 B handoff；人工核对所问主题是否与 `must_ask_about` 中至少一个真正缺失条件有关，是否乱猜、漏掉阻塞条件。合格案例数 / 10。因为这 10 例全部应被追问，它只能衡量“该追问时是否追问”，不能检测“不该追问时误追问”的假阳性；若要总体准确率，后续需补充明确请求的负例。
2. **合格房源召回率**（只用 F01–F20）：日志保留 B 原始房源与交给 C 的候选，按 `listing_key` 去重，同时保留每次 attempt 的详情。真正召回率 = `|搜索得到且经人工确认合格的房源 ∩ 独立参考合格全集| / |独立参考合格全集|`。参考全集需人工或独立搜索在相同时间窗/来源/查询预算下建立；C 不再筛选后，`eligible_found_unique_count` 留空，必须人工核对 B 候选后才可计算，**不能把 B 候选数冒充合格数或召回率**。原始 B 数量、待核实项、失败来源和截断标记需一起报告。
3. **最终推荐准确率**（只用 F01–F20）：人工逐套判定最终推荐是否符合所有硬条件，并记录证据/未知项。`正确推荐套数 / 实际推荐套数`；无推荐时记 N/A，不当作 100%。同时检查推荐是否都来自该次 B 快照；C 不再独立确认硬条件。
4. **推荐／排序质量**（只用 F01–F20）：把每例 C 最终推荐与同次 B 的候选并排看。人工先判断候选是否满足硬条件，再给合格候选 0–3 分（0=不宜推荐、1=勉强、2=合适、3=非常合适），考虑软偏好、证据、新鲜度、价格和未知风险，再计算 `nDCG@K`（K=推荐数，最多 display_limit）；也可记录“明显更好的合格房源被漏排/低排”的原因。候选不足或完全同分时标记 N/A 或并列，不强造差异。
5. **时间**（只用 F01–F20）：A=需求解析与确认两次 A graph 调用之和；B=首搜/补搜的查询准备、计划及实际 B 搜索；C=retrieve、evaluate、review、decide_next 调用之和。按案例报告毫秒、全体中位数与 P90；`wall_duration_ms` 另列，包含编排、持久化及日志开销，不等于 A+B+C。若等待真人回答/放宽条件，该等待时间不算模型/搜索处理耗时；案例会停在等待状态。

附加报告 **端到端完成率**：`phase=published` 且有最终推荐的案例数 / 20。需要同时列出因 A 追问、B 澄清、C 等待放宽、来源故障或无合格房源而未发布的数量，不能将失败与合理无房源混为一类。搜索覆盖受网站可用性、实时上下架和本项目 `candidate_limit` / 页面上限影响，评测时必须固定配置并记录运行时间。
