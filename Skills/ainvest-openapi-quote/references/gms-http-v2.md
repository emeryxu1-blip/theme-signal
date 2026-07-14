# GMS-基础行情HTTP协议-v2

# 一 全局说明

    此协议适用于 app/pc/web等C端 和 扩展行情服务等内部服务。

## 1 请求

### 1.1 Method

目前只支持post，因为get请求如果代码表比较多在经过ngnix或其他网关的时候容易被长度限制

### 1.2 Headers

   压缩、鉴权等公共header详见 [GMS-HTTP网关协议](http://cf.myhexin.com/pages/viewpage.action?pageId=924228521)

## 2 响应

### 2.1 响应格式

基本格式如下，data里面的内容根据不同的接口定义

{  
"status\_code": 0,  
"data": {}

}

### 2.2 Headers

| 字段  | 是否必须 | 内容  | 说明  |
| --- | --- | --- | --- |
| Content-Type | 是   | application/json; charset=utf-8 | 数据类型和编码 |

## 3 数据说明

1）空值的处理

5.1） 请求的代码不存在，则不返回数据，即json里面没有这只代码

5.2）请求的数据项不存在，则这个数据项没有，比如在k线里面请求了 收盘价、现价和成交量，因为k线没有现价，所以返回的数据项只有收盘价和成交量

5.3）如果数据项存在，但值为空，则返回 null。（在请求的响应消息里面，hqfile里面的0xffffffff和0x80000000都当作空值处理）

5.4）在推送的时候，如果本次的数据和上一次没有变化，则此数据项不返回；如果有值，但值是空，则返回null。（hqfile里面的0xffffffff表示和上一笔没有变化，0x80000000表示是空值）

  

2）数据项定义

  参数定义参考 [GMS-json基础字段定义](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921)

  数据项id的定义参考 [数据字典---数据id和数据名](http://172.20.200.191:8003/pages/viewpage.action?pageId=811829327)

# 二 请求接口

## 最新交易日快照

适用场景：个股、自选股、排名页面等列表类

约束：代码数 <= 100;  data\_fields <= 20；

### 1 请求

#### 1.1 URL

/quote/v2/last\_snapshot

#### 1.2 请求参数

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   |  \[\] | "code\_list":\[ { "market":"UDC", "codes":\["BTCUSD"\] } \]    |
| trade\_class | 是   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |
| data\_fields | 是   | string \[\] | 需要的数据项id，比如最新价是10 |
| lang | 否   | string | 影响字段55返回结果  zh-hans 简体中文（默认）   en  英语 |

  

data\_fields范围

| trade\_class | data\_id | 含义  | 类型  | 支持的品种 | 说明  |
| --- | --- | --- | --- | --- | --- |
| pre\_market<br><br>(盘前) | 1   | 时间  | number | UUS |     |
| 7   | 开盘价 | number |     |
| 8   | 最高价 | number |     |
| 9   | 最低价 | number |     |
| 10  | 最新价 | number |     |
| 13  | 总成交量 | number |     |
| 19  | 总成交额 | number |     |
| 24  | 买一价 | number |     |
| 25  | 买一量 | number |     |
| 30  | 卖一价 | number |     |
| 31  | 卖一量 | number |     |
| 199112 | 涨跌幅 | number |     |
| 264648 | 涨跌值 | number |     |
| intraday<br><br>(盘中) | 1   | 时间  | number | ALL |     |
| 6   | 昨收  | number | ALL |     |
| 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 12  | 交易方向 | string | ALL | 数字货币不支持，包含 "sell" "buy" "unknown" |
| 13  | 总成交量 | number | ALL |     |
| 14  | 外盘成交量 | number | ALL |     |
| 15  | 内盘成交量 | number | ALL |     |
| 18  | 成交次数 | number | ALL |     |
| 19  | 总成交额 | number | ALL |     |
| 54  | 均价  | number | ALL |     |
| 24  | 买一价 | number | ALL |     |
| 25  | 买一量 | number | ALL |     |
| 30  | 卖一价 | number | ALL |     |
| 31  | 卖一量 | number | ALL |     |
| 37  | 指数成分股总数 | number | 89  |     |
| 38  | 指数成分股上涨家数 | number | 89  |     |
| 39  | 指数成分股上涨家数 | number | 89  |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌值 | number | ALL |     |
| ```<br>330378<br>``` | 24小时成交量 | number | UDC |     |
| ```<br>330379<br>``` | 24小时成交额 | number | UDC |     |
| post\_market<br><br>(盘后) | 1   | 时间  | number | UUS |     |
| 7   | 开盘价 | number |     |
| 8   | 最高价 | number |     |
| 9   | 最低价 | number |     |
| 10  | 最新价 | number |     |
| 13  | 总成交量 | number |     |
| 19  | 总成交额 | number |     |
| 24  | 买一价 | number |     |
| 25  | 买一量 | number |     |
| 30  | 卖一价 | number |     |
| 31  | 卖一量 | number |     |
| 199112 | 涨跌幅 | number |     |
| 264648 | 涨跌值 | number |     |

### 2.响应

#### 1）响应格式

```
{
    "status_code": 0,
    "data": {
        //"data_class": "snapshot",
        "quote_data": [
            {
                "market": "具体市场",
                "code": "具体代码",
                "65558": 20221108,
                "65541": "intraday",
                "数据项的key": 数据项对应的value, //比如"65558": 20221108，"65555": "trading"
            }
        ]
    }
}
```

  

### 3\. 请求与响应示例

请求：

```
	{
		"code_list": [{
			"market": "185",
			"codes": ["TSLA"]
		}],
		"trade_class": "intraday",
		"data_fields": ["1", "6", "7", "8", "9", "10", "13"],
	}
```

响应：

```
{
	"data": {
		"data_class": "snapshot",
		"quote_data": [{
			"65535": 20221223,
			"65541": "intraday",
			"code": "TSLA",
			"market": "185",    
            "1": 1671829200000,
            "6": 125.35,
            "7": 126.37,
            "8": 128.6173,
            "9": 121.02,
            "10": 123.15,
            "13": 166989688
            }]
	},
	"status_code": 0
}
```

  

## 最新交易日计算项

适用场景：个股、自选股、排名页面等列表类

约束：代码数 <= 100;  data\_fields <= 20；

### 1 请求

#### 1.1 URL

/quote/v2/last\_calc

#### 1.2 请求参数

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   |  \[\] | "code\_list":\[ { "market":"UDC", "codes":\["BTCUSD"\] } \]    |
| trade\_class | 是   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |
| data\_fields | 是   | string \[\] | 需要的数据项id，比如最新价是10 |

  

data\_fields范围（[指标计算系统已配置支持的指标计算ID](http://cf.myhexin.com/pages/viewpage.action?pageId=968395096)）

| trade\_class | data\_id | 含义  | 类型  | 支持的品种 | 说明  |
| --- | --- | --- | --- | --- | --- |
| pre\_market<br><br>(盘前) | 461256 | 委比  | number | 股票  |     |
| intraday<br><br>(盘中) | 461256 | 委比  | number | 股票  |     |
| 1968584 | 换手率 | number |     |
| 199187 | TTM市盈率 | number |     |
| 2942 | 动态市盈率 | number |     |
| 2946 | 静态市盈率 | number |     |
| 3541450 | 总市值 | number |     |
| 1771976 | 量比  | number |     |
| 723571 | 市净率 | number |     |
| 134071 | 市销率 | number |     |
| 199427 | 股息率(TTM) | number |     |
| 3475914 | 流通市值 | number |     |
| 199590 | 52周最高 | number |     |
| 199591 | 52周最低 | number |     |
| 526792 | 振幅  | number |     |
| post\_market<br><br>(盘后) | 461256 | 委比  | number | 股票  |     |

### 2.响应

#### 1）响应格式

```
{
    "status_code": 0,
    "data": {
        //"data_class": "snapshot",
        "quote_data": [
            {
                "market": "具体市场",
                "code": "具体代码",
                "65558": 20221108,
                "65541": "intraday",
                "数据项的key": 数据项对应的value, //比如"65558": 20221108，"65555": "trading"
            }
        ]
    }
}
```

  

### 3\. 请求与响应示例

请求：

```
	{
		"code_list": [{
			"market": "185",
			"codes": ["TSLA"]
		}],
		"trade_class": "intraday",
		"data_fields": ["3541450", "2942"]
	}
```

响应：

```
{
	"data": {
		"data_class": "snapshot",
		"quote_data": [{
			"65535": 20221223,
			"65541": "intraday",
			"code": "TSLA",
			"market": "185",    
            "1": 1671829200000,
            "2942": 80.38,
            "3541450": 870912000000
            }]
	},
	"status_code": 0
}
```

## 最新交易日区间统计数据

请求到最新交易日为止，一段区间内的统计值，主要是涨跌幅、涨跌额

适用场景：个股、自选股、排名页面等列表类

约束：代码数 <= 100;  data\_fields <= 20；(data\_fields 计数包含 extend\_fields )

### 1 请求

#### 1.1 URL

/quote/v2/last\_stats

#### 1.2 请求参数

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   |  \[\] | "code\_list":\[ { "market":"UDC", "codes":\["BTCUSD"\] } \]              |
| stats\_fields | 是   | object \[\] | 计算类数据项，可以指定不同的周期，比如330204，在day\_1周期表示5日涨幅，在min\_1周期表示5分钟涨幅 |
| trade\_class | 否   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |

  

stats\_fields

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| data\_field | 是   | string | 需要的数据项id，比如五个周期的涨幅是330204 |
| time\_period | 是   | string | 如： "min\_1"  "min\_10"  "hour\_1" "day\_1"  "week\_1" "month\_1" "quarter\_1" "year\_1"，详见 [GMS-json基础字段定义#time\_period](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson%E5%9F%BA%E7%A1%80%E5%AD%97%E6%AE%B5%E5%AE%9A%E4%B9%89-time_period) |

  

stats\_fields 支持字段如下：

![](./attachments/image-2024-1-18_14-53-19.png)

### 2.响应

#### 1）响应格式

```
{
    "status_code": 0,
    "data": {
        "data_class": "snapshot",
        "quote_data": [
            {
                "market": "具体市场",
                "code": "具体代码",
                "65558": 20221108,
                "65541": "intraday",
                "stats_value": [
                    {
                        "time_period": "min_1",
                        "data_field": "330204",
                        "value": 1.5
                    },
                    {
                        "time_period": "day_1",
                        "data_field": "330204",
                        "value": 0.3
                    }
                ]
            }
        ]
    }
}
```

  

### 3\. 请求与响应示例

请求：

```
	{
		"code_list": [{
			"market": "185",
			"codes": ["TSLA"]
		}],
		"trade_class": "intraday",
		"stats_fields": [{"data_field":3250, "time_period":"min_5"}]
	}
```

响应：

```
{
	"data": {
		"data_class": "snapshot",
		"quote_data": [{
			"65535": 20221223,
			"65541": "intraday",
			"code": "TSLA",
			"market": "185",    
            "1": 1671829200000,
           "stats_value": [
                    {
                        "time_period": "min_5",
                        "data_field": "3250",
                        "value": 1.5
                    }]        
       }]
	},
	"status_code": 0
}
```

  

## 实时资金流向（大单）接口

### 1 请求

#### 1.1 URL

/quote/v2/real\_fund\_flow

#### 1.2 请求参数

约束：代码数 <= 100

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 二选一 |  \[\] | "code\_list":\[ { "market":"184", "codes":\["AAPL"\] } \]              |
| trade\_date | 是   | int | 详见 [基础字段定义-begin\_time、end\_time和trade\_date](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson%E5%9F%BA%E7%A1%80%E5%AD%97%E6%AE%B5%E5%AE%9A%E4%B9%89-begin_time%E3%80%81end_time%E5%92%8Ctrade_date)，目前只支持trade\_date = 0  |
| trade\_class | 否   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |

存量小市场兼容：对于存量市场，market可以使用小市场，比如169，185，返回的市场和用户传入的保持一致

#### 3）请求示例  

`{`  
    `"trade_date"``: ` `0``,`  
   `"trade_class"``: ` `"intraday"``,`  
    `"code_list"``:[`  
        `{ ` `"market"``:``"184"``, ` `"codes"``:[``"AAPL"``] }`  
     `]`  
`}`

  

### 2.响应

#### 1）响应格式

`{`  
    `"status_code"``: 0,`  
    `"data"``: {`  
         `"quote_data"``: [{ `   
            `"market"``: ` `"具体市场"``,`  
            `"code"``: ` `"具体代码"``,`  
            `"65558"``: 20221108,`  
            `"65541"``: ` `"intraday"``,`  
          `"data_fields"``: [``"数据项1"``, ` `"数据项2"``, ` `"数据项N"``],`  
            `"value"``: [`  
                `[``"数据项1的值"``, ` `"数据项2的值"``, ` `"数据项N的值"``]`  
            `]`  
        `}]  `    
    `}`  
`}`

data\_fields范围

| trade\_class | data\_id | 含义  | 类型  | 支持的市场 | 说明  |
| --- | --- | --- | --- | --- | --- |
| intraday | 1   | 时间  | number | UUS |     |
| 201 | 主动买入特大单量 | number |     |
| 202 | 主动卖出特大单量 | number |     |
| 203 | 主动买入大单量 | number |     |
| 204 | 主动卖出大单量 | number |     |
| 205 | 主动买入中单量 | number |     |
| 206 | 主动卖出中单量 | number |     |
| 207 | 被动买入特大单量 | number |     |
| 208 | 被动卖出特大单量 | number |     |
| 209 | 被动买入大单量 | number |     |
| 210 | 被动卖出大单量 | number |     |
| 211 | 被动买入中单量 | number |     |
| 212 | 被动卖出中单量 | number |     |
| 213 | 主动买入小单量 | number |     |
| 214 | 主动卖出小单量 | number |     |
| 215 | 主动买入特大单笔数 | number |     |
| 216 | 主动卖出特大单笔数 | number |     |
| 217 | 主动买入大单笔数 | number |     |
| 218 | 主动卖出大单笔数 | number |     |
| 219 | 被动买入特大单笔数 | number |     |
| 220 | 被动卖出特大单笔数 | number |     |
| 221 | 被动买入大单笔数 | number |     |
| 222 | 被动卖出大单笔数 | number |     |
| 223 | 主动买入特大单金额 | number |     |
| 224 | 主动卖出特大单金额 | number |     |
| 225 | 主动买入大单金额 | number |     |
| 226 | 主动卖出大单金额 | number |     |
| 227 | 被动买入特大单金额 | number |     |
| 228 | 被动卖出特大单金额 | number |     |
| 229 | 被动买入大单金额 | number |     |
| 230 | 被动卖出大单金额 | number |     |
| 259 | 主动买入中单金额 | number |     |
| 260 | 主动卖出中单金额 | number |     |
| 261 | 被动买入中单金额 | number |     |
| 262 | 被动卖出中单金额 | number |     |
| 250 | 委托买入前五档金额 | number |     |
| 237 | 主动买入小单金额 | number |     |
| 238 | 主动卖出小单金额 | number |     |

  

## 个股k线

### 1 请求

#### 1.1 URL

/quote/v2/single\_kline

#### 1.2 请求参数

约束：代码数 == 1

|     | 名称  | 必填  | 类型  | 默认值 | 备注  | 示例  | 其他信息 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1   | code\_list | 是   | 列表  |     | 代码列表 | `"code_list"``: [`  <br>    `{`  <br>      `"market"``: ` `"33"``,`  <br>      `"codes"``: [`  <br>        `"300033"`  <br>      `]`  <br>    `}`  <br>  `]` |     |
| 2   | trade\_class | 是   | 字符串 |     | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 | intraday | intraday |
| 3   | time\_period | 是   | 数值  |     | 时间周期 如： "min\_1" "min\_10" "hour\_1" "day\_1" "week\_1" "month\_1" "quarter\_1" "year\_1"，详见 GMS-json基础字段定义#time\_period |  day\_1 |     |
| 4   | time\_range | 是   | object |     | 组合1、组合2 |     |     |
| 5   | adjust\_type | 否   | 字符串 |     | 复权类型 forward:前复权,backward:后复权,,actual 不复权 | forward |     |
| 6   | ~need\_trade\_hours~ | ~否~ | ~bool~ |     | ~是否需要时间轴~ |     |     |

time\_range

可以使用其中一种组合取数据

| 时间组合 | 字段  | 是否必须 | 含义  | 说明  |
| --- | --- | --- | --- | --- |
| 组合1：某一个交易日 | trade\_date | 是   | YYMMDD  –  具体一个交易日<br><br>0  –  表示最新的交易日 |     |
| date\_offset | 否   | < 0 表示trade\_date往前的第N个交易日；<br><br>\> 0 表示trade\_date往后的第N个交易日；<br><br>0 默认值，表示trade\_date这个交易日 |
|     |     |     |     |     |
| 组合2：具体时间往前取总共N条 | count | 是   | \> 0 表示从end\_time（包括）往前总的数据条数 |     |
| end\_time | 是   | 精确到毫秒的时间戳<br><br>0表示最新 |     |
|     |     |     |     |     |
| 组合3：具体时间往前取总共N条 | begin\_time | 是   | 精确到毫秒的时间戳<br><br>0表示最早的时间 |     |
| count | 是   | \> 0 表示从begin\_time（包括）往后总的数据条数 |     |
|     |     |     |     |     |
| 组合4：\[开始时间，结束时间\] | begin\_time | 是   | 精确到毫秒的时间戳<br><br>0表示最早的时间 | begin\_time和end\_time不能同时为0 |
| end\_time | 是   | 精确到毫秒的时间戳<br><br>0表示最新 |

  

### 2 响应

#### 2.1  响应格式

**响应格式**

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "具体市场",
        "code": "具体代码",
        "base_price": 88.5, //trade_date >= 0时返回涨幅计算基点
      //"minute_window_type": "minute_window_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 [9:31:00, 9:31:59), post [9:30:00, 9:30:59)        
        "data_fields": [
          "数据项1",
          "数据项2",
          "数据项N"
        ],
        "value": [
          [
            "数据项1的值",
            "数据项2的值",
            "数据项N的值"
          ]
        ]
      }
    ]
  }
}  
```

#### 2.2  响应data\_fields

| data\_id | 含义  | 类型  | 支持的品种 |
| --- | --- | --- | --- |
| 1   | 时间  | number | ALL |
| 7   | 开盘价 | number | ALL |
| 8   | 最高价 | number | ALL |
| 9   | 最低价 | number | ALL |
| 11  | 收盘价 | number | ALL |
| 13  | 成交量 | number | ALL |
| 19  | 成交金额 | number | ALL |

### 3 请求与响应示例

#### 3.1 请求示例

```
{
  "code_list": [
    {
      "market": "33",
      "codes": [
        "300033"
      ]
    }
  ],
  "trade_class": "intraday",
  "time_period": "day_1",
  "time_range":{
    "count": 390,
      "end_time": 0,
  },
  "adjust_type": "forward"
}
```

#### 3.2 响应示例

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "33",
        "code": "300033",
        "data_fields": [
          "1",
          "7",
          "8",
          "9",
          "11",
          "13",
          "19"
        ],
        "value": [
          [
            1677196800000,
            121.5,
            121.9,
            119.45,
            119.85,
            3266894,
            392636340
          ],
          [
            1677456000000,
            118.51,
            120.48,
            117.2,
            118.84,
            4608100,
            545637910
          ],
          [
            1677542400000,
            119.8,
            120.26,
            116.92,
            118.5,
            5321235,
            631237760
          ],
          [
            1677628800000,
            118.6,
            126.55,
            117.01,
            125.11,
            8401612,
            1034549370
          ],
          [
            1677715200000,
            125.08,
            128.73,
            124.41,
            127.63,
            6131682,
            780882690
          ],
          [
            1677801600000,
            127.63,
            127.8,
            123.6,
            124.45,
            3606388,
            450847520
          ],
          [
            1678060800000,
            124.5,
            124.6,
            119,
            120.06,
            6495716,
            780508310
          ],
          [
            1678147200000,
            119.97,
            121.01,
            117.75,
            118.12,
            3398111,
            405546540
          ],
          [
            1678233600000,
            117.65,
            119.1,
            116.7,
            117.79,
            2898815,
            340784100
          ],
          [
            1678320000000,
            117.8,
            118.3,
            115.38,
            116.66,
            3268761,
            381301610
          ],
          [
            1678406400000,
            115.5,
            117.5,
            115.01,
            116.98,
            596428,
            69691867
          ]
        ]
      }
    ]
  },
  "status_msg": "ok"
}
```

## 多股k线

### 1 请求

#### 1.1 URL

/quote/v2/multi\_kline

#### 1.2 请求参数

约束：代码数 <= 16；一次最多取 2000条k线；trade\_date为-1时，begin\_time 和 end\_time 不能同时为0

|     | 名称  | 必填  | 类型  | 默认值 | 备注  | 示例  | 其他信息 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1   | code\_list | 是   | 列表  |     | 代码列表 | `"code_list"``: [`  <br>    `{`  <br>      `"market"``: ` `"33"``,`  <br>      `"codes"``: [`  <br>        `"300033"`  <br>      `]`  <br>    `}`  <br>  `]` |     |
| 2   | trade\_class | 是   | 字符串 |     | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 | intraday | intraday |
| 3   | time\_period | 是   | 数值  |     | 时间周期 如： "min\_1" "min\_10" "hour\_1" "day\_1" "week\_1" "month\_1" "quarter\_1" "year\_1"，详见 GMS-json基础字段定义#time\_period |  day\_1 |     |
| 4   | time\_range | 是   | object |     | 同single\_kline |     |     |
| 5   | adjust\_type | 否   | 字符串 |     | 复权类型 forward:前复权,backward:后复权,,actual 不复权 | forward |     |

  

### 2 响应

#### 2.1  响应格式

**响应格式**

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "具体市场",
        "code": "具体代码",
      "base_price": 88.5, //trade_date >= 0时返回涨幅计算基点
      "minute_window_type": "minute_window_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 [9:31:00, 9:31:59), post [9:30:00, 9:30:59)
      "data_fields": [
          "数据项1",
          "数据项2",
          "数据项N"
        ],
        "value": [
          [
            "数据项1的值",
            "数据项2的值",
            "数据项N的值"
          ]
        ]
      }
    ]
  }
}  
```

#### 2.2  响应data\_fields

| data\_id | 含义  | 类型  | 支持的品种 |
| --- | --- | --- | --- |
| 1   | 时间  | number | ALL |
| 7   | 开盘价 | number | ALL |
| 8   | 最高价 | number | ALL |
| 9   | 最低价 | number | ALL |
| 11  | 收盘价 | number | ALL |
| 13  | 成交量 | number | ALL |
| 19  | 成交金额 | number | ALL |

### 3 请求与响应示例

#### 3.1 请求示例

```
{
  "code_list": [
    {
      "market": "33",
      "codes": [
        "300033"
      ]
    }
  ],
  "trade_class": "intraday",
  "time_period": "day_1",
  "time_range":{
    "offset_count": 390,
      "end_time": 0,
  },
 "adjust_type": "forward"
}
```

#### 3.2 响应示例

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "33",
        "code": "300033",
        "data_fields": [
          "1",
          "7",
          "8",
          "9",
          "11",
          "13",
          "19"
        ],
        "value": [
          [
            1677196800000,
            121.5,
            121.9,
            119.45,
            119.85,
            3266894,
            392636340
          ],
          [
            1677456000000,
            118.51,
            120.48,
            117.2,
            118.84,
            4608100,
            545637910
          ],
          [
            1677542400000,
            119.8,
            120.26,
            116.92,
            118.5,
            5321235,
            631237760
          ],
          [
            1677628800000,
            118.6,
            126.55,
            117.01,
            125.11,
            8401612,
            1034549370
          ],
          [
            1677715200000,
            125.08,
            128.73,
            124.41,
            127.63,
            6131682,
            780882690
          ],
          [
            1677801600000,
            127.63,
            127.8,
            123.6,
            124.45,
            3606388,
            450847520
          ],
          [
            1678060800000,
            124.5,
            124.6,
            119,
            120.06,
            6495716,
            780508310
          ],
          [
            1678147200000,
            119.97,
            121.01,
            117.75,
            118.12,
            3398111,
            405546540
          ],
          [
            1678233600000,
            117.65,
            119.1,
            116.7,
            117.79,
            2898815,
            340784100
          ],
          [
            1678320000000,
            117.8,
            118.3,
            115.38,
            116.66,
            3268761,
            381301610
          ],
          [
            1678406400000,
            115.5,
            117.5,
            115.01,
            116.98,
            596428,
            69691867
          ]
        ]
      }
    ]
  },
  "status_msg": "ok"
}
```

## 个股多日分钟k线

### 1 请求

#### 1.1 URL

/quote/v2/single\_days\_kline

#### 1.2 请求参数

约束：代码数 == 1

|     | 名称  | 必填  | 类型  | 默认值 | 备注  | 示例  | 其他信息 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1   | code\_list | 是   | 列表  |     | 代码列表 | `"code_list"``: [`  <br>    `{`  <br>      `"market"``: ` `"33"``,`  <br>      `"codes"``: [`  <br>        `"300033"`  <br>      `]`  <br>    `}`  <br>  `]` |     |
| 2   | trade\_class | 是   | 字符串 |     | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 | intraday | intraday |
| 3   | time\_period | 是   | 数值  |     | 时间周期 如： "min\_1" "min\_10" "hour\_1" "day\_1" "week\_1" "month\_1" "quarter\_1" "year\_1"，详见 GMS-json基础字段定义#time\_period |  day\_1 |     |
| 4   | trade\_date | 是   | 数值  |     | YYMMDD  –  具体一个交易日<br><br>0  –  表示最新的交易日 |     |     |
| 5   | date\_count | 是   | 数值  |     | < 0 ，表示trade\_date（包括）往前总共N个交易日；<br><br>\> 0 表示trade\_date（包括）往后总共N个交易日； |     |     |
| 6   | adjust\_type | 是   | 字符串 |     | 复权类型 forward:前复权,backward:后复权,,actual 不复权 | forward |     |

  

### 2 响应

#### 2.1  响应格式

**响应格式**

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "具体市场",
        "code": "具体代码",
        "base_price": 88.5, //涨幅计算基点
      "minute_window_type": "minute_window_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 [9:31:00, 9:31:59), post [9:30:00, 9:30:59)        
        "data_fields": [
          "数据项1",
          "数据项2",
          "数据项N"
        ],
        "value": [
          [
            "数据项1的值",
            "数据项2的值",
            "数据项N的值"
          ]
        ]
      }
    ]
  }
}  
```

#### 2.2  响应data\_fields

| data\_id | 含义  | 类型  | 支持的品种 |
| --- | --- | --- | --- |
| 1   | 时间  | number | ALL |
| 7   | 开盘价 | number | ALL |
| 8   | 最高价 | number | ALL |
| 9   | 最低价 | number | ALL |
| 11  | 收盘价 | number | ALL |
| 13  | 成交量 | number | ALL |
| 19  | 成交金额 | number | ALL |

### 3 请求与响应示例

#### 3.1 请求示例

```
{
  "code_list": [
    {
      "market": "33",
      "codes": [
        "300033"
      ]
    }
  ],
  "trade_class": "intraday",
  "time_period": "day_1",
  "time_range":{
    "trade_date": 0,
      "date_count": 5,
  },
  "adjust_type": "forward"
}
```

#### 3.2 响应示例

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "33",
        "code": "300033",
        "data_fields": [
          "1",
          "7",
          "8",
          "9",
          "11",
          "13",
          "19"
        ],
        "value": [
          [
            1677196800000,
            121.5,
            121.9,
            119.45,
            119.85,
            3266894,
            392636340
          ],
          [
            1677456000000,
            118.51,
            120.48,
            117.2,
            118.84,
            4608100,
            545637910
          ],
          [
            1677542400000,
            119.8,
            120.26,
            116.92,
            118.5,
            5321235,
            631237760
          ],
          [
            1677628800000,
            118.6,
            126.55,
            117.01,
            125.11,
            8401612,
            1034549370
          ],
          [
            1677715200000,
            125.08,
            128.73,
            124.41,
            127.63,
            6131682,
            780882690
          ],
          [
            1677801600000,
            127.63,
            127.8,
            123.6,
            124.45,
            3606388,
            450847520
          ],
          [
            1678060800000,
            124.5,
            124.6,
            119,
            120.06,
            6495716,
            780508310
          ],
          [
            1678147200000,
            119.97,
            121.01,
            117.75,
            118.12,
            3398111,
            405546540
          ],
          [
            1678233600000,
            117.65,
            119.1,
            116.7,
            117.79,
            2898815,
            340784100
          ],
          [
            1678320000000,
            117.8,
            118.3,
            115.38,
            116.66,
            3268761,
            381301610
          ],
          [
            1678406400000,
            115.5,
            117.5,
            115.01,
            116.98,
            596428,
            69691867
          ]
        ]
      }
    ]
  },
  "status_msg": "ok"
}
```

## 个股逐笔成交

### 1 请求

#### 1.1 URL

/quote/v2/single\_tick

#### 1.2 请求参数

约束：代码数 == 1

  

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   | 列表  | `"code_list"``: [`  <br>    `{`  <br>      `"market"``: ` `"33"``,`  <br>      `"codes"``: [`  <br>        `"300033"`  <br>      `]`  <br>    `}`  <br>  `]` |
| trade\_class | 是   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |
| time\_range | 是   |     | 组合1 |

  

time\_range

可以使用其中一种组合取数据

| 时间组合 | 字段  | 是否必须 | 含义  | 说明  |
| --- | --- | --- | --- | --- |
| 组合1：具体时间往前取总共N条 | trade\_date | 是   | 0  –  表示最新的交易日，其他值暂不支持 | 当trade\_date >=0 的时候时间范围限制在这个交易日内:<br><br>end\_time = 0表示这个交易日的最后的时间点 |
| count | 是   | \> 0 表示从end\_time（包括）往前总的数据条数 | 如果ms时间内记录数超过count，则**全部返回**； |
| end\_time | 是   | 精确到毫秒的时间戳<br><br>0 – 表示最新 | 0时，返回值为**闭区间**；<br><br>非0时，返回值为**左开右闭区间** |
|     |     |     |     |     |

  

### 2 响应

#### 2.1  响应格式

**响应格式**

```
{
    "status_code": 0,
    "data": {
        "data_class": "tick",
        "quote_data": [{
             "market": "具体市场",
             "code": "具体代码",
             "time_zone": "America/New_York",
             "data_fields": ["数据项1", "数据项2", "数据项N"],
             "value": [
                ["数据项1的值", "数据项2的值", "数据项N的值"]
            ]
        }]
    }
}
```

#### 2.2  响应data\_fields

| field | 含义  | 类型  | 支持的品种 | 备注  |
| --- | --- | --- | --- | --- |
| 1   | 时间  | number | UUS |     |
| 10  | 最新价 | number | UUS |     |
| 12  | 交易方向 | string | UUS | enum(string)        "sell" "buy" "unknown" |
| 49  | 现手  | number | UUS |     |
| 65541 | 交易阶段 | string | UUS | [trade\_period](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson%E5%9F%BA%E7%A1%80%E5%AD%97%E6%AE%B5%E5%AE%9A%E4%B9%89-trade_period%EF%BC%88%E7%94%A8%E4%BA%8E%E6%8F%8F%E8%BF%B0%E6%95%B0%E6%8D%AE%EF%BC%89) |
| 65552 | 交易类型 | string | UUS | enum(string)         <br><br>"automatch"      自动对盘<br><br>"odd\_lot"            碎股成交<br><br>"next\_day"          隔日交易 |
| 65558 | 交易日 | number | UUS |     |

### 3 请求与响应示例

#### 3.1 请求示例

```
{
    "api": "single_tick",
    "req": {
        "code_list": [{
            "market": "184",
            "codes": ["TSLA"]
        }],
        "trade_class": "intraday",
      "time_range":{
           "trade_date":0
         "count": 20,
           "end_time": 0,
        }
   }
}
```

#### 3.2 响应示例

```
{
    "data": {
        "data_class": "tick",
        "quote_data": [{
            "code": "TSLA",
            "data_fields": ["1", "10", "12", "49", "65558", "65541"],
            "market": "184",
            "value": [
                [1671570004000, 137.8, "buy", 561, 20221220, "intraday"],
                [1671570004000, 137.8, "buy", 1, 20221220, "intraday"],
                [1671570004000, 137.8, "buy", 15, 20221220, "intraday"],
                [1671570004000, 137.8, "buy", 3, 20221220, "intraday"],
                [1671570004000, 137.8, "buy", 10, 20221220, "intraday"]
            ]
        }]
    },
    "status_code": 0
}
```

## ~个股分时（暂不提供）~

### 1 请求

#### 1.1 URL

/quote/v2/single\_trend

#### 1.2 请求参数

约束：代码数 == 1

|     | 名称  | 必填  | 类型  | 默认值 | 备注  | 示例  | 其他信息 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1   | code\_list | 是   | 列表  |     | 代码列表 | `"code_list"``: [`  <br>    `{`  <br>      `"market"``: ` `"33"``,`  <br>      `"codes"``: [`  <br>        `"300033"`  <br>      `]`  <br>    `}`  <br>  `]` |     |
| 2   | trade\_class | 是   | 字符串 |     | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 | intraday | intraday |
| 3   | time\_range | 是   | object |     | 同single\_kline |     |     |

  

### 2 响应

#### 2.1  响应格式

**响应格式**

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "具体市场",
        "code": "具体代码",
        "base_price": 88.5, //trade_date >= 0时返回涨幅计算基点
      "minute_window_type": "minute_window_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 [9:31:00, 9:31:59), post [9:30:00, 9:30:59)        
        "data_fields": [
          "数据项1",
          "数据项2",
          "数据项N"
        ],
        "value": [
          [
            "数据项1的值",
            "数据项2的值",
            "数据项N的值"
          ]
        ]
      }
    ]
  }
}  
```

  

#### 2.2  响应data\_fields

| data\_id | 含义  | 类型  | 支持的品种 |
| --- | --- | --- | --- |
| 1   | 时间  | number | ALL |
| 10  | 最新价 | number | ALL |
| 13  | 成交量 | number | ALL |
| 19  | 成交金额 | number | ALL |

### 3 请求与响应示例

#### 3.1 请求示例

```
{
  "code_list": [
    {
      "market": "33",
      "codes": [
        "300033"
      ]
    }
  ],
  "trade_class": "intraday",
  "time_range":{
    "count": 390,
      "end_time": 0
  },
}
```

#### 3.2 响应示例

```
{
  "status_code": 0,
  "data": {
    "quote_data": [
      {
        "market": "33",
        "code": "300033",
        "data_fields": [
          "1",
          "10",
          "13",
          "19"
        ],
        "value": [
          [
            1677196800000,
            119.85,
            3266894,
            392636340
          ],
          [
            1677456000000,
            118.84,
            4608100,
            545637910
          ],
          [
            1677542400000,
            118.5,
            5321235,
            631237760
          ],
          [
            1677628800000,
            125.11,
            8401612,
            1034549370
          ],
          [
            1677715200000,
            127.63,
            6131682,
            780882690
          ],
          [
            1677801600000,
            124.45,
            3606388,
            450847520
          ],
          [
            1678060800000,
            120.06,
            6495716,
            780508310
          ],
          [
            1678147200000,
            118.12,
            3398111,
            405546540
          ],
          [
            1678233600000,
            117.79,
            2898815,
            340784100
          ],
          [
            1678320000000,
            116.66,
            3268761,
            381301610
          ],
          [
            1678406400000,
            116.98,
            596428,
            69691867
          ]
        ]
      }
    ]
  },
  "status_msg": "ok"
}
```

  

## 代码状态

### 1 请求

#### 1.1 URL

/quote/v2/code\_status

#### 1.2 请求参数

约束：代码数 <= 100

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   | \[\] | "code\_list":\[{"market":"32","codes":\["300033"\]}\]              |

存量小市场兼容：对于存量市场，market可以使用小市场，比如169，185，返回的市场和用户传入的保持一致

#### 3）请求示例 

{"code\_list":\[{"market":"32","codes":\["300033"\]}\] }  

### 2.响应

| key | 类型  | 说明  |
| --- | --- | --- |
| trade\_time | uint64 | 当前市场级别的交易时间         |
| trade\_date | int | 当前市场级别的交易日 |
| trade\_status | string | [http://cf.myhexin.com/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade\_status（交易状态）](http://cf.myhexin.com/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_status（交易状态）) |
| market | string | 和请求市场一致 （大/小市场） |
| time\_zone | string | 时区  |
| trade\_period | string | [http://cf.myhexin.com/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade\_period（用于描述数据）](http://cf.myhexin.com/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_period（用于描述数据）) |

`{`  
    `"data"``: {`  
        `"codes_status"``: [{`  
            `"code"``: ` `"AAPL"``,`  
            `"market"``: ` `"184"``,`  
            `"trade_date"``: ` `20221223``,`  
            `"trade_period"``: ` `"post_market"``,`  
            `"trade_time"``:精确到毫秒的unix时间戳,`  
            `"time_zone"``:xxxx,`

                             "trade\_status":"trading"  
        `}, {`  
            `"code"``: ` `"TSLA"``,`  
            `"market"``: ` `"184"``,`  
            `"trade_date"``: ` `20221223``,`  
            `"trade_period"``: ` `"post_market"``,`  
            `"trade_time"``:精确到毫秒的unix时间戳,`  
            `"time_zone"``:xxxx,`

                             "trade\_status":"trading"  
    `}]`  
    `},`  
    `"status_code"``: ` `0`  
`}`

## ~代码时间轴 (暂不支持)~

### 1 请求

#### 1.1 URL

/quote/v2/trade\_hours

#### 1.2 请求参数

约束：代码数 <= 100

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   | \[\] | "code\_list":\[{"market":"32","codes":\["300033"\]}\]              |

存量小市场兼容：对于存量市场，market可以使用小市场，比如169，185，返回的市场和用户传入的保持一致

#### 3）请求示例 

{"code\_list":\[{"market":"168","codes":\["BABA"\]}\]   }

### 2.响应

{  
    "status\_code": 0,  
    "data": {  
        "trade\_hours\_group": \[{   
            "current\_trade\_hours": { //当前交易日  
                "minute\_window\_type": "minute\_window\_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 \[9:31:00, 9:31:59), post \[9:30:00, 9:30:59)  
                "code\_group\_id": "168\_0",  
                "time\_zone": "America/New\_York",  
                "trade\_date": 20221223,  
                "period\_hours": \[{  
                    "trade\_period": "intraday",  
                    "phase\_hours": \[{  
                        "begin\_time": 1671805800,  
                        "end\_time": 1671829200,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }, {  
                    "trade\_period": "post\_market",  
                    "phase\_hours": \[{  
                        "begin\_time": 1671829200,  
                        "end\_time": 1671843600,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }, {  
                    "trade\_period": "pre\_market",  
                    "phase\_hours": \[{  
                        "begin\_time": 1671786000,  
                        "end\_time": 1671805800,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }\]  
            },  
            "next\_trade\_hours": { //下一个交易日，开盘前一段时间才会有  
                "minute\_window\_type": "minute\_window\_pre", //pre 分钟k线时间窗口往前计算，即9:31分钟k线 \[9:31:00, 9:31:59), post \[9:30:00, 9:30:59)     "code\_group\_id": "168\_0",  
                "code\_group\_id": "168\_0",  
                "time\_zone": "America/New\_York",  
                "trade\_date": 20221228,  
                "period\_hours": \[{  
                    "trade\_period": "intraday",  
                    "phase\_hours": \[{  
                        "begin\_time": 1672237800,  
                        "end\_time": 1672261200,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }, {  
                    "trade\_period": "post\_market",  
                    "phase\_hours": \[{  
                        "begin\_time": 1672261200,  
                        "end\_time": 1672275600,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }, {  
                    "trade\_period": "pre\_market",  
                    "phase\_hours": \[{  
                        "begin\_time": 1672218000,  
                        "end\_time": 1672237800,  
                        "has\_kline": true, //表示这段时间是否包含在分时、k线中，比如沪深的开盘集合竞价是不包含在分时、k线中的  
                        "trade\_phase\_type": "trade\_phase\_continuous" //类型有 trade\_phase\_continuous -- 连续竞价， trade\_phase\_open\_auction -- 开盘竞价， trade\_close\_auction -- 收盘竞价  
                    }\]  
                }\]  
            }  
        }\]  
    }  
}

  

## ~代码基础信息~

### 1 请求

#### 1.1 URL

~/quote/v2/code\_info~

#### 1.2 请求参数

约束：代码数 <= 100

| key | 是否必选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 是   | \[\] | "code\_list":\[{"market":"32","codes":\["300033"\]}\]           |

#### 存量小市场兼容：对于存量市场，market可以使用小市场，比如169，185，返回的市场和用户传入的保持一致

#### 3)请求示例 

`{`  
    `"code_list"``: [{`  
        `"market"``: ` `"32"``,`  
        `"codes"``: [``"300033"``]`  
    `}, {`  
        `"market"``: ` `"16"``,`  
        `"codes"``: [``"600000"``]`  
    `}]`  
`}`

  
  

### 2.响应

  

`{`  
    `"status_code"``: 0,`  
    `"data"``:{`  
        `"codes_data"``[`  
            `{`  
                `"codes_info"``: [`  
                    `{`  
                        `"market"``: ` `"176"``, ` `//大市场`  
                        `"sub_market"``:``"177"``, ` `//小市场`  
                        `"code"``: ` `"HK0700"``,  `    
                        `"show_code"``:``"00700"``, ` `//显示代码`  
                        `"names"``:[`  
                            `{`  
                                `                                "lang":"en"`  
                                `"name"``:``""`  
                            `}`  
                           
                        `]`  
                  `},`  
                `]`  
            `}`  
        `]`  
    `}  `    
`}`

  

## 排序 

约束：sort\_count <= 100，appends的data\_fields总个数 < 20

### 1 请求

#### 1.1 URL

/quote/v2/sort

#### 1.2 请求参数

约束：代码数 <= 100

| key | 是否须选 | 类型  | 说明  |
| --- | --- | --- | --- |
| code\_list | 三选一 | \[\] | 支持用户传一批代码表进行排序，多市场多代码排序，比如code\_list:\[{"market": "185", "codes": \["AAPL","TSLA"\]},{"market": "169", "codes": \["BABA"\]}\] |
| block\_id | string | 支持板块排序，如 "C199" |
| map\_code | {}  | 使用指定market\_code的关联代码，格式为<br><br>"map\_code":{  <br>    "market\_code":{  <br>      "market":"185",  <br>      "code":"TSLA"  <br>    },  <br>    "map\_id":1  <br>}<br><br>map\_id含义<br><br>1 - 成份股，表示使用market\_code的成分股，比如指数成分股，基金持仓股 |
| trade\_class | 是   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |
| sort\_field | 是   | string | 具体的数据项，比如涨幅是199112 |
| time\_period | 否   | string | 如果是当天的快照指标（比如10，199112）不需要填<br><br>  <br><br>只有区间统计类的指标才需要，取值限制：<br><br>日线：最大区间不超过365，即day\_365合法，day\_366非法<br><br>分钟线：最大区间不超过60，即min\_60合法，min\_61非法<br><br>月线：最大区间不超过3，即month\_3合法，month\_4非法<br><br>年线：最大区间不超过5，即year\_5合法，year\_6非法 |
| sort\_direct | 是   | string | ascend 升序<br><br>descend 降序 |
| sort\_begin | 是   | number | 排序结果的开始位置，从头开始为0 |
| sort\_count | 是   | number | 返回排序数据的个数，从sort\_begin开始，<=100 |
| ~lang~ | ~否~ | ~string~ | ~仅影响字段55返回结果  ~ <br><br>~zh-hans 简体中文（默认）   en  英~ |
| appends | 否   | object \[\] | 排序的同时请求的数据项，见以下说明，里面data\_fields个数总和 < 20 |
|     |     |     |     |

存量小市场兼容：代码表里面market可以使用小市场，比如169，185，返回的市场和用户传入的保持一致；如果是板块排序，存量的市场返回使用小市场

#### appends

| key | 是否须选 | 类型  | 说明  |
| --- | --- | --- | --- |
| trade\_class | 是   | string | [交易阶段](http://172.20.200.191:8003/pages/viewpage.action?pageId=864226921#GMSjson基础字段定义-trade_class(用于请求))，比如pre\_market – 盘前, intraday – 盘中 |
| data\_field | 是   | string | 对于排序后的代码，获取的数据项id，比如最新价是10 |
| time\_period | 否   | string | 如果是当天的快照指标（比如10，199112）不需要填<br><br>只有区间统计类的指标才需要 |

sort\_field范围

| trade\_class | data\_id | 含义  | 类型  | 支持的品种 | 说明  |
| --- | --- | --- | --- | --- | --- |
| pre\_market<br><br>(盘前) | 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | ALL |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| intraday<br><br>(盘中) | 6   | 昨收  | number | ALL |     |
| 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | UUS | 数字货币不支持 |
| 14  | 外盘成交量 | number | UUS |     |
| 15  | 内盘成交量 | number | UUS |     |
| 18  | 成交次数 | number | UUS |     |
| 19  | 总成交额 | number | UUS |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| 461256 | 委比  | number | ALL |     |
| 1968584 | 换手率 | number | ALL |     |
| 199187 | TTM市盈率 | number | ALL |     |
| 2942 | 动态市盈率 | number | ALL |     |
| 2946 | 静态市盈率 | number | ALL |     |
| 3541450 | 市值  | number | ALL |     |
| 1771976 | 量比  | number | ALL |     |
| post\_market<br><br>(盘后) | 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | ALL |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| 461256 | 委比  | number | ALL |     |

  

data\_fields范围

| trade\_class | data\_id | 含义  | 类型  | 支持的品种 | 说明  |
| --- | --- | --- | --- | --- | --- |
| pre\_market<br><br>(盘前) | 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | ALL |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| intraday<br><br>(盘中) | 6   | 昨收  | number | ALL |     |
| 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | UUS | 数字货币不支持 |
| 14  | 外盘成交量 | number | UUS |     |
| 15  | 内盘成交量 | number | UUS |     |
| 18  | 成交次数 | number | UUS |     |
| 19  | 总成交额 | number | UUS |     |
| 55  | 名称  | string | ALL |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| 461256 | 委比  | number | ALL |     |
| 1968584 | 换手率 | number | ALL |     |
| 199187 | TTM市盈率 | number | ALL |     |
| 2942 | 动态市盈率 | number | ALL |     |
| 2946 | 静态市盈率 | number | ALL |     |
| 3541450 | 市值  | number | ALL |     |
| 1771976 | 量比  | number | ALL |     |
| 265387 | 涨幅  | number | ALL | 值是\*100的，必须要要和time\_period配合使用，比如 time\_period=min\_5表示5分钟涨幅 |
| 265388 | 涨跌  | number | ALL | 必须要要和time\_period配合使用，比如 time\_period=min\_5表示5分钟涨跌 |
| post\_market<br><br>(盘后) | 7   | 开盘价 | number | ALL |     |
| 8   | 最高价 | number | ALL |     |
| 9   | 最低价 | number | ALL |     |
| 10  | 最新价 | number | ALL |     |
| 13  | 总成交量 | number | ALL |     |
| 199112 | 涨跌幅 | number | ALL |     |
| 264648 | 涨跌额 | number | ALL |     |
| 461256 | 委比  | number | ALL |     |

#### 3)请求示例 

  

{  
    "map\_code": {  
        "market\_code": {  
            "market": "169",  
            "code": "SPY"  
                },  
        "map\_id": 1  
        },  
    "time\_period": "day\_5",  
    "sort\_direct": "ascend",  
    "sort\_field": "265387",  
    "sort\_begin": 0,  
    "sort\_count": 10,  
    "appends": \[  
                {  
            "data\_field": "55"  
                }  
        \],  
    "lang": "en\_us"  
}

### 2.响应

#### 1)响应格式

  

{  
    "data": {  
        "append\_data": \[  
                        {  
                "code": "LLY",  
                "market": "169",  
                "value": \[  
                                        {  
                        "55": "Eli Lilly",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        },  
                        {  
                "code": "AAPL",  
                "market": "185",  
                "value": \[  
                                        {  
                        "55": "Apple",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        },  
                        {  
                "code": "MSFT",  
                "market": "185",  
                "value": \[  
                                        {  
                        "55": "Microsoft",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        }  
                \],  
        "sort\_data": {  
            "65541": "intraday",  
            "orders": \[  
                                {  
                    "code": "LLY",  
                    "market": "169",  
                    "value": \-0.77060056  
                                },  
                                {  
                    "code": "MSFT",  
                    "market": "185",  
                    "value": 2.21128962  
                                },  
                                {  
                    "code": "AAPL",  
                    "market": "185",  
                    "value": 6.28982192  
                                }  
                        \],  
            "sort\_begin": 0,  
            "sort\_count": 3,  
            "sort\_field": "265387",  
            "sort\_total": 3  
                }  
        },  
    "status\_code": 0  
}

说明：

1\. append\_data内代码不要求升降序；如果请求没有appends，则响应消息没有append\_data；

2\. append\_data里的每个value都只包含一个data\_field，其中65541和time\_period是两个描述项：

| 字段  | 含义  | 是否必须 | 说明  |
| --- | --- | --- | --- |
| 65541 | 数据项的交易阶段（盘前、盘中、盘后） | 否   | 除了55，其他数据项都需要 |
| time\_period | 时间周期（比如5分钟、10日等） | 否   | 只有区间统计类的数据项需要，比如 |

    以下面的数据为例， 表示LIY这个代码，返回携带了三个数据项，分别是：1）盘前的最新价；2）盘中的5日涨幅；3）名称

```
	"code": "LLY",
	"market": "169",
	"value": [{
		"10": 630.88,
		"65541": "pre_market"
	}, {          
		"65541": "intraday",
		"265387": -0.77060056,
		"time_period": "day_5"
	}, {
		"55": "Eli Lilly"
	}]
```

### 请求与响应示例

  

请求

{  
    "map\_code": {  
        "market\_code": {  
            "market": "169",  
            "code": "SPY"  
                },  
        "map\_id": 1  
        },  
    "time\_period": "day\_5",  
    "sort\_direct": "ascend",  
    "sort\_field": "19",  
    "sort\_begin": 0,  
    "sort\_count": 10,  
    "appends": \[  
                {  
            "data\_field": "55"  
                },  
                {  
            "data\_field": "265387",  
            "time\_period": "day\_5"  
                },  
                {  
            "data\_field": "10",  
            "trade\_class": "pre\_market"  
                }  
        \],  
    "lang": "en\_us"  
}

响应

{  
    "data": {  
        "append\_data": \[  
                        {  
                "code": "LLY",  
                "market": "169",  
                "value": \[  
                                        {  
                        "10": 630.88,  
                        "65541": "pre\_market"  
                                        },  
                                        {  
                        "65541": "intraday",  
                        "265387": \-0.77060056,  
                        "time\_period": "day\_5"  
                                        },  
                                        {  
                        "55": "Eli Lilly",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        },  
                        {  
                "code": "AAPL",  
                "market": "185",  
                "value": \[  
                                        {  
                        "10": 193.89,  
                        "65541": "pre\_market"  
                                        },  
                                        {  
                        "65541": "intraday",  
                        "265387": 6.28982192,  
                        "time\_period": "day\_5"  
                                        },  
                                        {  
                        "55": "Apple",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        },  
                        {  
                "code": "MSFT",  
                "market": "185",  
                "value": \[  
                                        {  
                        "10": 396.51,  
                        "65541": "pre\_market"  
                                        },  
                                        {  
                        "65541": "intraday",  
                        "265387": 2.21128962,  
                        "time\_period": "day\_5"  
                                        },  
                                        {  
                        "55": "Microsoft",  
                        "65541": "intraday"  
                                        }  
                                \]  
                        }  
                \],  
        "sort\_data": {  
            "65541": "intraday",  
            "orders": \[  
                                {  
                    "code": "LLY",  
                    "market": "169",  
                    "value": 13189309  
                                },  
                                {  
                    "code": "MSFT",  
                    "market": "185",  
                    "value": 122500096  
                                },  
                                {  
                    "code": "AAPL",  
                    "market": "185",  
                    "value": 296715614  
                                }  
                        \],  
            "sort\_begin": 0,  
            "sort\_count": 3,  
            "sort\_field": "19",  
            "sort\_total": 3  
                }  
        },  
    "status\_code": 0  
}

  

# 三 附录

## 1 market\_privilege

| 市场  | level | 含义  |     |
| --- | --- | --- | --- |
| 168 | level0 | 延迟美股（纽交所） |     |
| leve10 | 实时美股（纽交所） |     |
| 184 | level0 | 延迟美股（纳斯达克） |     |
| level10 | 实时美股（纳斯达克） |     |
| UDC | level10 | 实时数字货币 |     |
|     |     |     |     |