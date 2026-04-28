import httpx
from uuid import uuid4 
API_KEY = "OjdlYzhlM2QyLWY3MjAtNDdiNi1iNmU4LTM3YTNhODllMWI4OTo="
URL = "https://llm-gate-np.localiza.dev/llm-gate/v2/chat/completions"
 
headers = {
    "API_KEY": API_KEY,
    "Content-Type": "application/json",
    "X-Correlation-id": uuid4().__str__()
}
 
payload = {
    "model": "gpt-4o-mini",
    "messages": [
        {"role": "system", "content": "Você é um assistente prestativo."},
        {"role": "user", "content": "Olá! Me conte uma curiosidade sobre IA."},
    ],
    "temperature": 0.7,
}
 
with httpx.Client() as client:
    response = client.post(URL, headers=headers, json=payload, timeout=30)
    print(response.json())
    response.raise_for_status()
    data = response.json()
 
msg = data["choices"][0]["message"]["content"]
print(msg)