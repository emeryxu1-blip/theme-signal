#  协议说明



## 考虑点说明

1. 业务场景
   1. 全市场排序、跨市场品种排序的技术方案
   2. 造数按照DataStreamID维度提供
2. 术语字段明确，符合ai通用认识
3. 指标的参数命名规范
4. 指数的输出值选择是否放在param中



## 请求体

请求body的格式如下

```
{    "symbols": {        "type": "market",        "values": [{"market": "184"}, {"market": "168"}]    },    "data_ids": [        {            "id": "4294967288",            "attributes": {                "time_period": "1d",                "market_stage": "intraday",                "adjust_type": ""            }        },        {            "id": "4294967282",            "attributes": {                "time_period": "1d",                "market_stage": "intraday",                "adjust_type": ""            },            "params": [                {"param": "Conversion Line Length", "value": 9},                 {"param": "Base Line Length", "value": 26},                {"param": "Leading Span B Length", "value": 52},                {"param": "Lagging Span", "value": 26},                {"param": "indicator_component", "value": "Conversion Line"}            ]        }    ],    "order_by": {        "request_data_id_index": 0,        "side": "desc"    },    "offset": 0,    "limit": 32 }
```





### 字段说明

| 字段         | 类型   | 是否必须               | 格式定义                                                     | 说明                                                         |      |
| :----------- | :----- | :--------------------- | :----------------------------------------------------------- | :----------------------------------------------------------- | :--- |
| **symbols**  | struct | **必须**               | 市场{"type": "market","values": [{"market": "184"}, {"market": "168"}]}代码列表 {"type": "code", "values": [{"market": "185", "code": "AAPL"}]} | type：查询关系 values：symbol列表，symbol定义如下            |      |
| **data_ids** | struct | 必须                   | `         {            "id": "68317",// 数据字典分配的数据id            "attributes": {                "time_period": "1w",// 数据周期：1min，1h，1d...                "market_stage": "intraday",//数据阶段                "adjust_type": ""// 数据复权类型，当前默认向前复权            },            "params": [  // 数据参数，chart indicator指标有一些自定义参数，可以不存在                {"param": "Short-term", "value": 10},                 {"param": "Long-term", "value": 20},                 {"param": "indicator_component", "value": "DIFF"} // 汇聚指标，子指标统一的名字            ]        } ` | `time_period：1min，1h，1d，1w，1m，1q，1ymarket_stage：pre_market、post_market、intradayadjust_type：数据复权类型，当前默认向前复权，不需要填写` |      |
| `order_by`   | struct | **可选，三个同时存在** | `{   "request_data_id_index": 0,   "side": "desc" }`         | `request_data_id_index 请求data_ids数组序号`                 |      |
| **offset**   | int32  |                        | 起始索引号**后端数据索引从1开始编码****返回数据不包含给定索引号** |                                                              |      |
| **limit**    | int32  |                        | 返回数据条数负数为不返回任何数据                             |                                                              |      |

symbols中type字段说明



## 支持数据说明



```
# 依赖indicator的指标
# id 表示计算公式，是个复合指标，所有复合数据都入池
# time_period 表示支持的数据周期
# param 表示输入的参数
# desc 表示指标的描述
indicators:
  - id: 4294967288
    name: Awesome Oscillator
    desc: 动量震荡指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值AO
  - id: 4294967287
    name: Hull Moving Average
    desc: 船体移动平均线
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值HMA
      - params:
        - param: Length
          value: 9
      - params:
        - param: Length
          value: 20
      - params:
        - param: Length
          value: 200
  - id: 4294967284
    name: Ultimate Oscillator
    desc: 终极波动指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值HMA
    #使用chart指标的默认参数即可
      - params:
        # - param: Fast Length
        #   value: 7
        # - param: Middle Length
        #   value: 14
        # - param: Slow Length
        #   value: 28
      - params:
        - param: Fast Length
          value: 7
        - param: Middle Length
          value: 14
        - param: Slow Length
          value: 28
  - id: 4294967283
    name: Stochastic RSI
    desc: 随机相对强弱指数
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值K、D
      - params:
        # - param: K
        #   value: 3
        # - param: D
        #   value: 3
        # - param: RSI Length
        #   value: 14
        # - param: Stochastic Length
        #   value: 14
      - params:
        - param: K
          value: 3
        - param: D
          value: 3
        - param: RSI Length
          value: 14
        - param: Stochastic Length
          value: 14
  - id: 4294967282
    name: Ichimoku Cloud
    desc: 一目均衡表
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值Conversion Line、Base Line、Leading Span A、Leading Span B、Lagging Span
      - params:
        - param: Conversion Line Length
          value: 9
        - param: Base Line Length
          value: 26
        - param: Leading Span B Length
          value: 52
        - param: Lagging Span
          value: 26
      - params:
        - param: Conversion Line Length
          value: 20
        - param: Base Line Length
          value: 60
        - param: Leading Span B Length
          value: 120
        - param: Lagging Span
          value: 30
  # 20250827 增加
  - id: 4294967281
    name: Aroon
    desc: 阿隆指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值Aroon Up、Aroon Down
      - params:
        - param: Length
          value: 14
  - id: 4294967280
    name: Average Daily Range
    desc: 平均日内范围
    supported_periods:
      - 1d
    param_sets: #输出值ADR
      - params:
        - param: Length
          value: 14
  - id: 4294967279
    name: Average Daily Range %
    desc: 平均日内范围幅度
    supported_periods:
      - 1d
    param_sets: #输出值ADR%
      - params:
        - param: Length
          value: 14
  - id: 4294967293
    name: Average True Range
    desc: 平均真实波幅
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值ATR
      - params:
        - param: Length
          value: 14
  - id: 4294967278
    name: Average True Range %
    desc: 平均真实范围幅度
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值ATR%
      - params:
        - param: Length
          value: 14
  - id: 68320
    name: Bollinger Bands
    desc: 布林带
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值MID、UPPER、LOWER
      - params:
        - param: Standard Deviation
          value: 20
        - param: Width
          value: 2
      - params:
        - param: Standard Deviation
          value: 50
        - param: Width
          value: 2
  - id: 4294967277
    name: Bull Bear Power
    desc: 牛熊力
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值BBPower
      - params:
        - param: Length
          value: 13
  - id: 4294967276
    name: Chaikin Money Flow
    desc: 蔡金资金流向指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值CMF
      - params:
        - param: Length
          value: 20
  - id: 4294967275
    name: Commodity Channel Index
    desc: 商品通道指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值CCI、CCI MA
      - params:
        - param: Length
          value: 20
  - id: 4294967274
    name: Directional Movement Index
    desc: 动向指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值ADX、+DI、-DI
      - params:
        - param: DI Length
          value: 14
        - param: ADX Smoothing
          value: 14
  - id: 265380
    name: Donchian Channels
    desc: 唐安奇通道
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值UPPER、LOWER、MID
      - params:
        - param: N
          value: 20
  - id: 265381
    name: Exponential Moving Average
    desc: 指数移动平均
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值EMA
      - params:
        - param: N1
          value: 10
      - params:
        - param: N1
          value: 20
      - params:
        - param: N1
          value: 30
      - params:
        - param: N1
          value: 50
      - params:
        - param: N1
          value: 100
      - params:
        - param: N1
          value: 200
  - id: 4294967273
    name: Keltner Channels
    desc: 肯特纳通道
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值Upper、Basis、Lower
      - params:
        - param: Length
          value: 20
        - param: Multiplier
          value: 2
        - param: ATR Length
          value: 10
  - id: 4294967272
    name: Momentum
    desc: 动量指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值MOM
      - params:
        - param: Length
          value: 10
  - id: 4294967271
    name: Money Flow Index
    desc: 资金流量指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值MFI
      - params:
        - param: Length
          value: 14
  - id: 68317
    name: Moving Average Convergence Divergence
    desc: 移动平均收敛散度指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值DIFF、DEA、MACD
      - params:
        - param: Short-term
          value: 12
        - param: Long-term
          value: 26
        - param: M
          value: 9
  - id: 4294967270
    name: Parabolic SAR
    desc: 抛物线转向指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值ParabolicSAR
  - id: 4294967269
    name: Rate of Change
    desc: 变化率指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值ROC
      - params:
        - param: Length
          value: 9
  - id: 4294967268
    name: Relative Strength Index
    desc: 相对强弱指数
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值RSI、RSI-based MA
      - params:
        - param: Length
          value: 14
  - id: 265372
    name: Simple Moving Average
    desc: 简单移动平均线
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值MA
      - params:
        - param: N1
          value: 10
      - params:
        - param: N1
          value: 20
      - params:
        - param: N1
          value: 30
      - params:
        - param: N1
          value: 50
      - params:
        - param: N1
          value: 100
      - params:
        - param: N1
          value: 200
  - id: 4294967290
    name: Stochastic
    desc: 随机指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值%K、%D
      - params:
        - param: "%K Length"
          value: 5
        - param: "%K Smoothing"
          value: 3
        - param: "%D Smoothing"
          value: 3
      - params:
        - param: "%K Length"
          value: 6
        - param: "%K Smoothing"
          value: 3
        - param: "%D Smoothing"
          value: 3
      - params:
        - param: "%K Length"
          value: 8
        - param: "%K Smoothing"
          value: 3
        - param: "%D Smoothing"
          value: 3
      - params:
        - param: "%K Length"
          value: 14
        - param: "%K Smoothing"
          value: 1
        - param: "%D Smoothing"
          value: 3
      - params:
        - param: "%K Length"
          value: 14
        - param: "%K Smoothing"
          value: 3
        - param: "%D Smoothing"
          value: 3
  - id: 4294967292
    name: Volume Weighted Average Price
    desc: 成交量加权平均价（VWAP）
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值VWAP、UpperBand、LowerBand
      - params:
        - param: Bands Multiplier
          value: 1
  - id: 4294967267
    name: Volume Weighted Moving Average
    desc: 成交量加权移动平均线
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值VWMA
      - params:
        - param: Length
          value: 20
  - id: 68321
    name: Williams Percent Range
    desc: 威廉指标
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值WR
      - params:
        - param: N
          value: 14
  - id: 4294967266
    name: Pivot Points Camarilla
    desc: 卡玛利拉枢轴点
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值P、R1、S1、R2、S2、R3、S3
  - id: 4294967265
    name: Pivot Points Classic
    desc: 经典枢轴点
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值P、R1、S1、R2、S2、R3、S3
  - id: 4294967264
    name: Pivot Points DeM
    desc: 迪马克枢轴点
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值P、R1、S1
  - id: 4294967263
    name: Pivot Points Fibonacci
    desc: 斐波那契枢轴点
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值P、R1、S1、R2、S2、R3、S3
  - id: 4294967262
    name: Pivot Points Woodie
    desc: 伍迪枢轴点
    supported_periods:
      - 1d
      - 1w
      - 1m
    param_sets: #输出值P、R1、S1、R2、S2、R3、S3
```







## 返回体

1. 约束：返回的指标顺序 和 请求的指标顺序保持一致



# 示例



## 请求

```
curl -X POST http://localhost:80/quote-source/aggr/v1/nasdaq-nls-m0/latest/eod/daily/seq \ -H "Content-Type: application/json" \ -d '{    "symbols": {        "type": "market",        "values": [{"market": "184"}, {"market": "168"}]    },    "data_ids": [        {            "id": "4294967288",            "attributes": {                "time_period": "1d",                "market_stage": "intraday",                "adjust_type": ""            },            "params": [                {"param": "indicator_component", "value": "AO"}            ]        },        {            "id": "4294967282",            "attributes": {                "time_period": "1d",                "market_stage": "intraday",                "adjust_type": ""            },            "params": [                {"param": "Conversion Line Length", "value": 9},                {"param": "Base Line Length", "value": 26},                {"param": "Leading Span B Length", "value": 52},                {"param": "Lagging Span", "value": 26},                {"param": "indicator_component", "value": "Conversion Line"}            ]        }    ],    "order_by": {        "data_id": {            "id": "4294967288",            "attributes": {                "time_period": "1d",                "market_stage": "intraday",                "adjust_type": ""            },            "params": [                {"param": "indicator_component", "value": "AO"}            ]        },        "side": "desc"    },    "offset": 0,    "limit": 32 }'
```



## 返回

```
{    "status_code": 0,    "status_msg": "success",    "data": {        "total_count": 11526,        "end": 32,        "count": 32,        "data_ids": [            {                "id": "market"            },            {                "id": "code"            },            {                "id": "4294967288",                "attributes": {                    "time_period": "1d",                    "market_stage": "intraday",                    "adjust_type": ""                },                "params": []            },            {                "id": "4294967282",                "attributes": {                    "time_period": "1d",                    "market_stage": "intraday",                    "adjust_type": ""                },                "params": [                    {                        "param": "Conversion Line Length",                        "value": 9                    },                    {                        "param": "Base Line Length",                        "value": 26                    },                    {                        "param": "Leading Span B Length",                        "value": 52                    },                    {                        "param": "Lagging Span",                        "value": 26                    },                    {                        "param": "indicator_component",                        "value": "Conversion Line"                    }                ]            }        ],        "seq": [            [                "171",                "MAGX",                null,                44.48025            ],            [                "169",                "CGRO",                null,                27.273            ],            [                "169",                "MSIF",                null,                16.44            ],            [                "171",                "MUNY",                null,                99.905            ],            [                "186",                "TELO",                null,                2.115            ],            [                "169",                "PALL",                null,                112.66            ],            [                "186",                "CELC",                null,                13.932500000000001            ],            [                "186",                "LXRX",                null,                1.24            ],            [                "185",                "ESLT",                null,                440.41999999999996            ],            [                "171",                "UXJA",                null,                31.4766            ],            [                "186",                "THCH",                null,                2.885            ],            [                "185",                "OSUR",                null,                3.415            ],            [                "171",                "ITB",                null,                97.89495            ],            [                "171",                "VXX",                null,                45.129999999999995            ],            [                "186",                "SSII",                null,                6.805            ],            [                "169",                "KOP",                null,                33.285            ],            [                "169",                "AVGE",                null,                78.84            ],            [                "185",                "OFLX",                null,                34.03            ],            [                "169",                "HAPY",                null,                24.02            ],            [                "185",                "BBIO",                null,                46.977050000000006            ],            [                "169",                "BF.A",                null,                29.377499999999998            ],            [                "185",                "VTRS",                null,                9.127500000000001            ],            [                "185",                "PCH",                null,                41.135000000000005            ],            [                "185",                "SNWV",                null,                36.055            ],            [                "186",                "INVE",                null,                3.56            ],            [                "171",                "MOTG",                null,                43.18255            ],            [                "169",                "HUBS",                null,                546.165            ],            [                "185",                "RUSC",                null,                28.20125            ],            [                "185",                "AXTI",                null,                2.3396            ],            [                "169",                "TAN",                null,                38.975            ],            [                "169",                "GNLpD",                null,                22.913899999999998            ],            [                "185",                "NAMM",                null,                7.51515            ]        ]    } }
```