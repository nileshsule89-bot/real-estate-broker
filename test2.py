import requests
import json

invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"

headers = {
    "Authorization": "Bearer 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",
    "Accept": "text/event-stream"
}

payload = {
    "model": "mistralai/mistral-small-4-119b-2603",
    "messages": [
        {
            "role": "user",
            "content": "2 BHK for rent in Thane under 25000"
        }
    ],
    "temperature": 0.3,
    "top_p": 0.9,
    "max_tokens": 1024,
    "stream": True
}

response = requests.post(
    invoke_url,
    headers=headers,
    json=payload,
    stream=True
)

for line in response.iter_lines():

    if not line:
        continue

    decoded = line.decode("utf-8")

    if decoded.startswith("data: "):

        data = decoded[6:]

        if data == "[DONE]":
            break

        try:
            chunk = json.loads(data)

            choices = chunk.get("choices", [])

            if not choices:
                continue

            delta = choices[0].get("delta", {})

            content = delta.get("content")

            if content:
                print(content, end="", flush=True)

        except Exception as e:
            print("Error:", e)