# 历史时间序列指标数据接口协议

该文件保留为原始协议型补充资料，优先阅读 `series.md`、`template-index.md` 和拆分后的指标参考文件。

  

# 介绍

说明：获取**证券实体**相关指标的一段时间范围数据接口，类似画线式取数，不包括明细数据和宏观数据。（限制代码数量）  
场景：K线图、折柱图场景

# 快速上手

## 场景一：获取AAPL 和LLY 的5日区间涨幅指标数据，从 1746378000000 至今的每天的数据

```
  

```

### 请求体

  展开源码

```
{
    "symbol": {
        "type": "market_code",
        "value": ["185:AAPL","169:LLY"]
    },
    "indicator": [{
            "id": "inr-price_change_ratio_pct-sum",
            "req_unique_id": "id_0", 
            "attr": {
                "trade_class": "intraday",
                "time_period": "day_5"
            }
        }
    ],
    "time_range": {
        "type": "begin_end",
        "begin_time": 1746378000000,
        "end_time": 0,
        "time_period":"day_1"
    }
}
```

  

### 返回体

  展开源码

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
			"id": "inr-price_change_ratio_pct-sum",
        "req_unique_id": "id_0",  
         "attr": {
				"trade_class": "intraday",
				"time_period": "day_5"
			}
		}],
		"symbol_type": "market_code",
		"data": [{
			"symbol_code": "169:LLY",
			"value": [{
				"attr": {},
				"value": [{
					"t": 1677196800000,
					"v": 121.5
				}, {
					"t": 1677456000000,
					"v": 118.51
				}]
			}]
		}, {
			"symbol_code": "185:AAPL",
			"value": [{
				"value": [{
					"t": 1677196800000,
					"v": 227.3
				}, {
					"t": 1677456000000,
					"v": 230.12
				}]
			}]
		}]
	}
}
```

  

## 场景二：获取AAPL 和LLY 的5日区间涨幅指标数据以及业绩预报数据，从 1746378000000 至今的每天的数据

### 请求体

  展开源码

```
{
	"symbol": {
		"type": "market_code",
		"value": ["185:AAPL", "169:LLY"]
	},
	"indicator": [{
			"id": "inr-price_change_ratio_pct-sum",
 			"req_unique_id": "id_0", 
        "attr": {
				"trade_class": "intraday",
				"time_period": "day_5"
			}
		},
		{
			"id": "forecast_eps_quarter",
  			"req_unique_id": "id_1"
      }
	],
	"time_range": {
		"type": "begin_end",
		"begin_time": 1746378000000,
		"end_time": 0,
		"time_period": "day_1"  
	}
}
```

  

### 返回体

  展开源码

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
				"id": "inr-price_change_ratio_pct-sum",
  	    		"req_unique_id": "id_0",  
            "attr": {
					"trade_class": "intraday",
					"time_period": "day_5"
				}
			},
			{
				"id": "forecast_eps_quarter",
  	    		"req_unique_id": "id_1",   
         }
		],
		"symbol_type": "market_code",
		"data": [{
				"symbol_code": "169:LLY",
				"value": [{
						"attr": {},
						"value": [{
								"t": 1677196800000,
								"v": 121.5
							},
							{
								"t": 1677456000000,
								"v": 118.51
							}
						]
					},
					{
						"value": [{
								"t": 1677196800000,
								"v": 1.26,
								"fp": "2025-Q1"
							},
							{
								"t": 1677456000000,
								"v": 118.51,
								"fp": "2025-Q2"
							}
						]
					}
				]
			},
			{
				"symbol_code": "185:AAPL",
				"value": [{
					"value": [{
							"t": 1677196800000,
							"v": 227.3
						},
						{
							"t": 1677456000000,
							"v": 230.12
						}
					]
				}, {
					"value": []
				}]
			}
		]
	}
}
```

  

  

## 场景三： 请求宏观数据 的历史序列数据

### 请求体

  展开源码

```
{
    "indicator": [{
            "id": "ext_metric_altcoin_season_index",
			"req_unique_id": "id_0",
            "attr": {
                "trade_class": "intraday",
                "time_period": "day_5"
            }
        }
    ],
    "time_range": {
        "type": "begin_end",
        "begin_time": 1746378000000,
        "end_time": 0,
        "time_period":"day_1"
    }
}


```

  

### 返回体

  展开源码

```
{
	"status_code": 0,
	"status_msg": "success",
	"data": {
		"indicator": [{
			"id": "ext_metric_altcoin_season_index",
 			"req_unique_id": "id_0", 
             "attr": {
				"trade_class": "intraday",
				"time_period": "day_5"
			}
		}],
		"symbol_type": null,
		"data": [{
			"value": [{
				"attr": {},
				"value": [{
					"t": 1677196800000,
					"v": 121.5
				}, {
					"t": 1677456000000,
					"v": 118.51
				}]
			}]
		}]
	}
}
```

  

# 输入参数说明

## 请求URI

/indicator/v2/series

## 请求头（Header）

| 字段  | 含义  | 格式  | 说明  | 是否必须 |
| --- | --- | --- | --- | --- |
| Content-Type | 数据类型和编码 | application/json; charset=utf-8 | json格式，utf-8编码 | 是   |
| sw8 | 链路追踪信息（SW8标准） | [skywalking的trace协议规范](http://cf.myhexin.com/pages/viewpage.action?pageId=758154439) | [SkyWalking Web端接入说明](http://cf.myhexin.com/pages/viewpage.action?pageId=664470948) | 是   |
| Accept-Language | 请求语言 | Accept-Language: en,zh-hans<br><br>  <br><br>可以设置多种权重的语言，语法规则是Accept-Language: <language1>\[;q=<q-value1>\]\[, <language2>\[;q=<q-value2>\]\]...，具体请参考  [Accept-Language](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language) | 常见的 Accept-Language 请求头部中使用的语言标记的枚举示例：<br><br>en: 英语  <br>fr: 法语  <br>de: 德语  <br>es: 西班牙语  <br>zh-hans: 简体中文  <br>zh-hant: 繁体中文（台湾）  <br>ja: 日语  <br>ko: 韩语  <br>ru: 俄语  <br>pt: 葡萄牙语  <br>ar: 阿拉伯语<br><br>更多语言定义见 [多语言代码规范](http://cf.myhexin.com/pages/viewpage.action?pageId=998584940) | 否   |
| X-Card-Id | 具体的某一个功能页面，所有客户端全局唯一 | string |     | 否   |

## 请求方法（Method）

仅支持POST请求

  

## 请求参数（Request Body）

整体参数说明

  

| 字段名称 | 类型  | 是否必填 | 描述  |
| --- | --- | --- | --- |
| symbol | ```<br>Object<br>``` | 否   | ```<br>代码表  <br>  <br>1）对于宏观、市场环境类的指标，symbol不用填，比如恐慌指数、数字币山寨指数等  <br>2）对于以证券代码为key的数据，比如行情、财务等，symbol必须填<br>``` |
| ```<br>├── type<br>``` | String | 是   | 代码表的类型，`market_code` 或 `ths_code`。 |
| ```<br>└── value<br>``` | Array of String | 是   | 代码表值，与`type`对应。 |
| indicator | ```<br>Array of Object<br>``` | 是   | 指标查询列表 |
| ```<br>├── id<br>``` | String | 是   | 指标的唯一 ID，例如`inr-price_change_ratio_pct-sum` |
| ```<br>├── req_unique_id<br>``` | String | 是   | 请求方用于标识这个指标的唯一id，服务端不处理，原样返回，解决指标id不能作为唯一key的问题 |
| ```<br>└── attr<br>``` | ```<br>Object<br>``` | 是   | 指标的附加属性。详见 [http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明](http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明) |
| ```<br>    ├── trade_class<br>``` | String | 否   | 交易类别，如`intraday`盘中。 |
| ```<br>    ├── time_period<br>``` | String | 否   | 时间周期，如`day_5`表示5天。 |
| time\_range | Object | 是   | 时间范围查询条件。 |
| ```<br>├── type<br>``` | String | 是   | 时间范围的类型，如`begin_end`。 |
| ```<br>├── begin_time<br>``` | Number | 是   | 起始时间戳，单位为毫秒。 |
| ```<br>├── end_time<br>``` | Number | 是   | 结束时间戳，单位为毫秒。 |
| ```<br>└── time_period<br>``` | String | 否   | 时间周期，如`day_1`表示1天，表示1天1个数据，不填的时候以指标的周期为准，比如指标是min\_60的成交量，那么返回数据也是60分钟一个点 |

  

### 参数字段细节说明

#### Symbol 代码表（仅支持 行情代码表 和 同花顺代码表）

| 以下组合选一种 | 字段  | 是否必须 | 类型  | 含义  | 举例  | 说明  |
| --- | --- | --- | --- | --- | --- | --- |
| **行情代码表** | type | 是   | string | market\_code – 类型是行情代码表 | "type"："market\_code",<br><br>"value":\["185:QQQ","169:IBM"\] |     |
| value | 是   | array | 具体的代码列表 |     |
|     |     |     |     |     |     |     |
| **同花顺代码表** | type | 是   | string | ths\_code – 类型是同花顺代码表 | "type"："ths\_code",<br><br>"value":\["AAPL.O","BABA.N"\] |     |
| value | 是   | array | 具体的代码列表 |     |
|     |     |     |     |     |     |     |
| 区块链ID | type | 是   | string | chain\_id – 类型是区块链ID | "type"："chain\_id",<br><br>"value":\["L000000006"\] |     |
|     | value | 是   | array | 具体的区块链列表 |     |

  

  

  

#### time\_range 字段说明

|     |     |     |     |     |
| --- | --- | --- | --- | --- |
| 组合1：\[开始时间，结束时间\] | type | 是   | 类型：begin\_end |     |
| begin\_time | 是   | 精确到毫秒的时间戳，表示日（包括）以上的时间时，用对应交易所的中午12点 |     |
| end\_time | 是   | 精确到毫秒的时间戳，表示日（包括）以上的时间时，用对应交易所的中午12点 | 0 – 表示最新的时间 |
| time\_period | 是   | 时间间隔单位，即多少时间取一个点的数据，比如day\_1 表示每天1个点 |     |
|     |     |     |     |     |
| 组合2：具体时间往前取总共N条 | type | 是   | 类型：end\_count |     |
| end\_time | 是   | 精确到毫秒的时间戳 | 0 – 表示最新的时间 |
| ~offset~ | ~否~ | ~end\_time偏移的条数， 0（默认值） – 表示不偏移，就使用end\_time，> 0 – 表示往后偏移的条数， < 0 表示往前偏移的条数~ | ~暂不支持~<br><br>~end\_time = 0~<br><br>~offset = -2~<br><br>~count = 100~<br><br>~time\_period = day\_1~<br><br>~表示从最新的时间往前偏移2天开始（包括）往前取100天数据~ |
| count | 是   | \> 0 表示从end\_time（包括）往前总的数据条数 | 比如请求最新交易日（包括）往前的1条数据，那么<br><br>end\_time = 0, count = 1 |
| time\_period | 是   | 时间间隔单位，即多少时间取一个点的数据，比如day\_1 表示每天1个点 |     |
|     |     |     |     |     |
| 组合3：取某个交易日的数据 | type | 是   | 类型：trade\_date |     |
| trade\_date | 是   | yyyymmdd 表示某个交易日， 0 表示最新交易日 |     |
| time\_period | 是   | 时间间隔单位，即多少时间取一个点的数据，比如min\_1 表示每分钟1个点 | 只支持 min\_xx |
|     |     |     |     |     |
| ~组合3：具体时间往后取总共N条~ | ~type~ | ~是~ | ~类型：begin\_count~ |     |
| ~begin\_time~ | ~是~ | ~精确到毫秒的时间戳~ | ~0 – 表示最早的时间~ |
| ~count~ | ~是~ | ~\> 0 表示从begin\_time（包括）往后总的数据条数~ |     |
| 组合  | 字段  | 是否必须 | 含义  | 说明  |
| --- | --- | --- | --- | --- |

  

  

  

  

# 返回结果

  

| 字段名称 | 代码类型 | 描述  | 是否必存在 |
| --- | --- | --- | --- |
| status\_code | Number | 状态码，\`0\` 表示成功，非 \`0\` 表示错误 | 是   |
| ```<br>status_msg<br>``` | String | 状态信息，成功或错误的描述 | 是   |
| ```<br>data<br>``` | Object | 响应体的主数据，当异常或者所有id均无效时，返回的是空对象 | 是   |
| ```<br>├── indicator<br>``` | Array of Object | 指标查询列表 | 否   |
| ```<br>│    ├── id<br>``` | String | 指标的唯一 ID，例如\`inr-price\_change\_ratio\_pct-sum\` | 是   |
| ```<br>│    ├── req_unique_id<br>``` | String | 本次请求的id，跟请求入参的保持一致，给用户自行判断，映射指标 | 是   |
| ```<br>│    └── attr<br>``` | Object | 指标的附加属性。 | 否   |
| ```<br>├── symbol_type<br>``` | String | 代码表的类型，\`market\_code\` 或 \`ths\_code\`。 | 否   |
| ```<br>└── data<br>``` | Array of Object | 实际返回的数据列表 | 否   |
| ```<br>     ├── symbol_code<br>``` | String | 股票代码，如 \`169:LLY\` | 否   |
| ```<br>     └── value<br>``` |  Array Of Object | 实际返回的数据列表。 | 是   |
| ```<br>          ├── attr<br>``` | Object | 该指标的数据属性，例如货币单位等。详见 [http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明](http://cf.myhexin.com/pages/viewpage.action?pageId=1404047441#indexapi指标id字典-指标属性定义和说明) | 否   |
| ```<br>          └── value<br>``` | Array of Object | 一个指标在一段时间区间内的数据列表 | 是   |
| ```<br>              ├── t<br>``` | Number | 精确到毫秒的时间戳 | 是   |
| ```<br>              └── v<br>``` | Any | 具体指标值。可以是数字或字符串。 | 是   |

# Q&A

问题1：后端处理异常 或 请求里面所有的id均为无效

答：返回内容为{"status\_code":xxx,"status\_msg":"xxxxx","data":{}}

  

问题2：如果指标id有效，但对应代码没有数据？

答：请求如果出现数据为空的情况，那么返回的值为一个空数组，给一个返回为空的样例：{"status\_code":0,"status\_msg":"success","data":{"indicator":\[{"id":"inr-price\_change\_ratio\_pct-sum","attr":{"trade\_class":"intraday","time\_period":"day\_5"}},{"id":"forecast\_eps\_quarter"}\],"symbol\_type":"market\_code","data":\[{"symbol\_code":"169:LLY","value":\[{"attr":{},"value":\[{"t":1677196800000,"v":121.5},{"t":1677456000000,"v":118.51}\]},{"value":\[{"t":1677196800000,"v":1.26,"fp":"2025-Q1"},{"t":1677456000000,"v":118.51,"fp":"2025-Q2"}\]}\]},{"symbol\_code":"185:AAPL","value":\[{"value":\[{"t":1677196800000,"v":227.3},{"t":1677456000000,"v":230.12}\]},{"value":\[\]}\]}\]}}

  

问题3： 请求方中请求中，不能用指标id来作为唯一key，当一个请求中，同一个id出现了多次（区间涨幅指标）如何去做好映射？

答： indicator中的req\_unique\_id 字段，用于可以自定义一个key，返回的时候服务端会带着这个key原样返回，请求方可以根据这个自定义的req\_unique\_id 去做唯一映射。该字段非必传，只有带上才会返回。

  

问题4：针对宏观数据的请求方式？

答：宏观数据指标没有对应的实体，因此不需要传symbol，但是不支持宏观指标与非宏观指标同时请求。

  

问题5：一个指标里面的数据顺序是怎么样的？

答：按照时间(t)从小到大排序
