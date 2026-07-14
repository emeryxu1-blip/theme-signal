# 外部模型调用方式（支持官方SDK调用）

注意：

1、请求头中的X-Trace-Id字段为本次请求的唯一标识，非必须字段。如客户端不传此字段，模型适配器会自动生成一个X-Trace-Id并在响应头中返回

2、**请求头中的Authorization需要替换成真实的值，可以找雷项阳申请；办公网段(含测试环境)、五常生产、海外生产 为三套环境，不同环境访问需单独找雷项阳申请授权**

**3、为避免超时导致的频繁失败（如返回504），请尽量将客户端和中间代理层的响应超时时间调整到600s**

# 海外生产环境

## 纽约七(euny7aiv)集群调用

### 查询模型列表

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://iwc-aime-model:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

  

### 推理请求 - chat/completions协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/chat/completions

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

  

### **tts文本转语音接口调用- v1/[audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)协议**

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)[v1/audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/audio/speech \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{
    "model": "tts-1",
    "input": "Hola, bienvenido/a a Dreamface. Esperamos que tengas un día maravilloso.",
    "voice": "shimmer",
    "response_format": "mp3"
}'
```

  

### 推理请求 - 视频生成

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:predictLongRunning

```
  

```

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:predictLongRunning \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxxx" \
-d '{"instances":[{"prompt":"Panning wide shot of a calico kitten sleeping in the sunshine"}],"parameters":{"aspectRatio":"16:9","personGeneration":"dont_allow"}}'
```

输出示例：

```
{
    "name": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"
}
```

官方接口协议文档：[https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)

  

### 推理请求 - 视频获取

调用地址：[http://iwc-aime-model:9219/litellm](http://iwc-aime-model:9219/litellm/*)/vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:fetchPredictOperation

```
  

```

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:fetchPredictOperation \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"operationName": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"}'
```

输出示例：

```
{
    "name": "projects/test-claude-01/locations/us-central1/publishers/google/models/veo-2.0-generate-001/operations/49daf4e0-df0b-4bad-8ba1-3c1b17493000",
    "done": true,
    "response": {
        "@type": "type.googleapis.com/cloud.ai.large_models.vision.GenerateVideoResponse",
        "raiMediaFilteredCount": 0,
        "videos": [
            {
                "bytesBase64Encoded": "AAAAIGZ0e......",
                "mimeType": "video/mp4"
            }
        ]
    }
}
```

  

### 推理请求-图片生成

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

  

## 弗吉尼亚(csva2df)集群

### 查询模型列表

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://iwc-aime-model:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

  

### 推理请求 - chat/completions协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/chat/completions

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

  

### **tts文本转语音接口调用- v1/[audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)协议**

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)[v1/audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/audio/speech \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{
    "model": "tts-1",
    "input": "Hola, bienvenido/a a Dreamface. Esperamos que tengas un día maravilloso.",
    "voice": "shimmer",
    "response_format": "mp3"
}'
```

  

### 推理请求 - 视频生成

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:predictLongRunning

```
  

```

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:predictLongRunning \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxxx" \
-d '{"instances":[{"prompt":"Panning wide shot of a calico kitten sleeping in the sunshine"}],"parameters":{"aspectRatio":"16:9","personGeneration":"dont_allow"}}'
```

输出示例：

```
{
    "name": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"
}
```

官方接口协议文档：[https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)

  

### 推理请求 - 视频获取

调用地址：[http://iwc-aime-model:9219/litellm](http://iwc-aime-model:9219/litellm/*)/vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:fetchPredictOperation

```
  

```

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:fetchPredictOperation \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"operationName": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"}'
```

输出示例：

```
{
    "name": "projects/test-claude-01/locations/us-central1/publishers/google/models/veo-2.0-generate-001/operations/49daf4e0-df0b-4bad-8ba1-3c1b17493000",
    "done": true,
    "response": {
        "@type": "type.googleapis.com/cloud.ai.large_models.vision.GenerateVideoResponse",
        "raiMediaFilteredCount": 0,
        "videos": [
            {
                "bytesBase64Encoded": "AAAAIGZ0e......",
                "mimeType": "video/mp4"
            }
        ]
    }
}
```

### 推理请求-图片生成

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

  

## 阿里云集群

### 查询模型列表

调用地址：[http://10.217.216.77:9219/litellm/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://10.217.216.77:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

  

### 推理请求 - chat/completions协议

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77[:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/chat/completions

```
  

```

调用示例：

```
curl -X POST http://10.217.216.77:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77[:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

```
  

```

调用示例：

```
curl http://10.217.216.77:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

  

### **tts文本转语音接口调用- v1/[audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)协议**

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77[:9219/litellm/](http://iwc-aime-model:9219/litellm/*)[v1/audio/speech](http://arsenal-openai.myhexin.com/vtuber/ai_access/openai/v1/audio/speech)

```
  

```

调用示例：

```
curl http://10.217.216.77:9219/litellm/v1/audio/speech \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{
    "model": "tts-1",
    "input": "Hola, bienvenido/a a Dreamface. Esperamos que tengas un día maravilloso.",
    "voice": "shimmer",
    "response_format": "mp3"
}'
```

  

### 推理请求 - 视频生成

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77[:9219/litellm/](http://iwc-aime-model:9219/litellm/*)vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:predictLongRunning

```
  

```
```
  

```

调用示例：

```
curl -X POST http://10.217.216.77:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:predictLongRunning \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxxx" \
-d '{"instances":[{"prompt":"Panning wide shot of a calico kitten sleeping in the sunshine"}],"parameters":{"aspectRatio":"16:9","personGeneration":"dont_allow"}}'
```

输出示例：

```
{
    "name": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"
}
```

官方接口协议文档：[https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)

  

### 推理请求 - 视频获取

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77[:9219/litellm](http://iwc-aime-model:9219/litellm/*)/vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:fetchPredictOperation

```
  

```
```
  

```

调用示例：

```
curl -X POST http://10.217.216.77:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:fetchPredictOperation \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"operationName": "projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/a1d0fe0c-2c60-4fe9-a251-970f970e3edf"}'
```

输出示例：

```
{
    "name": "projects/test-claude-01/locations/us-central1/publishers/google/models/veo-2.0-generate-001/operations/49daf4e0-df0b-4bad-8ba1-3c1b17493000",
    "done": true,
    "response": {
        "@type": "type.googleapis.com/cloud.ai.large_models.vision.GenerateVideoResponse",
        "raiMediaFilteredCount": 0,
        "videos": [
            {
                "bytesBase64Encoded": "AAAAIGZ0e......",
                "mimeType": "video/mp4"
            }
        ]
    }
}
```

### 推理请求-图片生成

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.216.77:9219[/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

```
  

```

`调用示例：`

```
curl -X POST http://10.217.216.77:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

  

## 其他集群调用

注意：**阿里云集群不支持**

已支持的集群：csva2aiv 、euny7aiv、 beimeipri、  aws

```
  

```

### 查询模型列表

调用地址：[https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/](https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/chat/v2/stream_model)litellm/models

调用示例：

```
curl -i https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

  

### 推理请求 - chat/completions协议

调用地址：[https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/](https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/chat/v2/stream_model)litellm/v1/chat/completions

调用示例：

```
curl -X POST https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：

```
https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/litellm/v1/messages
```

调用示例：

```
curl https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

  

# 国内

## 乌兰察布训练集群调用

### 查询模型列表

调用地址：[http://](http://122.224.107.233:880/litellm/)10.217.132.111:9219[/litellm/](http://122.224.107.233:880/litellm/)models

调用示例：

```
  

```
```
  

```

```
curl -i http://10.217.132.111:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

### 推理请求 - chat/completions协议

调用地址：[http://10.217.132.111:9219/litellm/v1/chat/completions](http://122.224.107.233:880/litellm/v1/chat/completions)

```
  

```

```
curl --connect-timeout 5 --max-time 60 -X POST http://10.217.132.111:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhangsan' \
-H 'Authorization: Bearer sk-xxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}'
```

### 推理请求 - v1/messages协议

调用地址：

```
[http://10.217.132.111:9219](http://122.224.107.233:880/litellm/v1/chat/completions)/litellm/v1/messages
```

调用示例：

```
curl http://10.217.132.111:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

### 推理请求-图片生成

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.132.111:9219[/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

```
  

```

`调用示例：`

```
curl -X POST http://10.217.132.111:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

### 推理请求-图片编辑

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)10.217.132.111:9219[/litell](http://iwc-aime-model:9219/litellm/*)m/v1/images/edits

```
  

```
```
  

```

`调用示例：`

```
curl -X POST "http://10.217.132.111:9219/litellm/v1/images/edits" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -H 'X-Trace-Id: hutingcong' \
  -F "model=gemini-3-pro-image" \
  -F "image[]=@test.jpeg" \
  -F 'prompt=Create a lovely gift basket with these four items in it'
```

```
  

```

输出示例

```
{
    "created": 1782805106,
    "background": null,
    "data": [
        {
            "b64_json": ".....",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": null,
    "quality": null,
    "size": null,
    "usage": {
        "total_tokens": 0,
        "input_tokens": 0,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 0
        },
        "output_tokens": 0,
        "model_name": "gemini-3-pro-image"
    }
}
```

  

  

## 乌兰察布推理集群调用

### 查询模型列表

调用地址：[http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm](http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/*)[/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

### 推理请求 - chat/completions协议

调用地址：[http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/](http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/*)v1/chat/completions

调用示例：

```
  

```

```
curl -X POST http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxx' \
-d '{"model":"gpt-5.2","stream":false,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}'
```

### 推理请求 - v1/messages协议

调用地址：http://aime-llm-service-apisix.hxapisix/iwc-aime-model[/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

```
  

```

调用示例：

```
curl http://aime-llm-service-apisix.hxapisix/iwc-aime-model/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

### 推理请求-图片生成

调用地址：[http://aime-llm-service-apisix.hxapisix/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST http://aime-llm-service-apisix.hxapisix/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

### 推理请求-图片编辑

调用地址：[http://aime-llm-service-apisix.hxapisix/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST "http://aime-llm-service-apisix.hxapisix/litellm/v1/images/edits" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -H 'X-Trace-Id: hutingcong' \
  -F "model=gemini-3-pro-image" \
  -F "image[]=@test.jpeg" \
  -F 'prompt=Create a lovely gift basket with these four items in it'
```

```
  

```

输出示例

```
{
    "created": 1782805106,
    "background": null,
    "data": [
        {
            "b64_json": ".....",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": null,
    "quality": null,
    "size": null,
    "usage": {
        "total_tokens": 0,
        "input_tokens": 0,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 0
        },
        "output_tokens": 0,
        "model_name": "gemini-3-pro-image"
    }
}
```

  

## 五常PROD集群调用

### 查询模型列表

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://iwc-aime-model:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

### 推理请求  - v1/chat/completions协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/chat/completions

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

### 推理请求-图片生成

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

### 推理请求-图片编辑

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/v1/images/edits

```
  

```

`调用示例：`

```
curl -X POST "http://iwc-aime-model:9219/litellm/v1/images/edits" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -H 'X-Trace-Id: hutingcong' \
  -F "model=gemini-3-pro-image" \
  -F "image[]=@test.jpeg" \
  -F 'prompt=Create a lovely gift basket with these four items in it'
```

```
  

```

输出示例

```
{
    "created": 1782805106,
    "background": null,
    "data": [
        {
            "b64_json": ".....",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": null,
    "quality": null,
    "size": null,
    "usage": {
        "total_tokens": 0,
        "input_tokens": 0,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 0
        },
        "output_tokens": 0,
        "model_name": "gemini-3-pro-image"
    }
}
```

### 推理请求 - gemini内容生成

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)iwc-aime-model[:9219/litellm](http://iwc-aime-model:9219/litellm/*)/vertex\_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/${模型名称}:generate\_content

```
  

```
```
  

```

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/vertex_ai/v1/projects/vertex-0511/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001:generate_content \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model": "gemini-3.1-pro-preview", "contents": [{"role": "user","parts": [{"text": "马云是谁"}]}]}'
```

  

## 测试集群调用

### 查询模型列表

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)models

调用示例：

```
curl -i http://iwc-aime-model:9219/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

### 推理请求 - chat/completions协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/chat/completions

调用示例：

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhansan' \
-H 'Authorization: Bearer sk-xxxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}' 
```

流式输出示例:

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：[http://iwc-aime-model:9219/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages

调用示例：

```
curl http://iwc-aime-model:9219/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

### 推理请求-图片生成

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

`调用示例：`

```
curl -X POST http://iwc-aime-model:9219/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

### 推理请求-图片编辑

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/v1/images/edits

```
  

```

`调用示例：`

```
curl -X POST "http://iwc-aime-model:9219/litellm/v1/images/edits" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -H 'X-Trace-Id: hutingcong' \
  -F "model=gemini-3-pro-image" \
  -F "image[]=@test.jpeg" \
  -F 'prompt=Create a lovely gift basket with these four items in it'
```

```
  

```

输出示例

```
{
    "created": 1782805106,
    "background": null,
    "data": [
        {
            "b64_json": ".....",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": null,
    "quality": null,
    "size": null,
    "usage": {
        "total_tokens": 0,
        "input_tokens": 0,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 0
        },
        "output_tokens": 0,
        "model_name": "gemini-3-pro-image"
    }
}
```

  

## 办公网段(含wifi环境)调用

### 查询模型列表

调用地址：[https://](http://172.20.210.183/litellm/v1/chat/completions)[aimemodeldev.myhexin.com](http://aimemodeldev.myhexin.com)[/litellm/](http://172.20.210.183/litellm/v1/chat/completions)models

调用示例：

```
curl -i https://aimemodeldev.myhexin.com/litellm/models -H "Authorization: Bearer sk-xxxxx"
```

输出示例

```
{
  "data": [
    {
      "id": "gpt-5.2",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    },
    {
      "id": "gpt-5.4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai"
    }
  ],
  "object": "list"
}
```

### 推理请求 - chat/completions协议

注意：如果是在claude code中使用，Anthropic base URL 里面填写的值应该是[https://](http://172.20.210.183/litellm/v1/chat/completions)[aimemodeldev.myhexin.com](http://aimemodeldev.myhexin.com)[/litellm](http://172.20.210.183/litellm/v1/chat/completions)

调用地址  ：[https://](http://172.20.210.183/litellm/v1/chat/completions)aimemodeldev.myhexin.com[/litellm/](http://172.20.210.183/litellm/v1/chat/completions)v1/chat/completions

```
调用示例：
```
```
  

```

```
curl --connect-timeout 5 --max-time 60 -X POST https://aimemodeldev.myhexin.com/litellm/v1/chat/completions \
-H 'Content-Type: application/json' \
-H 'X-Trace-Id: zhangsan' \
-H 'Authorization: Bearer sk-xxxxx' \
-d '{"model":"gpt-5.2","stream":true,"messages":[{"role":"developer","content":"You are a helpful assistant."},{"role":"user","content":"Hello!"}]}'
```

  

流式输出示例：

```
data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],"obfuscation":"ZcHkhe8S4Ff"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"}}],"obfuscation":"om8sP2rTz6fsUIl"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" How"}}],"obfuscation":"kJe6dedGVth6"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" can"}}],"obfuscation":"z7tgzPVJ2TCI"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" I"}}],"obfuscation":"8xkFdbzpxZzXSU"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" help"}}],"obfuscation":"F0E3a9MA2mZ"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" you"}}],"obfuscation":"4epczF7moXHL"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" today"}}],"obfuscation":"5qNZJNrU6L"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"?"}}],"obfuscation":"AxsJsyAcz4r9LkA"}

data: {"id":"chatcmpl-DPIQxMQ9E8qaytDJ0VQur00mfRZV8","created":1774921432,"model":"gpt-5.2","object":"chat.completion.chunk","choices":[{"finish_reason":"stop","index":0,"delta":{}}]}

data: [DONE] 
```

### 推理请求 - v1/messages协议

调用地址：

```
https://aimemodeldev.myhexin.com[/litellm/](http://iwc-aime-model:9219/litellm/*)v1/messages
```

调用示例：

```
curl https://aimemodeldev.myhexin.com/litellm/v1/messages \
-H "Authorization: Bearer sk-xxxxx" \
-H "Content-Type: application/json" \
-d '{"model":"claude-sonnet-4-6","max_tokens":100,"messages":[{"role":"user","content":"hello, reply in one sentence"}]}'
```

非流式输出示例:

```
{
    "model": "claude-sonnet-4-6",
    "id": "msg_01SrHUp4AW4obmTR1cm9DKe7",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": "Hello! I'm Claude, ready to help you with whatever you need today."
        }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "stop_details": null,
    "usage": {
        "input_tokens": 28,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0
        },
        "output_tokens": 19,
        "service_tier": "standard",
        "inference_geo": "not_available",
        "speed": "standard",
        "total_tokens": 47
    },
    "context_management": {
        "applied_edits": []
    }
}
```

### 推理请求-图片生成

调用地址：[http://](http://iwc-aime-model:9219/litellm/*)aimemodeldev.myhexin.com[/litell](http://iwc-aime-model:9219/litellm/*)m/`v1/images/generations`

```
  

```

`调用示例：`

```
curl -X POST http://aimemodeldev.myhexin.com/litellm/v1/images/generations \
-H "Content-Type:application/json" \
-H "Authorization:Bearer xxx" \
-d '{"model":"gpt-image-2","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}'
```

```
  

```

输出示例

```
{
    "created": 1782800267,
    "background": null,
    "data": [
        {
            "b64_json": "iVBORw0KGgkaGF.........",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": "png",
    "quality": "high",
    "size": "1024x1024",
    "usage": {
        "total_tokens": 208,
        "input_tokens": 12,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 12
        },
        "output_tokens": 196,
        "output_tokens_details": {
            "image_tokens": 196,
            "text_tokens": 0
        }
    }
}
```

  

### 推理请求-图片编辑

调用地址：[http://iwc-aime-model:9219/litell](http://iwc-aime-model:9219/litellm/*)m/v1/images/edits

```
  

```

`调用示例：`

```
curl -X POST "http://ceshiai.iwencai.com/litellm/v1/images/edits" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -H 'X-Trace-Id: hutingcong' \
  -F "model=gemini-3-pro-image" \
  -F "image[]=@test.jpeg" \
  -F 'prompt=Create a lovely gift basket with these four items in it'
```

```
  

```

输出示例

```
{
    "created": 1782805106,
    "background": null,
    "data": [
        {
            "b64_json": ".....",
            "revised_prompt": null,
            "url": null
        }
    ],
    "output_format": null,
    "quality": null,
    "size": null,
    "usage": {
        "total_tokens": 0,
        "input_tokens": 0,
        "input_tokens_details": {
            "image_tokens": 0,
            "text_tokens": 0
        },
        "output_tokens": 0,
        "model_name": "gemini-3-pro-image"
    }
}
```

  

# 如何申请配额

针对外部模型的调用，需通过先通过工单的方式申请配额。申请方式请参考下述文档：

[工单接入帮助文档#2办公网（office）-----团队和个人额度申请](http://cf.myhexin.com/pages/viewpage.action?pageId=1507841338)

  

# Harness如何接入

[Claude Code CLI](http://cf.myhexin.com/spaces/AIE8/pages/1522533777/1.+Claude+Code+CLI)

[OpenCode](http://cf.myhexin.com/spaces/AIE8/pages/1522537469/3.+Open+Code)

如需要支持其他请联系zhangfan6@myhexin.com