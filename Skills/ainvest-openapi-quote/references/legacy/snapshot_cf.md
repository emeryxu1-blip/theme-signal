# 快照指标数据接口协议

该文件保留为原始协议型补充资料，优先阅读 `snapshot.md`、`template-index.md` 和拆分后的指标参考文件。

# 介绍

支持获取指标的快照数据

# 快速上手

## 场景一：获取某个代码五分钟涨跌的数据

### 请求体：

  展开源码

```
{
	"symbol": [{
		"type": "market_code",
		"value": ["185:QQQ"]
	}],
	"indicator": [{
		"id": "inr-change",
		"attr": {
			"trade_class": "intraday",
			"time_period": "min_5",
			"period_type": "rolling"
		},
        "req_unique_id":"id_0"
	}],
	"sort": [{
		"pos": 0,
		"order": "desc"
	}],
	"page": {
		"begin": 0,
		"count": 5
	}
}
```

### 返回结果：

  展开源码

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
			"id": "inr-change",
			"attr": {
				"trade_class": "intraday",
				"time_period": "min_5",
				"period_type": "rolling"
			},
            "req_unique_id":"id_0"
		}],
		"symbol_type": "market_code",
		"data": [{
			"symbol_code": "185:QQQ",
			"value": [{
				"v": 13.5
			}]
		}],
		"page": {
			"total": 1
		}
	}
}
```

  

## 场景二：获取某个市场的5分钟区间涨幅数据与名称，并且按照5分钟区间涨幅从大到小进行排序取前2

### 请求参数

  展开源码

```
{
	"symbol": [{
		"type": "market",
		"value": ["E"]
	}],
	"indicator": [{
		"id": "inr-price_change_ratio_pct-sum",
		"attr": {
			"trade_class": "intraday",
			"time_period": "min_5"
		},
        "req_unique_id":"id_0"
	}, {
		"id": "security_name",
        "req_unique_id":"id_1"
	}],
	"sort": [{
		"pos": 0,
		"order": "desc"
	}],
	"page": {
		"begin": 0,
		"count": 2
	}
}
```

  

### 返回结果

  展开源码

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
			"id": "inr-price_change_ratio_pct-sum",
			"attr": {
				"trade_class": "intraday",
				"time_period": "min_5"
			},
            "req_unique_id":"id_0"
		}, {
			"id": "55",
            "req_unique_id":"id_1"
		}],
		"symbol_type": "market_code",
		"data": [{
				"symbol_code": "185:AAPL",
				"value": [{
						"v": 13.5
					},
					{
						"v": "Apple"
					}
				]
			},
			{
				"symbol_code": "185:TSLA",
				"value": [{
						"v": 0.89
					},
					{
						"v": "Tsla"
					}
				]
			}
		],
		"page": {
			"total": 2
		}
	}
}
```

  

## 场景三：获取宏观数据数字币山寨指数

### 请求参数

  展开源码

```
{
	"indicator": [{
		"id": "ext_metric_altcoin_season_index",
		"attr": {
			"trade_class": "intraday",
			"time_period": "min_5"
		},
        "req_unique_id":"id_0"
	}, {
		"id": "security_name",
        "req_unique_id":"id_1"
	}],
	"sort": [{
		"pos": 0,
		"order": "desc"
	}],
	"page": {
		"begin": 0,
		"count": 1
	}
}
```

  

  

### 返回参数

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
			"id": "ext_metric_altcoin_season_index",
			"attr": {
				"trade_class": "intraday",
				"time_period": "min_5"
			},
            "req_unique_id":"id_0"
		}],
		"symbol_type": "",
		"data": [{
				"value": [{
						"v": 13.5
					}
				]
			}
		],
		"page": {
			"total": 1
		}
	}
}
```

  

  

# 输入参数说明

## 请求URI

\*/indicator/v2/snapshot

## 请求方法（Method）

仅支持POST请求

## 请求参数（Request Body）

### 整体参数说明

| ```<br>字段名称<br>``` | ```<br>类型<br>``` | ```<br>是否必填<br>``` | ```<br>描述<br>``` |
| --- | --- | --- | --- |
| ```<br>symbol<br>``` | ```<br>Array of Object<br>``` | 否   | ```<br>代码表，支持多种条件进行集合运算。最终代码表是((并集) + (交集)) - (差集)  <br>  <br>1）对于宏观、市场环境类的指标，symbol不用填，比如恐慌指数、数字币山寨指数等  <br>2）对于以证券代码为key的数据，比如行情、财务等，symbol必须填<br>``` |
| ```<br>├── op<br>``` | ```<br>String<br>``` | 否   | 表示 `value` 加入 `symbol` 集合的方式，默认是`union`。 - `union`: 并集 - `exclude`: 差集 - `intersect`: 交集 |
| ```<br>├── type<br>``` | String | 是   | 代码表的类型，支持`market_code`、`prompt_id`等。 |
| ```<br>├── value<br>``` | Array of String | 是   | 代码表值，与`type`对应。 |
| ```<br>└── attr<br>``` | Object | 否   | 可扩展的键值对，用于代码表的附加属性。例如 `market_code`。 |
| ```<br>indicator<br>``` | ```<br>Array of Object<br>``` | 是   | 指标查询列表。支持多个指标。不保证返回顺序 |
| ```<br>├── id<br>``` | String | 是   | ```<br>指标的唯一ID。例如`inr-change`<br>``` |
| ```<br>├── req_unique_id<br>``` | String | 是   | 请求方用于标识这个指标的唯一id，请确保单个请求里面具有唯一性。服务端不处理，原样返回，解决指标id不能作为唯一key的问题 |
| ```<br>└── attr<br>``` | Object | 否   | ```<br>指标的附加属性，如`trade_class`、`time_period`等。详见 [http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明](http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明)<br>``` |
| ```<br>    ├── trade_class<br>``` | String | 否   | ```<br>交易类别，如`intraday`盘中<br>``` |
| ```<br>    ├── time_period<br>``` | String | 否   | ```<br>时间周期，如`min_5`表示5分钟<br>``` |
| ```<br>    └── period_type<br>``` | String | 否   | ```<br>周期类型，如`rolling`表示滚动时间区间<br>``` |
| sort | ```<br>Array of Object<br>``` | 否   | ```<br>排序规则，支持多字段排序。 如果sort不传，则保持symbol的顺序，比如block_id返回的是A,B,C，那么返回给客户端就是A,B,C的代码顺序<br>``` |
| ```<br>├── pos<br>``` | ```<br>Number<br>``` | ```<br>是<br>``` | 根据`indicator`中的第N个指标进行排序（从0开始）。 |
| ```<br>└── order<br>``` | ```<br>String<br>``` | ```<br>是<br>``` | 排序顺序。  `asc`: 升序,   `desc`: 降序 |
| ```<br>page<br>``` | ```<br>Object<br>``` | 是   | ```<br>分页信息。<br>``` |
| ```<br>├── begin<br>``` | ```<br>Number<br>``` | 是   | ```<br>从什么位置开始取数，0 表示从第1个开始<br>``` |
| ```<br>└── count<br>``` | ```<br>Number<br>``` | 是   | ```<br>返回排序数据的个数。<br>``` |
| filter | Object | 否   | ```<br>过滤条件。多个条件之间是`AND`关系<br>``` |
| ```<br>├── type<br>``` | ```<br>String<br>``` | **是** | ```<br>过滤类型，如`json_expr`<br>``` |
| ```<br>└── condition<br>``` | ```<br>Array of Object<br>``` | **否** | ```<br>排序规则。支持多字段排序。<br>``` |
| ```<br>    ├── pos<br>``` | ```<br>Number<br>``` | **是** | ```<br>indicator中的第N个指标（从0开始）<br>``` |
| ```<br>    ├── op<br>``` | ```<br>String<br>``` | 是   | ```<br>操作符，如`between`、`in`、`gt`等。<br>``` |
| ```<br>    └── value<br>``` | ```<br>Array of Number<br>``` | 是   | 操作的值 |

  

  

### 参数字段细节说明

#### Symbol 代码表展示

| 以下组合选一种 | 字段  | 是否必须 | 类型  | 含义  | 举例  | 说明  |
| --- | --- | --- | --- | --- | --- | --- |
| **行情代码表** | type | 是   | string | market\_code – 类型是行情代码表 | "type"："market\_code",<br><br>"value":\["185:QQQ","169:IBM"\] |     |
| value | 是   | array | 具体的代码列表 |     |
|     |     |     |     |     |     |     |
| **同花顺代码表** | type | 是   | string | ths\_code – 类型是同花顺代码表 | "type"："ths\_code",<br><br>"value":\["AAPL.O","BABA.N"\] |     |
| value | 是   | array | 具体的代码列表 |     |
|     |     |     |     |     |     |     |
| **市场** | type | 是   | string | market  – 类型是行情市场 | "type"："market",<br><br>"value":\["UDC"\] | 行情市场 |
| value | 是   | array | 具体的行情市场 |
|     |     |     |     |     |     |     |
| **板块** | type | 是   | string | block\_id – 类型是板块 | "type"："block\_id",<br><br>"value":\["C191"\] |     |
| value | 是   | array | 具体的板块id |     |
|     |     |     |     |     |     |     |
| **问句** | type | 是   | string | prompt\_id – 类型是问句id | "type"："prompt\_id",<br><br>"value":\["6762c178784e3a2b800f5bae"\],<br><br>"attr":{"market\_code":"185:AAPL"} |     |
| value | 是   | array | 具体的问句id |     |
| attr | 否   | object | 问句的模板变量，格式为key2value |     |
|     |     |     |     |     |     |     |
| **问句自身  <br>** | type | 是   | string | prompt\_id\_self –   针对这个id本身，而不是成分股 |     |     |
| value | 是   | array | 具体的问句id |     |     |
|     |     |     |     |     |     |     |
| **自定义分组** | type | 是   | string | group\_id  –  业务自定义的池子，成分可以是代码表、板块id列表 或 prompt id列表 |     |     |
| value | 是   | array | group id |     |     |
|     |     |     |     |     |     |     |
| **关联代码** | type | 是   | string | link\_code – 类型是关联代码，比如成分股、持仓股等 | "type"："link\_code",<br><br>"value":\["89:861070"\],<br><br>"attr":{"link\_type":"component"} |     |
| value | 是   | array | 具体的关联代码，使用market\_code |     |
| attr | 是   | object | link\_type – 关联类型，枚举值含义如下：<br><br>    component  – 指数成分股，表示指数代码对应的成分股<br><br>    holding – 基金持仓股，表示指数代码对应的成分股<br><br>    subsector – 子行业，表示指数代码对应的子行业指数 |     |

  

#### Symbol中的op

| op  | 说明  |
| --- | --- |
| union | 并集  |
| ```<br>intersect<br>``` | 交集  |
| ```<br>exclude<br>``` | 差集  |
| ```<br>最后的代码是： ((union[1] + union[2] + union[...]) + (intersect[1] & intersect[2] & intersect[...])) - (exclude[1] + exclude[2] + exclude[...])<br>``` |     |

  

  

#### filter 字段说明

|     |     |     |     |     |
| --- | --- | --- | --- | --- |
| ```<br>pos<br>``` | 是   | number | ```<br>indicator里面的第N个指标<br>``` |     |
| ```<br>op<br>``` | 是   | string | ```<br>between -- 在[ value[0],value[1] ]范围内  <br>in -- 和value中的任何一个值相等，枚举  <br>gt -- 大于value[0]  <br>ge -- 大于等于value[0]  <br>lt -- 小于value[0]  <br>le -- 小于等于value[0]  <br>eq -- 等于value[0]<br>``` | ```<br>描述value的关系  <br>  <br>between 限制 value[0] < value[1]  <br>eq value只能有1个元素<br>``` |
| ```<br>value<br>``` | 是   | array | ```<br>结合op的操作，描述id的范围<br>``` | ```<br>1、可以是number数组，如描述价格 "10" 的值范围  <br>2、可以是string数组，如描述日期<br>``` |

# 返回结果

## 请求返回体（Response）

  

| 字段名称 | 类型  | 是否必存在 | 描述  |
| --- | --- | --- | --- |
| status\_code | Number | 是   | 状态码，\`0\` 表示成功，非 \`0\` 表示错误。 |
| status\_msg | String | 是   | 状态信息，成功或错误的描述。 |
| data | Object | 是   | 异常或者所有id均无效的时候是空对象 |
| ├── indicator | Array of Object | 否   | 指标信息列表 |
| │    ├── id | String | 是   | 指标的唯一 ID，例如\`inr-price\_change\_ratio\_pct-sum\`。 |
| │    ├── req\_unique\_id | String | 是   | 本次请求的id，跟请求入参的保持一致，给用户自行判断，映射指标 |
| │    └── attr | Object | 否   | \`time\_period\`: 时间周期，如\`min\_5\`表示5分钟。详见 [http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明](http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明) |
| ├── symbol\_type | String | 否   | 代码表的类型，\`market\_code\` 或 \`ths\_code\` |
| ├── data | Array of Object | 否   | 实际返回的数据列表 |
| │    ├── symbol\_code | String | 否   | 股票代码，如 \`185:AAPL\` |
| │    └── value | Array of Object | 是   | 股票代码对应的值，与 \`indicator\` 列表一一对应 |
| │         ├── t | Number | 否   | 毫秒时间戳，不一定有 |
| │         └── v | Any | 是   | 具体指标值。可以是数字或字符串。 |
| └── page | Object | 否   | 分页信息 |
|    └── total | Number | 是   | 返回数据的总数 |

  

# Q&A

问题1：后端处理异常 或 请求里面所有的id均为无效

答：返回内容为{"status\_code":xxx, "status\_msg":"xxxxx", "data":{}}

  

问题2：指标有效，但对应的代码没有数据的情况？

答：当出现请求的指标无数据的情况下，规范返回数据对象，值（v） 为null ，即 {“v”:null} ， 一个返回数据包含null的样例：{"status\_code":0,"status\_msg":"success","data":{"indicator":\[{"id":"inr-price\_change\_ratio\_pct-sum","attr":{"trade\_class":"intraday","time\_period":"min\_5"}},{"id":"security\_name"}\],"symbol\_type":"market\_code","data":\[{"symbol\_code":"185:AAPL","value":\[{"v":13.5},{"v":null}\]},{"symbol\_code":"185:TSLA","value":\[{"v":null},{"v":"Tsla"}\]}\],"page":{"total":2}}} 

  

问题4：在一个请求返回结果中，指定了某个字段的值进行排序，如果出现部分值为null的情况，排序结果应该是怎样的？

答： 如果部分代码该指标没有值，那么对应的value是null，排序的时候无论正序还是逆序，这些代码都排在最后（对于null的代码，按symbol\_code的字典序从小到大排，比如185:ABC应该在185:ACD前面）。

  

问题5：针对宏观数据的请求方式？

答：宏观数据指标没有对应的实体，因此不需要传symbol，但是不支持宏观指标与非宏观指标同时请求。

  

问题6： 请求方中请求中，不能用指标id来作为唯一key，当一个请求中，同一个id出现了多次（区间涨幅指标）如何去做好映射？

答： indicator中的req\_unique\_id 字段，用户可以自定义一个key，返回的时候服务端会带着这个key原样返回，请求方可以根据这个自定义的req\_unique\_id 去做唯一映射。req\_unique\_id 字段必传，传什么返回什么。
