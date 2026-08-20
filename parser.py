import os
import re
import time
import json
import imaplib
import email
from email.header import decode_header
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests

# --- КОНФИГУРАЦИЯ И СЕКРЕТЫ ---
GIST_TOKEN = os.environ.get("GHOST_GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

BASE_URL = "https://hdrezka.ag"
IMAP_SERVER = "imap.gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Referer": BASE_URL,
}

session = requests.Session(impersonate="chrome120")

# --- РАБОТА С GIST API ---
def get_gist_data():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        raise Exception(f"Ошибка получения Gist: {res.status_code}")
    files = res.json()["files"]
    
    watchlist = json.loads(files["watchlist.json"]["content"]) if "watchlist.json" in files else []
    state = json.loads(files["state.json"]["content"]) if "state.json" in files else {}
    return watchlist, state

def update_gist_data(watchlist, state):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = {
        "files": {
            "watchlist.json": {"content": json.dumps(watchlist, ensure_ascii=False, indent=2)},
            "state.json": {"content": json.dumps(state, ensure_ascii=False, indent=2)}
        }
    }
    res = requests.patch(url, headers=headers, json=payload)
    if res.status_code == 200:
        print("-> Данные Gist успешно обновлены.")
    else:
        print(f"-> Ошибка обновления Gist: {res.status_code}")

# --- ПРОВЕРКА ВХОДЯЩЕЙ ПОЧТЫ (IMAP) ---
def process_incoming_emails(watchlist, state):
    removed_titles = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        # Ищем только непрочитанные письма от вашего адреса
        status, messages = mail.search(None, f'(UNSEEN FROM "{GMAIL_USER}")')
        email_ids = messages[0].split()

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Декодирование темы
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8")
                    subject = subject.strip()

                    # Извлечение тела письма
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode()
                                break
                    else:
                        body = msg.get_payload(decode=True).decode()

                    lines = [line.strip() for line in body.splitlines() if line.strip()]

                    if subject == "HDREZKA_ADD":
                        for item in lines:
                            if item not in watchlist:
                                watchlist.append(item)
                                print(f"Добавлено из письма: {item}")
                    
                    elif subject == "HDREZKA_REMOVE":
                        for item in lines:
                            if item in watchlist:
                                watchlist.remove(item)
                                removed_titles.append(f'"{item}" (по вашей команде)')
                                if item in state:
                                    del state[item]
                                print(f"Удалено из письма: {item}")

                    # Помечаем письмо как прочитанное
                    mail.store(e_id, '+FLAGS', '\\Seen')

        mail.logout()
    except Exception as e:
        print(f"Ошибка чтения почты: {e}")

    return watchlist, state, removed_titles

# --- ПАРСИНГ HDREZKA ---
def check_hdrezka(title):
    search_url = f"{BASE_URL}/engine/ajax/search.php"
    payload = {"q": title}
    headers = HEADERS.copy()
    headers["X-Requested-With"] = "XMLHttpRequest"

    try:
        res = session.post(search_url, data=payload, headers=headers, timeout=15)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, "html.parser")
        first_item = soup.find("li")
        if not first_item:
            return None

        link_elem = first_item.find("a")
        if not link_elem:
            return None

        page_url = link_elem["href"]
        
        # Переход на страницу тайтла
        time.sleep(1.5)
        page_res = session.get(page_url, headers=HEADERS, timeout=15)
        if page_res.status_code != 200:
            return None

        page_soup = BeautifulSoup(page_res.text, "html.parser")
        
        # Заголовок и оригинальное название
        ru_title = link_elem.find("span", class_="enty").text.strip() if link_elem.find("span", class_="enty") else title
        orig_title_elem = page_soup.find("div", class_="b-post__origtitle")
        orig_title = orig_title_elem.text.strip() if orig_title_elem else ""
        
        full_title = f"{ru_title} / {orig_title}" if orig_title else ru_title

        is_series = "/series/" in page_url or "/cartoons/" in page_url or page_soup.find("li", class_="b-simple_season__item")

        # --- СКРИПТ ДЛЯ СЕРИАЛОВ ---
        if is_series:
            # Проверка статуса "Завершен"
            completed_elem = page_soup.find("div", class_="b-post__infolast")
            is_completed = completed_elem and "Завершен" in completed_elem.text

            # Получение текущей серии и сезона
            season_elem = page_soup.find("li", class_="b-simple_season__item active")
            episode_elem = page_soup.find("li", class_="b-simple_episode__item active") or page_soup.find("li", class_="b-simple_episode__item")
            translator_elem = page_soup.find("li", class_="b-translator__item active") or page_soup.find("li", class_="b-translator__item")

            season_str = season_elem.text.strip() if season_elem else "1 сезон"
            episode_str = episode_elem.text.strip() if episode_elem else "1 серия"
            translator_str = translator_elem.text.strip() if translator_elem else "Стандартная"

            return {
                "type": "series",
                "title": full_title,
                "url": page_url,
                "status": f"{season_str}, {episode_str}",
                "translator": translator_str,
                "is_completed": is_completed
            }

        # --- СКРИПТ ДЛЯ ФИЛЬМОВ ---
        else:
            awaiting_elem = page_soup.find("div", style=re.compile(r"padding-top:\s*10px"))
            is_awaiting = awaiting_elem and "Ожидаем фильм" in awaiting_elem.text

            if is_awaiting:
                return None  # Фильм ещё не вышел в хорошем качестве

            translator_elem = page_soup.find("li", class_="b-translator__item active") or page_soup.find("li", class_="b-translator__item")
            translator_str = translator_elem.text.strip() if translator_elem else "Дубляж / Лицензия"

            return {
                "type": "movie",
                "title": full_title,
                "url": page_url,
                "translator": translator_str
            }

    except Exception as e:
        print(f"Ошибка при парсинге '{title}': {e}")
        return None

# --- ОТПРАВКА GMAIL (SMTP) ---
def send_email(updates, removed):
    if not updates and not removed:
        return

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = "HD REZKA"

    body_text = ""

    if updates:
        body_text += "Найдено:\n"
        for up in updates:
            body_text += f"- {up}\n"
        body_text += "\n"

    if removed:
        body_text += "Удалено из отслеживания:\n"
        for rm in removed:
            body_text += f"- {rm}\n"

    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
        print("-> Письмо с уведомлением успешно отправлено!")
    except Exception as e:
        print(f"Ошибка отправки почты: {e}")

# --- ОСНОВНОЙ ЦИКЛ ---
def main():
    watchlist, state = get_gist_data()
    watchlist, state, removed_titles = process_incoming_emails(watchlist, state)

    updates = []
    titles_to_remove = []

    for title in list(watchlist):
        print(f"Проверка: {title}...")
        data = check_hdrezka(title)
        
        if not data:
            continue

        last_state = state.get(title)

        if data["type"] == "series":
            current_state_str = f"{data['status']} ({data['translator']})"
            if last_state != current_state_str:
                state[title] = current_state_str
                updates.append(f'{data["status"]} сериала "{data["title"]}" в озвучке {data["translator"]}. Ссылка: {data["url"]}')
            
            if data["is_completed"]:
                titles_to_remove.append(title)
                removed_titles.append(f'"{data["title"]}" (получен статус "Завершён")')

        elif data["type"] == "movie":
            # Если фильм вышел в качестве
            updates.append(f'фильм "{data["title"]}" в озвучке {data["translator"]}. Ссылка: {data["url"]}')
            titles_to_remove.append(title)
            removed_titles.append(f'фильм "{data["title"]}" (вышел в хорошем качестве)')

    # Удаление завершенных/вышедших
    for t in titles_to_remove:
        if t in watchlist:
            watchlist.remove(t)
        if t in state:
            del state[t]

    # Отправка уведомлений и запись в Gist
    send_email(updates, removed_titles)
    update_gist_data(watchlist, state)

if __name__ == "__main__":
    main()
