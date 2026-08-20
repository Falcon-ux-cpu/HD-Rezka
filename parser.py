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

# --- СЕКРЕТЫ И НАСТРОЙКИ ---
GIST_TOKEN = os.environ.get("GHOST_GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID")

# Аккаунт-бот (отправитель / проверяемый ящик)
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

# Ваш личный адрес (получатель уведомлений / автор писем управления)
TARGET_EMAIL = os.environ.get("TARGET_EMAIL")

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
    headers = {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "HDRezka-Parser-App"
    }
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        raise Exception(f"Ошибка получения Gist: {res.status_code} | Ответ: {res.text}")
    
    files = res.json()["files"]
    
    watchlist = json.loads(files["watchlist.json"]["content"]) if "watchlist.json" in files else []
    state = json.loads(files["state.json"]["content"]) if "state.json" in files else {}
    return watchlist, state

def update_gist_data(watchlist, state):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "HDRezka-Parser-App"
    }
    payload = {
        "files": {
            "watchlist.json": {"content": json.dumps(watchlist, ensure_ascii=False, indent=2)},
            "state.json": {"content": json.dumps(state, ensure_ascii=False, indent=2)}
        }
    }
    res = requests.patch(url, headers=headers, json=payload)
    if res.status_code == 200:
        print("-> Данные Gist успешно сохранены.")
    else:
        print(f"-> Ошибка сохранения Gist: {res.status_code} | Ответ: {res.text}")

# --- ПРОВЕРКА ПОЧТЫ (IMAP ИЗ ЯРЛЫКА rezka) ---
def process_incoming_emails(watchlist, state):
    removed_titles = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        
        status, _ = mail.select('"rezka"')
        if status != 'OK':
            status, _ = mail.select('"INBOX/rezka"')
            if status != 'OK':
                print("❌ Ошибка: Ярлык/папка 'rezka' не найдена.")
                mail.logout()
                return watchlist, state, removed_titles

        status, messages = mail.search(None, f'(UNSEEN FROM "{TARGET_EMAIL}")')
        email_ids = messages[0].split()

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8")
                    subject = subject.strip()

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

                    mail.store(e_id, '+FLAGS', '\\Seen')

        mail.logout()
    except Exception as e:
        print(f"Ошибка при обработке почты: {e}")

    return watchlist, state, removed_titles

# --- ПАРСИНГ HDREZKA ---
def check_hdrezka(title):
    search_url = f"{BASE_URL}/engine/ajax/search.php"
    payload = {"q": title}
    headers = HEADERS.copy()
    headers["X-Requested-With"] = "XMLHttpRequest"

    try:
        time.sleep(2)
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
        
        time.sleep(2)
        page_res = session.get(page_url, headers=HEADERS, timeout=15)
        if page_res.status_code != 200:
            return None

        page_soup = BeautifulSoup(page_res.text, "html.parser")
        
        ru_title_elem = page_soup.find("h1", itemprop="name")
        ru_title = ru_title_elem.text.strip() if ru_title_elem else title
        
        orig_title_elem = page_soup.find("div", class_="b-post__origtitle", itemprop="alternativeHeadline")
        orig_title = orig_title_elem.text.strip() if orig_title_elem else ""
        
        full_title = f"{ru_title} / {orig_title}" if orig_title else ru_title

        is_series = "/series/" in page_url or "/cartoons/" in page_url or page_soup.find("li", class_="b-simple_season__item")

        # Сериалы
        if is_series:
            completed_elem = page_soup.find("div", class_="b-post__infolast")
            is_completed = completed_elem and "Завершен" in completed_elem.text

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

        # Фильмы
        else:
            awaiting_elem = page_soup.find("div", style=re.compile(r"padding-top:\s*10px"))
            is_awaiting = awaiting_elem and "Ожидаем фильм" in awaiting_elem.text

            if is_awaiting:
                return None

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

# --- ОТПРАВКА ПИСЬМА (SMTP) ---
def send_email(updates, removed):
    if not updates and not removed:
        return

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = TARGET_EMAIL
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
            server.sendmail(GMAIL_USER, TARGET_EMAIL, msg.as_string())
        print(f"-> Уведомление успешно отправлено на адрес {TARGET_EMAIL}.")
    except Exception as e:
        print(f"Ошибка при отправке письма: {e}")

# --- ОСНОВНОЙ ВЫЗОВ ---
def main():
    watchlist, state = get_gist_data()
    watchlist, state, removed_titles = process_incoming_emails(watchlist, state)

    updates = []
    titles_to_remove = []

    for title in list(watchlist):
        print(f"Обработка: {title}...")
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
            updates.append(f'фильм "{data["title"]}" в озвучке {data["translator"]}. Ссылка: {data["url"]}')
            titles_to_remove.append(title)
            removed_titles.append(f'фильм "{data["title"]}" (вышел в хорошем качестве)')

    for t in titles_to_remove:
        if t in watchlist:
            watchlist.remove(t)
        if t in state:
            del state[t]

    send_email(updates, removed_titles)
    update_gist_data(watchlist, state)

if __name__ == "__main__":
    main()
