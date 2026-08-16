"""
Gera inscritas.txt com todos os eventos do calendário "Alpha Competitions".
Corre apenas uma vez!
"""

import os
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

CALENDAR_NAME    = "Alpha Competitions"
SCOPES           = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "credentials.json"
INSCRITAS_FILE   = "inscritas.txt"

def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def main():
    service   = get_calendar_service()
    calendars = service.calendarList().list().execute().get("items", [])
    cal_id    = None
    for cal in calendars:
        if cal.get("summary", "").strip().lower() == CALENDAR_NAME.lower():
            cal_id = cal["id"]
            break

    if not cal_id:
        print(f"Calendário '{CALENDAR_NAME}' não encontrado!")
        return

    # Busca todos os eventos futuros
    now = datetime.now(timezone.utc).isoformat()
    events = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=cal_id,
            timeMin=now,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    print(f"Total de eventos encontrados: {len(events)}")

    with open(INSCRITAS_FILE, "w", encoding="utf-8") as f:
        for ev in events:
            name = ev.get("summary", "").strip()
            if name:
                f.write(f"[ ] {name}\n")

    print(f"Ficheiro '{INSCRITAS_FILE}' criado com {len(events)} eventos!")
    print("Edita o ficheiro e muda '[ ]' para '[x]' nos eventos em que estás inscrita.")

if __name__ == "__main__":
    main()
