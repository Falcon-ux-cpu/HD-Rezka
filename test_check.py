import sys
from curl_cffi import requests

# Актуальное зеркало HDRezka (при необходимости можно изменить)
BASE_URL = "https://hdrezka.ag"
SEARCH_QUERY = "Матрица"

def test_connection():
    print(f"[1/2] Проверка прямого доступа к {BASE_URL}...")
    
    # Сессия с эмуляцией TLS-отпечатка Chrome 120
    session = requests.Session(impersonate="chrome120")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": BASE_URL,
    }

    # 1. Проверка главной страницы
    try:
        response = session.get(BASE_URL, headers=headers, timeout=15)
        print(f"-> Статус ответа главной страницы: {response.status_code}")
        
        if response.status_code == 200:
            print("-> Успешно! Главная страница доступна.")
        else:
            print(f"-> Внимание! Получен код {response.status_code}.")
    except Exception as e:
        print(f"-> Ошибка подключения к главной странице: {e}")
        sys.exit(1)

    print("\n[2/2] Проверка поискового AJAX-запроса...")
    
    # 2. Проверка работы поиска
    search_url = f"{BASE_URL}/engine/ajax/search.php"
    payload = {"q": SEARCH_QUERY}
    
    headers["X-Requested-With"] = "XMLHttpRequest"
    
    try:
        search_res = session.post(search_url, data=payload, headers=headers, timeout=15)
        print(f"-> Статус ответа поиска: {search_res.status_code}")
        
        if search_res.status_code == 200:
            print("-> Результат поиска получен!")
            print("-> Первые 200 символов ответа:")
            print("-" * 40)
            print(search_res.text[:200])
            print("-" * 40)
            print("\n✅ ТЕСТ ПРОЙДЕН: Защита не заблокировала запрос.")
        else:
            print(f"❌ ОШИБКА: Поиск вернул код {search_res.status_code}")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Ошибка при выполнении поиска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_connection()
