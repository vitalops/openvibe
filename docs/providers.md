# Providers

openvibe uses [litellm](https://github.com/BerriAI/litellm) as its LLM backend, which provides a unified interface to 100+ providers. Any model supported by litellm works with openvibe.

## Supported providers

| Provider | `provider_id` | Example `model_id` |
|----------|--------------|-------------------|
| Anthropic | `anthropic` | `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5` |
| OpenAI | `openai` | `gpt-4o`, `gpt-4o-mini`, `o1`, `o3-mini` |
| Azure OpenAI | `azure` | `azure/my-deployment-name` |
| Google Gemini | `gemini` | `gemini/gemini-2.0-flash`, `gemini/gemini-1.5-pro` |
| Ollama (local) | `ollama` | `ollama/llama3.2`, `ollama/qwen2.5-coder`, `ollama/mistral` |
| AWS Bedrock | `bedrock` | `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Groq | `groq` | `groq/llama-3.1-70b-versatile`, `groq/mixtral-8x7b-32768` |
| Mistral | `mistral` | `mistral/mistral-large-latest` |
| Together AI | `together_ai` | `together_ai/meta-llama/Llama-3-70b-chat-hf` |
| Vertex AI | `vertex_ai` | `vertex_ai/gemini-pro` |

## Setting a provider

### Global config (`~/.config/openvibe/openvibe.json`)

```json
{
  "model": {
    "provider_id": "anthropic",
    "model_id": "claude-sonnet-4-6"
  },
  "provider": {
    "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"}
  }
}
```

### Project config (`openvibe.json`)

```json
{
  "model": {"provider_id": "openai", "model_id": "gpt-4o"},
  "provider": {
    "openai": {"api_key": "${OPENAI_API_KEY}"}
  }
}
```

### Per-agent model

```json
{
  "agent": {
    "build": {
      "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
    },
    "plan": {
      "model": {"provider_id": "openai", "model_id": "gpt-4o-mini"}
    }
  }
}
```

## Switching model live

```
/model                                       # show current model + providers
/model anthropic/claude-opus-4-6             # this session only
/model openai/gpt-4o --project              # save to openvibe.json
/model ollama/llama3.2 --global             # save to global config
```

## Provider-specific configuration

### Anthropic

```json
{
  "provider": {
    "anthropic": {
      "api_key": "${ANTHROPIC_API_KEY}"
    }
  }
}
```

### OpenAI

```json
{
  "provider": {
    "openai": {
      "api_key": "${OPENAI_API_KEY}"
    }
  }
}
```

### Azure OpenAI

```json
{
  "model": {"provider_id": "azure", "model_id": "azure/gpt-4o"},
  "provider": {
    "azure": {
      "api_key": "${AZURE_API_KEY}",
      "base_url": "https://my-instance.openai.azure.com",
      "api_version": "2024-02-01"
    }
  }
}
```

### Ollama (local models)

Start Ollama first: `ollama serve`

```json
{
  "model": {"provider_id": "ollama", "model_id": "ollama/qwen2.5-coder:32b"},
  "provider": {
    "ollama": {
      "base_url": "http://localhost:11434"
    }
  }
}
```

Pull a model: `ollama pull qwen2.5-coder:32b`

### Google Gemini

```json
{
  "model": {"provider_id": "gemini", "model_id": "gemini/gemini-2.0-flash"},
  "provider": {
    "gemini": {
      "api_key": "${GEMINI_API_KEY}"
    }
  }
}
```

### AWS Bedrock

```json
{
  "model": {
    "provider_id": "bedrock",
    "model_id": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
  }
}
```

Uses standard AWS credential chain (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`).

## Environment variables

Most providers read their key from a standard environment variable without needing explicit config:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | OpenAI |
| `AZURE_API_KEY` | Azure OpenAI |
| `GEMINI_API_KEY` | Google Gemini |
| `GROQ_API_KEY` | Groq |
| `MISTRAL_API_KEY` | Mistral |
| `TOGETHER_API_KEY` | Together AI |

## Programmatic model selection

```python
from openvibe import OpenVibe

with OpenVibe() as ov:
    session = ov.create_session()
    # Switch model mid-session
    session.update_session_config({
        "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
    })
```
