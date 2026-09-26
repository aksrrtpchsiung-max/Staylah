# 房源开发 Mock 数据包

> 历史数据工具：此目录自带的契约是数据生成时的快照，其文件摘要记录在 output/manifest.json 中。请勿用作正式应用契约；当前业务类型唯一定义在 ../property_agent/contracts.py。保留快照原始字节，以便校验已有数据。

已提供 **1,000 条完全合成的房源**：900 条普通样本、100 条边界样本（30 类场景）。普通样本也包含正常的数据缺失；“普通”不等于所有字段非空或一定满足用户筛选条件。

依据用户提供的 **2026-09-14 最新 `contracts_v0.py` 和 `CONTRACT_CHANGES_FOR_BC.md`** 生成，包内 `contracts/` 保存附件的原样副本。当前工作区 `docs/contracts_v0.py` 是旧版；接入本数据包时请以包内版本为准。接口金额是整数，房源交易类型是 `rent/sale`，完整 `attributes` 必填，范围在 `attributes.listing_scope`，`RunContext` 含 `user_id`。

## 直接使用哪些文件

| 文件 | 用途 |
|---|---|
| [output/listings.json](output/listings.json) | **给 B/C 接口使用**：纯 `list[Listing]`，不附加接口外 key |
| [output/records54.jsonl](output/records54.jsonl) | 每行 `{listing_key, id, fields}`，`fields` **严格包含图中的 54 项 key**；`id` 是内部 UUID |
| [output/db-tables.json](output/db-tables.json) | `listing`、`hdb_detail`、`condo_detail`、`landed_detail` 四张表的 JSON 行，带主外键；子表互斥 |
| [output/fixtures/search-success.json](output/fixtures/search-success.json) | 完整 `Result[SearchResult]` 工具返回示例，20 条候选 |
| [output/fixtures/profile.json](output/fixtures/profile.json) | 整租、Clementi、月租 ≤ SGD 3500、至少两卧的测试用户档案 |
| [output/fixtures/context.json](output/fixtures/context.json) | 完整 RunContext，conversation_id 为示例 thread_id |
| [output/fixtures/plan.json](output/fixtures/plan.json) | 与 search-success 对应的 SearchPlan |
| [output/fixtures/scenario-expectations.json](output/fixtures/scenario-expectations.json) | 30 个代表样本的 key、预期筛选分组和额外断言 |
| [output/field-statistics.json](output/field-statistics.json) | 实际生成数据的空值、unknown、数组以及按出租范围统计 |
| [output/validation-report.json](output/validation-report.json) | 本批数据的类型、数据一致性及场景验证结果 |
| [SCHEMA_MAPPING.md](SCHEMA_MAPPING.md) | 54 个字段如何映射到最新接口，哪些保留在数据库层 |

`Listing` 并不容纳图中全部 54 个字段。坐标、地址、PSF、房型子表等信息保留在 DB 数据中，通过 `listing_key` 和接口数据关联，不偷偷添加进 `Listing`。若 C 将来要直接消费这些信息，应先正式扩展共享 contract。

三种表示使用同一批身份，并非三个独立随机数据集。HDB/Condo/Landed 各有最多一个对应子表，`apartment/other/unknown` 没有这些子表。所有 54 项都存在于 `fields`；不适用或无证据的值允许为空。

## 使用示例

从本目录执行（Python 3.11+，无需安装依赖）：

```python
import json
from pathlib import Path

root = Path.cwd()
listings = json.loads((root / "output/listings.json").read_text())
search_response = json.loads((root / "output/fixtures/search-success.json").read_text())
db_rows = [json.loads(line) for line in (root / "output/records54.jsonl").read_text().splitlines()]
db_by_key = {row["listing_key"]: row["fields"] for row in db_rows}

assert listings[0]["attributes"]["area_sqft"] == db_by_key[listings[0]["listing_key"]]["area_sqft"]
# 将 listings 交给你实现的 screen，或让 mock search 返回 search_response。
```

`search-success` 包含边界候选，并非已经筛选通过的推荐。`coverage` 如实标记此固定回放未应用硬过滤；C 应检查约束。它只是固定 fixture，不是一个可以传任意 plan 的动态搜索 Provider。

## 真实观察如何影响生成

本轮补采 **40 条搜索卡片、5 条详情**，结合之前 4 条详情，形成 9 条详情原型：3 条单间、6 条整租。样本来自 Clementi、Tampines、Jurong East，**不是有代表性的市场抽样**。

原型摘要位于 [reference/observed-profiles.json](reference/observed-profiles.json)。只保留建模所需公开事实与来源；生成房源的 ID、地址、描述、Evidence 均是全新合成的，生成输出的 `source_url` 为 null，不把源网页作为虚构事实的引用。

| 字段缺失现象 | 实际详情样本 | Mock 处理 |
|---|---|---|
| 整租的 owner_stays、水电、Wi-Fi、访客、宠物 | 6/6 条均为空 | 普通整租保留这一成组缺失模式 |
| 单间的 owner_stays、水电、Wi-Fi | 1/3 条为空，另 2 条明确为 true | 按原型整组抽取，保留字段相关性 |
| 单间的访客、宠物 | 2/3 条为空，另 1 条明确为 false | 保留未知；不能默认 false |
| 做饭规则 | 单间能读到 NOT/LIGHT；整租普遍缺失 | 映射为 none/light/unknown；另设明确 full 的定向案例 |
| floorArea 缺失 | 单间面积可以位于 dimensions.room | area_sqft 配合 extra_attributes.area_basis 区分 room/floor |
| Studio 原始卧室数 | 真实样本 `value=-1, text=Studio` | 有明确 Studio 文本才转为 unit_layout=studio、bedrooms=0；原始负值约定另记 |
| 土地面积数值 | 可能是嵌套对象 `value.value` | 独立解析回归片段，DB 输出整数 |
| 设施名称 | 在 text 而非 label/name | 使用真实设施类别构造列表 |
| source project 分支 | verified/unverified 两种 | 解析回归片段要求根据 project.type 选择 |

原型金额和面积在有限范围内扰动；物业类别/租赁范围按测试设计分配权重，再选对应原型。**30/40/20/10 的单间 HDB/整租 HDB/整租 Condo/整租 Landed 权重、缺省额外丢失率、90/10 的场景比例均是开发测试设计，不是市场统计。** 源数据本身可能不准确，本包不把平台字段当作真实房屋的最终事实。

为补齐正反分支，定向案例另外包含明确允许宠物/访客、不包水电/Wi-Fi、master room 有独立卫浴、apartment、other、1+study 等情形。它们是可解释的测试设计，不声称在本次 9 个详情中均被观察到。

## 空值和语义

- 所有接口必需 key 都保留；`None`、`False`、`0`、`"unknown"` 不互换。
- 布尔未知用 null，只有模拟原文明确肯定/否定才生成 true/false 与证据。
- 不适用与未填写在接口里都可能是 null/unknown；DB 的 `extra_attributes.field_state` 额外区分。
- `lease_years` 表示产权年限：leasehold 可能为 99，freehold 为 null。租赁合同期另存 `extra_attributes.rental_lease_months`。
- `lease_commencement_year`、`storeys`、数值 `floor_range` 未获得可靠样本，本批保持 null。TOP 年不能替代产权起算年，High Floor 也不强造为 10–12 层。
- active 表示模拟的确认有效状态；fetched_at 是采集时间，不自动等于 last_verified_at。生成有效核验时间时会记录 `verification_method=simulated_confirmation`。
- 未知/冲突价格 amount=null；冲突情形有两份不同的结构化价格 Evidence。家具冲突归 unknown 并标记 field_issues。
- `field_issues` 在本包使用 **Listing 字段路径字符串**（如 `price.amount`），这是本包的约定，contract 未规定其字符串词表。
- raw_description/raw_details 是原始材料，不替代结构化 Evidence。Evidence ID 在全数据集中唯一，所有 price.evidence_ids 都能解析到对应事实。
- 坐标、门牌、邮编是合成测试值，邮编保留六位字符串及前导零；**不用于验证真实地址、地理编码或通勤准确性**。真实地点名称仅用于 location_id 分类测试。

本批 1,000 条数据中，面积 null 69 条，同住 null 804 条，宠物和访客 null 各 914 条，last_verified_at null 376 条。子表字段总空值率包含“不适用”，因此不要拿全部 1,000 条的 condo 字段空率当作公寓数据质量。

## 边界与失败用例

30 类代表样本覆盖：预算等于上限/超 1 元、未知价/冲突价、范围/地点/卧室未知、已下架/状态未知、过期核验/从未核验、单间 0 卧室、Studio、面积缺失、0 浴室、家具冲突、楼面/土地双 PSF、周租、售房、描述 prompt injection、长 Unicode/HTML、源 ID 缺失、未来入住、稀疏房源、bedspace、独立卫浴主卧、显式房屋规则、apartment、other、1+study。

原始描述含恶意指令或 script 文本的案例只是不可信数据。不会执行，也不构成修改用户条件的授权。推荐不应因为这类文字改变预算或排名。

配套工具结果：

- `search-empty.json`：成功但没有结果。
- `search-partial.json`：返回部分候选，源请求被限流，仍有后续页。
- `search-timeout.json`：error + data=null + TIMEOUT。
- `search-truncated.json`：明确 truncated、has_more 和 next_pages。
- `page-1.json` / `page-2.json`：跨页重复一个 listing_key，单页内不重复；附去重预期。
- `search-sale.json`、`buy-profile.json`、`buy-plan.json`：意图 buy 对应 Listing sale、价格 period=total。
- `snapshot.json`：完整 ListingSnapshot，供 evaluate/review 接入测试。
- `plans-by-response.json`：各响应对应的完整 SearchPlan。

[invalid-listings.json](output/fixtures/invalid-listings.json) 单独收录 **18 条不应通过校验的输入**：旧版字符串金额、布尔当整数、浮点金额、旧 price.scope、buy 房源、缺 attributes/必需 nullable key、错误 Studio 枚举、字符串布尔、负金额/面积、known 无金额、证据悬挂/重复/错值、未来核验、无证据的 false 默认、无时区时间。不要将它们混入正常候选池。

[raw-source-fragments.json](output/fixtures/raw-source-fragments.json) 是 7 个解析回归**片段**，不是完整 __NEXT_DATA__ 或完整网页，必须按其对应单元解析器使用。

## 再生成与验证

```bash
python3 scripts/generate.py --count 1000 --seed 20260914 --as-of '2026-09-14T12:00:00+08:00'
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

至少生成 100 条，小批次会保留至少 30 条边界样本，因此只有 count≥300 时才接近 90/10。相同 seed、count、as-of、参考样本、代码与契约副本生成相同结果；使用 `--out /path/to/new-output` 可保留原数据。`generate` 和 `validate` 均支持 `--out`。

本机没有 PATH 内的 Python 3.11+ 时，可使用：

```bash
/Users/linzihao/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/validate.py
```

默认时间是冻结的测试时钟，并非每次运行时的“现在”。验证新鲜度的测试必须使用 `scenario-expectations.json` 的 reference_time，或重新指定 `--as-of`。**7 天新鲜度阈值只是 fixture 的明确测试策略，接入团队正式策略时应替换。** 周/月计价未定义换算政策时不通过月租硬条件，同样是本包显式测试约定。

`contracts_v0.py` 是 TypedDict，仅能提供静态类型描述；本包从它动态解析运行时结构，并额外检查 Evidence、金额、时间、主外键、PSF 和跨文件映射。附带的参考筛选 oracle 仅用于固定场景验证，不替代团队真实的 screen/retrieve/evaluate/review 实现。14 项回归测试通过不等于真实 agent 的推荐质量已经通过测试。

## 追溯与采样

- `output/manifest.json` 记录种子、冻结时间、计数、契约及原型文件 SHA256。
- `reference/observed-profiles.json` 是可离线再生成的原型输入。
- `reference/live-observations.json` 是此次新增采样的公开结构化字段，位于 reference 层，不是 mock 候选。
- `scripts/sample_live.py` 是可选的少量只读采样工具，需要原有 opencli/Chrome 连接；**正常再生成不需要访问 PropertyGuru**。
- `scripts/prepare_reference.py` 是本工作区维护原型时用的工具，依赖此前 can-rent-lah 的本地审计文件；分享数据包后无需运行它。

全部 fixture 仅用于测试。少量真实样本帮助模拟字段结构和数据问题，不能据此评估真实市场比例、房源质量、租金水平或地理真实性。
