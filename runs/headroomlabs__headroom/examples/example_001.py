from headroom import compress

result = compress(messages, model="gpt-4o")  # OpenAI or Anthropic message dicts
client.chat.completions.create(model="gpt-4o", messages=result.messages)
