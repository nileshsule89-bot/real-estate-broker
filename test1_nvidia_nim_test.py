
import requests, base64

invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
stream = True


headers = {
  "Authorization": "Bearer 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",
  "Accept": "text/event-stream" if stream else "application/json"
}

payload = {
  "model": "mistralai/mistral-small-4-119b-2603",
  "reasoning_effort": "high",
  "messages": [{"role":"user","content":"2 BHK for rent in Thane under 25000"}],
  "max_tokens": 16384,
  "temperature": 0.10,
  "top_p": 1.00,
  "stream": stream
}



response = requests.post(invoke_url, headers=headers, json=payload)

if stream:
    for line in response.iter_lines():
        if line:
            print(line.decode("utf-8"))
else:
    print(response.json())
