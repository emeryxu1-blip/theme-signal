# ainvest-openapi-quote

这是一个用于构造、审阅、解释和执行 AInvest OpenAPI 行情请求的 skill。它覆盖三类访问场景下的五类接口：

- 沙盒 Aime Claw 场景：使用环境变量 `AIME_API_KEY` 生成 `Authorization: Bearer <AIME_API_KEY>`。
- B 端场景：使用调用方提供的 `apikey`。
- C 端场景：使用调用方提供的 `Cookie`。

支持的 endpoint：

- `index_api/indicator/v2/snapshot`
- `index_api/indicator/v2/series`
- `index_api/relation/v1/list`
- `quote/v2/multi_kline`
- `quote/v2/single_tick`

B 端 apikey 按应用区分，不能默认复用：

- `index_api/indicator/v2/snapshot`、`index_api/indicator/v2/series` 和 `index_api/relation/v1/list` 通常使用 `index-api` 应用的 apikey。
- `quote/v2/multi_kline` 和 `quote/v2/single_tick` 通常使用 `quoteag` 应用的 apikey。
- 只要求当前 endpoint family 实际需要的 apikey。只调用 `index_api` 时不要要求 `quoteag`；只调用 `quote/v2` 时不要要求 `index-api`。

## 适用范围

支持的市场和品类：

- 美股
- ETF
- 债券
- 加密货币
- 期权
- 宏观或市场环境类指标

支持的请求类型：

- `snapshot`：指标快照、列表、排序、分页、related、holding 等。
- `series`：指标历史序列、趋势、时间范围查询。
- `relation_list`：关系型列表，如 ETF 持仓代码、指数/行业成分、prompt 成分、group 成分。
- `multi_kline`：K 线、分钟线、分时等基础行情。
- `single_tick`：逐笔成交、成交明细等基础行情。

这个 skill 的职责不是直接封装接口调用，而是帮助模型稳定地产出这几类内容：

- 判断应该使用 `snapshot`、`series`、`relation_list`、`multi_kline` 还是 `single_tick`。
- 选择正确的 `symbol.type` 和 symbol 参数结构。
- 查询生成后的指标索引，找到 `indicator_id`、endpoint、模板名和 attrs。
- 按业务规则组织 request body。
- 解释响应结构、空值语义和缺失数据风险。
- 在认证信息可用时直接发起请求；认证缺失时只生成可执行请求体。

在对应场景的认证信息可用时，这个 skill 也可以直接用于取数，而不只是生成请求体。只有沙盒 Aime Claw 场景从环境变量读取认证信息；B 端和 C 端必须由调用方或当前对话上下文提供认证值。

它适合用于：

- watchlist
- 排行榜/榜单
- 个股详情快照
- related stock / related ETF
- 行业成分股 / 子行业
- ETF holdings
- relation/v1/list 关系型列表
- ETF/链上/评分等历史序列
- 分时行情
- K线 / 蜡烛图
- 逐笔成交 / 成交明细

## 目录说明

### 入口文件

- `SKILL.md`：agent 实际读取的主指令。
- `README.md`：当前维护说明。
- `agents/openai.yaml`：agent 展示配置。

### references

`references/` 是规则和知识库，建议优先阅读拆分后的精简文档：

- `index.md`：references 总入口。
- `routing.md`：判断使用 `snapshot`、`series`、`relation_list`、`multi_kline` 还是 `single_tick`。
- `snapshot.md`：`snapshot` 请求结构、限制、排序、过滤、分页和响应形态。
- `series.md`：`series` 请求结构、`time_range` 规则和响应形态。
- `basic-quote.md`：`multi_kline` 与 `single_tick` 的参数限制、返回字段和路由说明。
- `relation.md`：`relation/v1/list` 请求结构、支持的关系类型、模板和响应形态。
- `symbols.md`：`market_code`、`prompt_id`、`link_code`、`group_id` 等 symbol 选择规则。
- `display.md`：前端展示规则，尤其是百分号、空值和缺失指标处理。
- `scenarios.md`：从用户需求到模板的场景级映射。
- `template-index.md`：请求模板索引。
- `template-writing.md`：新增或修改模板的规范。
- `testing.md`：手工验证说明和生产请求头要求。
- `live-fetch-examples.md`：实际取数成功样例，覆盖主要 endpoint。
- `indicator-attrs.md`：指标请求/响应 attr 说明。
- `snapshot-indicators.md`：常见 snapshot 指标映射。
- `series-indicators.md`：常见 series 指标和 `time_range` 说明。
- `business-pools.md`：本地场景里出现的 `block_id`、`prompt_id`、`group_id` 等业务池说明。
- `event_id.md` 与 `event_id_index.csv`：需要 `event_id` 的指标参考。
- `tech.md`：技术指标参数参考。
- `tech_param_retry_request.json`：技术指标已验证过的 `tech_param` 默认参数样例。
- `legacy/id_dict.md`：历史 attrs 与指标规则。这里的 attrs 是第一优先级。

仍保留但不作为日常维护入口的原始资料：

- `references/gms-http-v2.md`
- `references/legacy/id_dict.md`
- `references/legacy/snapshot_cf.md`
- `references/legacy/series_cf.md`

只有精简文档覆盖不到时再回看这些原始协议资料。

### assets

`assets/request-templates/` 存放可复用请求模板。新增模板后需要同步更新：

- `references/template-index.md`
- `references/scenarios.md`
- 必要时更新 `references/template-writing.md`

### scripts

skill 运行期脚本：

- `scripts/validate_templates.py`：校验请求模板并 dry-run endpoint 推断。改模板后必须跑。
- `scripts/fetch_quote.py`：从模板、JSON 文件或 inline JSON 执行一次 quote 请求。
- `scripts/export_indicators.py`：检查本地指标 Excel 新鲜度、从 Tangram 导出 AInvest 支持 API 的指标 Excel 到 skill 内部，并可联动重建生成 CSV。
- `scripts/build_quote_csvs.py`：从 Excel 指标表生成 skill 查询用 CSV。
- `scripts/find_quote_params.py`：按关键词、指标 ID、中文名或英文名查询生成后的指标参数。

批量 C 端请求、symbol 扩展、time_period x symbol 矩阵和持久为空分析不属于当前 skill 运行目录。若需要重新加入，必须把脚本、测试和生成物一起放入明确目录，并同步更新本 README。

## 常见工作流

### 新增或修改请求场景

建议顺序：

1. 先看 `references/scenarios.md`。
2. 再看 `references/template-index.md` 找最接近的 JSON 模板。
3. 如果涉及指标，先查拆分后的 indicator references。
4. 拆分文档没有覆盖时，再查 `references/legacy/id_dict.md`。
5. 修改模板后执行模板校验。

```bash
python3 scripts/validate_templates.py
```

### 判断 endpoint

- 最新值、列表、排序、分页、related、holding 类需求，优先 `snapshot`。
- 过去 N 天的指标趋势、连续时间点的衍生指标，优先 `series`。
- 只需要关系项列表、不需要指标值/排序/过滤时，优先 `relation_list`。
- K 线、蜡烛图、分钟级基础行情、分时行情，优先 `multi_kline`。
- 逐笔成交、成交明细、time and sales，优先 `single_tick`。

补充规则：

- 用户要“分时行情”时，不要走 `single_trend`，应组装分钟 `multi_kline` 请求。
- 取当天全部分时，默认优先使用 `time_range = {"trade_date": 0, "date_offset": 0}`。
- 用户要“逐笔成交”或“成交明细”时，不要走 `snapshot` / `series`，应组装 `single_tick` 请求。
- 这个 skill 安装在沙盒后，涉及实际行情取数应优先考虑当前 skill，尤其是基础行情场景

### 3. 判断 symbol 来源

### 直接取数

如果用户只是要 request body，按模板和规则构造请求即可。

如果用户要实际行情结果，先判断访问场景：

- 没有明确 B/C 上下文时，默认按沙盒 Aime Claw 场景处理
- B 端上下文包括：B 端、内部网关、apisix、服务间调用、`quote-apisix-gateway.hxapisix`
- C 端上下文包括：C 端、浏览器/客户端、web、cookie 认证、公网 quote 域名
- 沙盒 Aime Claw：默认读取 `AIME_API_KEY` 并组装 `Authorization: Bearer <AIME_API_KEY>`。
- B 端：从用户请求或上下文获得对应 endpoint family 的 apikey。
- C 端：从用户请求或上下文获得完整 Cookie 字符串。
- B 端 `snapshot/series/relation_list` 使用 `index-api` apikey；`multi_kline/single_tick` 使用 `quoteag` apikey
- 只调用其中一类 endpoint 时，只需要对应应用的 apikey，不要额外要求另一类 apikey
- C 端从当前用户请求或上下文中获取 Cookie 字符串，并组装请求头 `Cookie`

没有认证信息时，不要伪装成已成功取数，只输出可执行请求体并说明缺少哪个 env 或 header。

直接取数返回结果时，不要把完整原始 JSON 全量贴给用户。优先返回：

1. 是否成功取到数据。
2. 使用的 endpoint。
3. 使用的访问场景和认证头形态，不输出真实凭证。
4. 核心结果摘要。
5. 必要时说明空值、缺失指标或空序列含义。
6. `snapshot` 场景优先返回核心字段值，比如最新价、涨跌幅、成交量
7. `series` 场景优先返回点数、时间范围、首尾点或趋势摘要
8. `relation_list` 场景优先返回关系类型、结果 `symbol_type`、total 和前几个 `v` 值
9. `multi_kline` 场景优先返回周期、bar 数、最新一根或首尾 bar 摘要
10. `single_tick` 场景优先返回成交笔数、最新成交时间、价格、方向、手数

如果接口返回空数据，要明确这是数据结果，不一定是请求错误。可能原因包括参数不适用、symbol 类型不匹配、指标本身为空。

## symbol 与指标规则

### market_code 前置解析

当用户输入的是代码、Ticker、证券中文名、证券英文名、ETF 名称、债券名称、数字货币或期权代码、行业中文名、行业英文名、行业 alias 等自然标的信息时，应先调用 `ainvest-marketcode` skill 解析为 AInvest `market_code`，再继续拼接本 skill 的 quote 请求。

独立安装当前 skill 时，`ainvest-marketcode` 是可选协同 skill，不是当前目录内置能力。如果未安装，应要求调用方提供明确的 AInvest `market_code`，不要根据 ticker/name 猜测。

规则：

- 用户已经给出显式 AInvest `market_code`，例如 `185:AAPL`，可直接使用，不必重复解析。
- `security`、related、holdings、K 线、逐笔等需要具体标的或行业代码的请求，都以 `ainvest-marketcode` 返回的 `market_code` 为准。
- `multi_kline` / `single_tick` 需要将解析得到的 `market_code` 拆成 `market` 和 `codes`。
- `market_env` 宏观或市场环境指标不需要市场代码，不调用 `ainvest-marketcode`。

indicator 接口的常见 `symbol.type`：

- 已知具体代码：`market_code`
- 同花顺代码输入/输出：`ths_code`
- 市场环境类指标：不传 `symbol`
- 榜单市场池：`market` 或 `block_id`
- 板块或行业池：`block_id`
- 产品 prompt 驱动池：`prompt_id`
- related-symbol：`prompt_id + attr.market_code`
- 行业/ETF 链接池：`link_code + attr.link_type`
- 自定义分组：`group_id`
- 用户自选池：`watchlist`
- prompt 派生池：`prompt_id`
- related 场景：`prompt_id_related`
- prompt 本身：`prompt_id_self`
- group 本身：`group_id_self`
- 宏观区域实体：`macro_region`
- 宏观指标实体：`macro_metric`
- 链级历史：`chain_id`
- 基础行情接口：`code_list`

`multi_kline` / `single_tick` 不使用 indicator 接口里的 `symbol` 结构。需要先把 `market_code`，例如 `185:AAPL`，拆成：

```json
{
  "market": "185",
  "codes": ["AAPL"]
}
```

宏观或 `market_env` 指标通常不需要市场代码，不要强制调用市场代码 skill。`market_env` 与 `security` 指标必须分开组请求，不能混用。

宏观实体请求里，`macro_region` 的指标 id 是宏观上游 catalog 的 `indicator_code`，例如 `GDP`、`CPI`、`IRYY`，返回值取上游 `macro_last`；`macro_metric` 的 symbol 是 `USGDP` 这类 subject，指标 id 是 `macro_last`、`macro_unit`、`macro_previous` 等 `macro_*` 字段。

### 指标命名约定

常见模板命名映射：

- `stock-detail.json`：个股详情。
- `stock-list.json`：列表/排行。
- `related-list.json`：related 场景。
- `holdings.json`：ETF holdings。
- `series-single-indicator.json`：单指标时间序列。
- `multi-kline-minute.json`：分钟 K 线。
- `single-tick.json`：逐笔成交。

### 业务规则

- watchlist 默认保留用户顺序，不主动加 `sort`
- related stock / related ETF 必须带 `attr.market_code`
- `link_code` 必须带 `attr.link_type`
- ETF holdings 场景 `value` 里只能有一个 ETF code
- `multi_kline` 每次最多 16 个代码，且一次最多返回 2000 条 K 线
- `single_tick` 一次只能查 1 个代码
- `分时行情` 用分钟 `multi_kline`
- `逐笔成交/成交明细` 用 `single_tick`
- 数字货币和期权标的只支持 `intraday`
- 美股和指数支持 `pre_market`、`intraday`、`post_market`
- `snapshot` 空值可能是 `{"v": null}`
- `series` 空值可能是空数组
- 返回结果不能假设和请求指标一一对应，映射靠 `req_unique_id`

### 测试与凭证

- `testing.md` 只保留 header 结构和占位符，不应在仓库正文里放真实 API key
- 沙盒 Aime Claw 直接取数必须有 `Authorization: Bearer <AIME_API_KEY>`
- B 端直接取数必须有调用方提供的 `apikey` 请求头
- B 端不能默认复用一个 apikey；按 endpoint family 选择 `index-api` 或 `quoteag` apikey
- B 端也不能默认要求两个 apikey；只要求本次请求实际使用的 endpoint family 对应 apikey
- C 端直接取数必须有调用方提供的 `Cookie` 请求头
- skill 输出时不应默认暴露测试凭证
- live fetch 时由场景对应环境变量组装认证头，不在输出中暴露真实值

### 直接取数的推荐输出

建议统一成下面这个顺序：

1. 说明这是 live fetch 还是仅构造请求
2. 说明用了哪个 endpoint
3. 说明使用了哪个场景和认证头形态，不输出真实凭证
4. 给出核心结果摘要
5. 必要时补 request body
6. 必要时补空值、缺失指标、空序列等解释

如果 live fetch 失败，至少要说明：

- 是否真的发起了请求
- 错误来自鉴权、参数还是数据侧
- 可用于复现或修正的 request body / 缺失头信息

成功返回样例可直接参考 `references/live-fetch-examples.md`。

### attrs 优先级

请求拼接时 attrs 优先级如下：

1. `references/legacy/id_dict.md` 中明确指定的 attrs。
2. `references/tech_param_retry_request.json` 中对应 indicator id 的已验证参数样例。
3. `references/tech.md`、`references/event_id.md`、`references/event_id_index.csv` 等结构化参考。
4. `references/generated/quote_request_lookup.csv` 中由 Excel 生成的 `attrs_json`。
5. 模板默认值。

重要规则：

- `time_period` 统一使用小写值，例如 `snapshot`、`day_1`。
- `begin_end` 默认不要使用 `begin_time = 0` 且 `end_time = 0`，这会取全量历史。生成脚本默认使用最近交易日 00:00:00 UTC 的毫秒时间戳作为 `begin_time`，并保留 `end_time = 0`。
- 需要 `event_id` 的指标必须查询 `event_id` 参考区。
- 需要 `tech_param` 的指标，优先按 `tech_param_retry_request.json` 中对应 indicator id 的默认 `param_sets` 生成。
- `tech_param.children` 必须保持结构化 JSON，不能扁平化丢失。
- `volume_call`、`turnover_call`、`volume_put`、`turnover_put` 的 attrs 需要注意：

```json
{
  "end_count": {
    "end_time": 0,
    "count": 1
  },
  "trade_date": 0
}
```

其中 `count` 可按场景取 `1` 或 `2`。

## Excel 指标表生成 CSV

先从 Tangram 拉取当前 AInvest 支持 API 的指标 Excel；默认写入 skill 内部 `references/generated/export_metric_meta_new.xlsx`：

```bash
python3 scripts/export_indicators.py
```

只检查本地 Excel 距离上次更新多久，不发起网络请求：

```bash
python3 scripts/export_indicators.py --status
```

再从导出的 Tangram 指标 Excel 生成两份 CSV：

- `references/generated/id_dict_quote.csv`
- `references/generated/quote_request_lookup.csv`

运行：

```bash
python3 scripts/build_quote_csvs.py
```

也可以一次完成导出和 CSV 重建：

```bash
python3 scripts/export_indicators.py --rebuild-csvs
```

独立目录下重建 CSV 时建议显式传入外部文件：

```bash
python3 scripts/build_quote_csvs.py --input /path/to/export_metric_meta_new.xlsx --category-map /path/to/id_dict.csv
```

`--category-map` 是可选项；缺失时指标分类会默认回退为 `security`，不会阻塞生成，但 `market_env` 等分类质量会下降。

查询生成参数：

```bash
python3 scripts/find_quote_params.py --query price_gap_ratio_pct
python3 scripts/find_quote_params.py --query stochastic_rsi
```

生成规则：

- `indicator_id` 只取 Tangram 新表 C 列 `*IndexAPI代码`。
- `query_key` 覆盖 `*IndexAPI代码`、`*指标名称`、`英文名称`，方便 skill 检索；不再把 D 列来源代码作为请求 ID。
- `attrs_json` 从 S 列 `扩展属性` 提取，这是拼请求的重要依据。
- `sortable` 从 U 列 `支持排序` 提取。
- `access_guide` 从 Z 列 `接入指南标签` 提取，并决定 `endpoint` 和 `time_range` 形态。
- `category` / `symbol_type` 由 AA 列 `证券实体类型` 决定；`exchange` 由 AB 列 `交易所` 决定。
- `periods` 优先从 S 列 `扩展属性` 的 `time_period.enum_options` 提取；如果 S 列没有 `time_period`，再从 `id_router.yaml` 回退。
- `[AUTO_ATTRS_FROM_ID_DICT]` 只作为补充，同名属性以主 JSON 为准。
- `time_period` 的默认值、枚举值和大小写以表格主 JSON 为准。
- 区间类指标补充别名时不要带时间。例如补充“区间收盘价涨幅”“区间涨幅”，不要把“5分钟”写死进别名；查询阶段再解析为 `time_period=MIN_5`。

## 本地取数脚本

从模板发起请求：

```bash
python3 scripts/fetch_quote.py --scene sandbox --template stock-detail.json --pretty
```

只查看将要请求的 endpoint、脱敏 header 和 body：

```bash
python3 scripts/fetch_quote.py --scene sandbox --template stock-detail.json --dry-run --pretty
```

B 端示例：

```bash
python3 scripts/fetch_quote.py --scene b --template stock-detail.json --pretty
python3 scripts/fetch_quote.py --scene b --template multi-kline-minute.json --pretty
```

C 端示例：

```bash
python3 scripts/fetch_quote.py --scene c --template stock-detail.json --pretty
```

脚本规则：

- `sandbox` 默认读取 `AIME_API_KEY`。
- 默认读取 `../env.json` 中的 `active_scene`、B 端 apikey、C 端 cookie 字段、scene endpoint 和公共 header；可用 `--no-env-file` 禁用。
- 自动 C 端登录只从 gitignored 的 `Skills/env.json` 读取本地账户凭证；登录成功后会覆盖该文件中的 `sessionid` 和 `userid`。凭证和刷新后的会话值均不得提交。
- `b` 仍可通过 `--index-api-apikey`、`--quoteag-apikey` 或 `--auth-value` 覆盖配置文件中的 apikey。
- `c` 仍可通过 `--auth-value` 覆盖配置文件中由 `sessionid` / `userid` 组成的 Cookie；显式值只作用于当前命令，并优先于保存的会话。
- `--dry-run` 只输出脱敏后的请求，不发送请求，也不触发自动登录。
- 不要把真实 Cookie、账户凭证或 apikey 写入仓库文档、模板或生成文件。
- 默认公共 header 为 `Content-Type: application/json`、`Accept-Language: en` 和 `X-Auth-ProgId: 7080`。
- `--endpoint` 可显式指定；不指定时脚本根据模板名和 body 结构推断。

## 校验清单

修改模板或请求拼接规则后，至少运行：

```bash
python3 scripts/validate_templates.py
python3 -m unittest discover -s tests
```

重点检查：

- 每个 indicator 是否都有 `req_unique_id`。
- `req_unique_id` 是否重复。
- endpoint 推断是否正确。
- `snapshot` 模板是否带有有效 `page`。
- `series` 是否带有正确 `time_range`。如果使用 `begin_end`，`begin_time` 和非 0 的 `end_time` 必须是毫秒时间戳；`end_time = 0` 表示取最新；避免 `begin_time = 0` 且 `end_time = 0` 的全量历史请求。
- `related` 场景的 `prompt_id` 是否带 `attr.market_code`。
- `holding` 场景是否只有一个 ETF code。
- `market_env` 与 `security` 是否被分开查询。
- `time_period` 是否统一为小写。

当前校验会检查这些规则：

- 每个 indicator 必须有 `req_unique_id`
- `req_unique_id` 不能重复
- 常见指标 id 与约定命名是否一致
- related 场景的 `prompt_id` 是否带 `attr.market_code`
- `link_code` 是否带 `attr.link_type`
- `holding` 是否只有一个 code
- `sort.pos` 是否越界
- snapshot 模板是否带有效 `page`
- `multi_kline` 是否满足 code 数和 `time_range` 约束
- `single_tick` 是否满足单代码和固定 `time_range` 约束
- `fetch_quote.py --dry-run` 是否能对典型模板推断正确 endpoint

## 维护原则

- 优先修改模板和精简 references，不要优先改大字典。
- 新增常用指标时，先补到拆分后的 indicator references，再视情况补 `legacy/id_dict.md`。
- 新增模板时同步更新 `template-index.md`、`scenarios.md` 和必要的模板写作规则。
- 业务样例优先放到 `assets/request-templates/`。
- 原始协议说明放到 legacy 文档，不要挤进主入口。
- `event_id.md`、`event_id_index.csv`、`tech.md`、`tech_param_retry_request.json` 是随 skill 提交的静态参考；更新时手动同步并校验索引内容。
- 大体积响应结果不要放回 skill 运行目录；若新增覆盖率工具，必须提交脚本、测试和 README 说明，不能只保留引用路径。

## 新接手建议

新接手这个 skill，建议先读：

1. `README.md`
2. `SKILL.md`
3. `references/index.md`
4. `references/scenarios.md`
5. `references/template-index.md`
6. `references/template-writing.md`

然后再看具体模板和指标参考。
