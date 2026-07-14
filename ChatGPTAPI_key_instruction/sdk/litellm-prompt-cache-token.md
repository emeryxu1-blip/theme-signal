# LiteLlm Prompt Cache Token用量统计接口

# 统计指定时间段各模型的token用量

## 说明：

查询指定时间段各模型的token用量；（目前查询结果仅包含已开启prompt cache的模型调用，协议支持：v1/messages  v1/chat/completions 两种协议）

## 协议说明

### 调用地址：

[http://localhost:9219/admin/litellm/token-stats/summary](http://localhost:9219/admin/litellm/token-stats/summary)

### 请求头

| 字段名 | 字段值 |
| --- | --- |
| content-type | application/json |

### 请求体

| 字段名 | 数据类型 | 是否必填 | 示例  | 说明  |
| --- | --- | --- | --- | --- |
| ```<br>models<br>``` | List<String> | 否   | \["gpt-5.2", "kimi-k2.5"\] | 模型列表，如不传则表示查询所有 |
| ```<br>start_time<br>``` | String | 是   | 2026-05-01 00:00:00 | 开始时间点 |
| ```<br>end_time<br>``` | String | 是   | 2026-06-01 23:59:59 | 截止时间点 |
| ```<br>protocol<br>``` | String | 否   | v1/chat/completions | 协议类型,不传表示查询所有；目前支持：v1/chat/completions    v1/messages |
| stream | ```<br>Boolean<br>``` | 否   | true | 是否流式响应，不传表示查询所有 |
| ```<br>cache_hit<br>``` | ```<br>Boolean<br>``` | 否   | false | 是否命中prompt cache，不传表示查询所有 |

### 响应体

|     |     |     |     |
| --- | --- | --- | --- |
| 字段名 | 数据类型 | 示例  | 说明  |
| ```<br>items.model<br>``` | String | gpt-5.2 | 模型名称 |
| items.request\_count | long | ```<br>723<br>``` | 累计请求次数 |
| items.prompt\_tokens | long | ```<br>9667069<br>``` | 累计输入的token数 |
| items.completion\_tokens | long | ```<br>175122<br>``` | 累计输出的token数 |
| items.total\_tokens | long | ```<br>9842191<br>``` | 累计消耗的token数 |
| items.cached\_tokens | long | 0   | 累计命中缓存的token数(这里指的是引擎侧的cache, 而非prompt cache) |
| items.reasoning\_tokens | long | 0   | 思考累计消耗的token数 |
| items.audio\_tokens | long | 0   | 语音数据累计消耗的token数 |
| ```<br>start_time<br>``` | String | 2026-05-01 00:00:00 | 开始时间点 |
| ```<br>end_time<br>``` | String | 2026-06-01 23:59:59 | 截止时间点 |

## 示例

调用示例：

```
curl -i -X POST http://localhost:9219/admin/litellm/token-stats/summary \
-H "content-type: application/json" \
-d '{"start_time":"2026-05-01 00:00:00","end_time":"2026-06-01 23:59:59"}'
```

响应示例：

```
{
    "start_time": "2026-05-01 00:00:00",
    "end_time": "2026-06-01 23:59:59",
    "items": [
        {
            "model": "gpt-5.2",
            "request_count": 4,
            "prompt_tokens": 52,
            "completion_tokens": 158,
            "total_tokens": 210,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "audio_tokens": 0
        },
        {
            "model": "qwen3.6-plus",
            "request_count": 723,
            "prompt_tokens": 9667069,
            "completion_tokens": 175122,
            "total_tokens": 9842191,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "audio_tokens": 0
        }
    ]
}
```