
import os
import requests
import json

def diagnose():
    print("=== Prospector API Diagnostics ===")
    
    # 1. Check Env Vars
    serper_key = os.environ.get("SERPER_KEY", "")
    ollama_key = os.environ.get("OLLAMA_KEY", "")
    ollama_base = os.environ.get("OLLAMA_BASE", "https://ollama.com/v1")
    
    print(f"SERPER_KEY: {'[SET]' if serper_key else '[MISSING]'}")
    print(f"OLLAMA_KEY: {'[SET]' if ollama_key else '[MISSING]'}")
    print(f"OLLAMA_BASE: {ollama_base}")
    
    if not serper_key:
        print("\nERROR: SERPER_KEY is missing. Search will not work.")
        return

    # 2. Test Serper Connectivity
    print("\n--- Testing Serper API ---")
    query = "restaurante Curitiba PR"
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": "br", "hl": "pt-br", "num": 10}
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"Status Code: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            organic = data.get("organic", [])
            print(f"Results found: {len(organic)}")
            if organic:
                print(f"First result: {organic[0].get('title')}")
            else:
                print("Warning: No results found for 'restaurante Curitiba PR'. Check your Serper credits or query.")
        else:
            print(f"Error Response: {r.text}")
    except Exception as e:
        print(f"Serper Exception: {e}")

    # 3. Test Places Connectivity
    print("\n--- Testing Serper Places ---")
    url_places = "https://google.serper.dev/places"
    try:
        r = requests.post(url_places, headers=headers, json=payload, timeout=15)
        print(f"Status Code: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            places = data.get("places", [])
            print(f"Places found: {len(places)}")
            if places:
                print(f"First place: {places[0].get('title')}")
        else:
            print(f"Error Response: {r.text}")
    except Exception as e:
        print(f"Serper Places Exception: {e}")

    # 4. Test BrasilAPI
    print("\n--- Testing BrasilAPI ---")
    test_cnpj = "00000000000191" # Banco do Brasil
    url_cnpj = f"https://brasilapi.com.br/api/cnpj/v1/{test_cnpj}"
    try:
        r = requests.get(url_cnpj, timeout=10)
        print(f"Status Code: {r.status_code}")
        if r.status_code == 200:
            print(f"CNPJ Lookup OK: {r.json().get('razao_social')}")
        else:
            print(f"Error Response: {r.text}")
    except Exception as e:
        print(f"BrasilAPI Exception: {e}")

    # 5. Test AI Connectivity
    if ollama_key:
        print("\n--- Testing AI (Ollama) ---")
        url_ai = f"{ollama_base}/chat/completions"
        ai_headers = {"Authorization": f"Bearer {ollama_key}", "Content-Type": "application/json"}
        ai_payload = {
            "model": "glm-5.1",
            "messages": [{"role": "user", "content": "Olá, responda com OK se estiver funcionando."}],
            "max_tokens": 50
        }
        try:
            r = requests.post(url_ai, headers=ai_headers, json=ai_payload, timeout=30)
            print(f"Status Code: {r.status_code}")
            if r.status_code == 200:
                print(f"AI Response: {r.json()['choices'][0]['message']['content']}")
            else:
                print(f"Error Response: {r.text}")
        except Exception as e:
            print(f"AI Exception: {e}")

if __name__ == "__main__":
    diagnose()
