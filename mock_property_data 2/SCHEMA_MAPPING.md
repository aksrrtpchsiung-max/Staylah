# 54 字段 → 最新 Listing 映射

fields 层始终包含下表全部 54 个字段；内部 id/listing_key 放在外层，不计入 54 项。

| 数据库字段 | 接口字段 | 说明 |
|---|---|---|
| `source_listing_id` | `source_listing_id` |  |
| `source_url` | `source_url` |  |
| `source` | `source` |  |
| `property_type` | `attributes.property_type` |  |
| `transaction_type` | `transaction_type` |  |
| `listing_scope` | `attributes.listing_scope` |  |
| `room_type` | `attributes.room_type` |  |
| `unit_layout` | `attributes.unit_layout` |  |
| `price_sgd` | `price.amount` | 整数或 null，配合 price.status/evidence_ids。 |
| `price_period` | `price.period` | month/week/total/null。 |
| `area_sqft` | `attributes.area_sqft` |  |
| `psf_sgd` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `psf_basis` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `bedrooms` | `bedrooms` |  |
| `bathrooms` | `attributes.bathrooms` |  |
| `ensuite_bathroom` | `attributes.ensuite_bathroom` |  |
| `owner_stays` | `attributes.owner_stays` |  |
| `cooking_policy` | `attributes.cooking_policy` |  |
| `utilities_included` | `attributes.utilities_included` |  |
| `wifi_included` | `attributes.wifi_included` |  |
| `visitors_allowed` | `attributes.visitors_allowed` |  |
| `pets_allowed` | `attributes.pets_allowed` |  |
| `furnishing` | `attributes.furnishing` |  |
| `title` | `title` |  |
| `address` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `postal_code` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `latitude` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `longitude` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `tenure_type` | `attributes.tenure_type` |  |
| `lease_years` | `attributes.lease_years` | 产权年限，不是租赁合同期。 |
| `inclusivity_statement` | `仅数据库` | 接口未定义此列；文本可同时出现在 raw_details，若要生成相关推荐事实还需补对应结构化 Evidence。 |
| `raw_description` | `raw_description` |  |
| `raw_details` | `raw_details` |  |
| `extra_attributes` | `部分转换；其余仅数据库` | location_id、source_updated_at、price.status、field_issues 与合成证据构造使用此处元数据；不往 Listing 添加 extra_attributes。 |
| `listing_status` | `listing_status` |  |
| `listed_date` | `listed_date` |  |
| `fetched_at` | `fetched_at` |  |
| `last_verified_at` | `last_verified_at` |  |
| `created_at` | `仅数据库` | 本数据库时间；来源更新时间通过 extra_attributes.source_updated_at 映射 Listing.source_updated_at。 |
| `updated_at` | `仅数据库` | 本数据库时间；来源更新时间通过 extra_attributes.source_updated_at 映射 Listing.source_updated_at。 |
| `block` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `town` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `hdb_model` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `flat_type` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `floor_range` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `lease_commencement_year` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `development_name` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `top_year` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `floor_level` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `facilities` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `landed_type` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `land_area_sqft` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `built_up_area_sqft` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |
| `storeys` | `仅数据库` | 最新 Listing 无同名字段，不额外塞入接口对象。 |

接口另有：`listing_key` 来自外层稳定键；`source_mode="mock"`；`price.currency="SGD"`；`price.status/evidence_ids`、`evidence`、`field_issues` 由同一记录的合成观测材料生成。

HDB/Condo/Landed 子表字段在 records54 的 fields 中平铺，但在 db-tables.json 中拆表，保留主外键与子表互斥关系。

unknown 字符串仅用于 contract 明确允许的枚举；不是所有空值通用占位。布尔使用 true/false/null。

本包定义 psf_basis=room/floor/land，area_basis 在 extra_attributes 中。原图没有提供 DB 枚举约束/DDL，这些是本包的明确数据约定，不是新增的接口字段。
