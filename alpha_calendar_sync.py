"""
Alpha Competition Services → Google Calendar Sync
==================================================
- Cria eventos novos futuros
- Atualiza data/local de eventos existentes futuros
- Remove eventos futuros que deixaram de existir no site
- Eventos em inscritas.txt com [x] ficam cor tomate no calendário
- Adiciona eventos novos ao inscritas.txt como [ ]
- Notificação Windows com lista de novos e alterados (local) / resumo no
  GitHub Actions Step Summary (quando corre em CI)
- Gera map/index.html (mapa mundo Leaflet) com a localização de todas as
  provas, para publicação via GitHub Pages
"""

import os
import re
import requests
import logging
import subprocess
import json
from datetime import date, datetime, timedelta, timezone
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────────────────

URL              = "https://alphacompetitionservices.com/calendar/"
CALENDAR_NAME    = "Alpha Competitions"
SCOPES           = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
]
TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "credentials.json"
INSCRITAS_FILE   = "inscritas.txt"
COLOR_INSCRITA   = "11"  # Tomate

MAP_DIR          = "map"
CITY_COORDS_FILE = os.path.join(MAP_DIR, "city_coords.json")
MAP_TEMPLATE_FILE = os.path.join(MAP_DIR, "template.html")
MAP_OUTPUT_FILE  = "index.html"

# Correções manuais de localizações mal formatadas vindas do site da Alpha
LOCATION_FIXES = {
    "BRCELONA (SPAIN)": "BARCELONA (SPAIN)",
    "& 1": "KRIENS (SWITZERLAND)",
    "GURUTZETA (SPAIN)": "GALDAKAO (SPAIN)",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── AUTENTICAÇÃO ─────────────────────────────────────────────────────────────

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

def get_or_create_calendar(service, name):
    calendars = service.calendarList().list().execute().get("items", [])
    for cal in calendars:
        if cal.get("summary", "").strip().lower() == name.lower():
            log.info(f"Calendário encontrado: '{name}' (id: {cal['id']})")
            return cal["id"]
    new_cal = service.calendars().insert(body={"summary": name}).execute()
    log.info(f"Calendário criado: '{name}' (id: {new_cal['id']})")
    return new_cal["id"]

# ─── INSCRITAS ────────────────────────────────────────────────────────────────

def load_inscritas():
    if not os.path.exists(INSCRITAS_FILE):
        return set()
    inscritas = set()
    with open(INSCRITAS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.lower().startswith("[x]"):
                inscritas.add(line[3:].strip().lower())
    return inscritas

def add_to_inscritas(names):
    existing = set()
    lines = []
    if os.path.exists(INSCRITAS_FILE):
        with open(INSCRITAS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            s = line.strip()
            if s.startswith("[ ]") or s.lower().startswith("[x]"):
                existing.add(s[3:].strip().lower())
    new_lines = [f"[ ] {n}\n" for n in names if n.lower() not in existing]
    if new_lines:
        with open(INSCRITAS_FILE, "a", encoding="utf-8") as f:
            if lines and not lines[-1].endswith("\n"):
                f.write("\n")
            f.writelines(new_lines)
        log.info(f"  {len(new_lines)} evento(s) adicionado(s) ao inscritas.txt")

def is_inscrita(name, inscritas):
    name_lower = name.lower()
    return any(isinstance(i, str) and (i in name_lower or name_lower in i) for i in inscritas)

# ─── SCRAPING ─────────────────────────────────────────────────────────────────

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

def parse_dates(date_text, year=2026):
    text = date_text.strip().lower()
    months_found = [m for m in MONTH_MAP if m in text]
    numbers = list(map(int, re.findall(r"\d+", text)))
    if not numbers or not months_found:
        return None, None
    if len(months_found) == 1:
        month = MONTH_MAP[months_found[0]]
        return date(year, month, numbers[0]), date(year, month, numbers[-1])
    second = months_found[1]
    pos = text.index(second)
    nb = list(map(int, re.findall(r"\d+", text[:pos])))
    na = list(map(int, re.findall(r"\d+", text[pos:])))
    m1, m2 = MONTH_MAP[months_found[0]], MONTH_MAP[second]
    return (date(year, m1, nb[0]) if nb else date(year, m1, 1),
            date(year, m2, na[-1]) if na else date(year, m2, 1))

def scrape_events():
    log.info(f"A fazer scraping de {URL}")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="pt-PT",
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html = page.content()
        browser.close()
    resp_content = html.encode("utf-8")
    soup = BeautifulSoup(resp_content, "html.parser")
    events = []
    today = date.today()
    for h2 in soup.find_all("h2"):
        name = h2.get_text(strip=True)
        if not name or len(name) < 3:
            continue
        if name.upper() in [m.upper() for m in MONTH_MAP]:
            continue
        if any(x in name for x in ["COMPETITIONS CALENDAR", "Check the official"]):
            continue
        sibling = h2.find_next_sibling()
        date_text = location = ""
        if sibling:
            strongs = sibling.find_all("strong")
            if len(strongs) >= 2:
                date_text, location = strongs[0].get_text(strip=True), strongs[1].get_text(strip=True)
            elif len(strongs) == 1:
                date_text = strongs[0].get_text(strip=True)
        if not date_text:
            continue
        start, end = parse_dates(date_text)
        if not start or end < today:
            continue
        events.append({"name": name, "date_text": date_text, "location": location, "start": start, "end": end})
    log.info(f"Total de eventos futuros no site: {len(events)}")
    return dedupe_events(events)

def dedupe_events(events):
    """
    O site da Alpha lista por vezes a mesma prova em vários blocos <h2>
    (nome + data + local idênticos). Sem esta deduplicação, cada ocorrência
    era inserida como um evento novo no Google Calendar na primeira vez que
    era vista (o `cal_by_key` do sync() é um snapshot tirado uma vez no
    início e não é atualizado a cada insert dentro do mesmo loop) —
    explica os eventos duplicados encontrados no calendário.
    """
    seen = set()
    deduped = []
    dropped = 0
    for ev in events:
        key = (ev["name"].strip().lower(), ev["start"].isoformat(), ev["location"].strip().lower())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(ev)
    if dropped:
        log.info(f"  {dropped} entrada(s) duplicada(s) no site descartada(s) antes do sync")
    return deduped

# ─── CALENDÁRIO ───────────────────────────────────────────────────────────────

def get_calendar_events(service, calendar_id):
    now = datetime.now(timezone.utc).isoformat()
    items = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return items

def get_all_calendar_events(service, calendar_id):
    """
    Todos os eventos do calendário, passados e futuros — ao contrário de
    get_calendar_events() (usada na sync, que só olha para o futuro), esta
    função alimenta o mapa, que deve mostrar o histórico completo tal como
    aparece no Google Calendar (a sync nunca apaga eventos passados).
    """
    items = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=calendar_id,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return items

def format_date(d):
    if isinstance(d, str):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            return d
    return d.strftime("%d/%m/%Y")

# ─── SINCRONIZAÇÃO ────────────────────────────────────────────────────────────

def sync(service, calendar_id, site_events):
    inscritas    = load_inscritas()
    cal_events   = get_calendar_events(service, calendar_id)
    created_list = []
    changed_list = []
    deleted_list = []
    new_names    = []

    cal_by_key = {}
    for ev in cal_events:
        name     = ev.get("summary", "").strip().lower()
        start    = ev.get("start", {}).get("date", "")
        location = ev.get("location", "").strip().lower()
        cal_by_key[f"{name}|{start}|{location}"] = ev

    site_by_key = {}
    for ev in site_events:
        key = f"{ev['name'].lower()}|{ev['start'].isoformat()}|{ev['location'].lower()}"
        site_by_key[key] = ev

    for ev in site_events:
        key      = f"{ev['name'].lower()}|{ev['start'].isoformat()}|{ev['location'].lower()}"
        inscrita = is_inscrita(ev["name"], inscritas)
        existing = cal_by_key.get(key)
        body = {
            "summary":     ev["name"],
            "location":    ev["location"],
            "description": f"Fonte: {URL}\nData original: {ev['date_text']}",
            "start": {"date": ev["start"].isoformat()},
            "end":   {"date": (ev["end"] + timedelta(days=1)).isoformat()},
        }
        if existing is None:
            if inscrita:
                body["colorId"] = COLOR_INSCRITA
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            cal_by_key[key] = created  # evita re-criar se o site repetir esta chave no mesmo loop
            created_list.append(f"• {ev['name']} — {format_date(ev['start'])}")
            new_names.append(ev["name"])
            log.info(f"  ✅ Criado: {ev['name']} ({ev['start']})")
        else:
            old_location = existing.get("location", "")
            old_color    = existing.get("colorId")
            changes      = []
            if old_location != ev["location"] and ev["location"]:
                changes.append(f"local: {old_location} → {ev['location']}")
            color_patch = {}
            if inscrita and old_color != COLOR_INSCRITA:
                color_patch["colorId"] = COLOR_INSCRITA
            elif not inscrita and old_color == COLOR_INSCRITA:
                color_patch["colorId"] = None
            if changes or color_patch:
                patch_body = {}
                if changes:
                    patch_body["location"] = ev["location"]
                if color_patch:
                    patch_body["colorId"] = color_patch["colorId"]
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=existing["id"],
                    body=patch_body
                ).execute()
                if changes:
                    changed_list.append(f"• {ev['name']} — {', '.join(changes)}")
                    log.info(f"  🔄 Alterado: {ev['name']} ({', '.join(changes)})")
                if color_patch:
                    log.info(f"  🎨 Cor atualizada: {ev['name']}")
            else:
                log.info(f"  → Sem alterações: {ev['name']}")

    if len(site_events) > 0:  # só remove se o scraping devolveu resultados
        for key, existing in cal_by_key.items():
            if key not in site_by_key:
                service.events().delete(calendarId=calendar_id, eventId=existing["id"]).execute()
                deleted_list.append(f"• {existing.get('summary', '')}")
                log.info(f"  🗑 Removido: {existing.get('summary', '')}")
    else:
        log.warning("Lista de eventos vazia — remoções ignoradas por segurança.")

    if new_names:
        add_to_inscritas(new_names)

    log.info(f"\nResumo: {len(created_list)} criados, {len(changed_list)} alterados, {len(deleted_list)} removidos.")
    send_notification(created_list, changed_list, deleted_list)

# ─── NOTIFICAÇÃO ──────────────────────────────────────────────────────────────

def send_notification(created_list, changed_list, deleted_list):
    counts = []
    if created_list: counts.append(f"{len(created_list)} novos")
    if changed_list: counts.append(f"{len(changed_list)} alterados")
    if deleted_list: counts.append(f"{len(deleted_list)} removidos")
    title = "Alpha Competitions — " + (", ".join(counts) if counts else "sem alterações no calendário")

    # Expõe o resumo ao workflow do GitHub Actions (via GITHUB_ENV) para a
    # mensagem do commit refletir o que realmente mudou — assim a Diana vê
    # de relance, no histórico de commits ou na notificação do GitHub, se
    # houve provas novas/alteradas/removidas nesta corrida, mesmo sem abrir
    # os logs. Escrito sempre, mesmo quando não há alterações.
    env_path = os.environ.get("GITHUB_ENV")
    if env_path:
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"SYNC_SUMMARY={title}\n")
        except Exception as e:
            log.debug(f"Não foi possível escrever GITHUB_ENV: {e}")

    if not created_list and not changed_list and not deleted_list:
        return

    lines = []
    if created_list:
        lines.append("Novos:")
        lines.extend(created_list)
    if changed_list:
        if lines: lines.append("")
        lines.append("Alterados:")
        lines.extend(changed_list)
    if deleted_list:
        if lines: lines.append("")
        lines.append("Removidos:")
        lines.extend(deleted_list)
    text = "\n".join(lines)

    # Notificação Windows (BurntToast) — só faz algo em Windows local; em
    # qualquer outro SO (incl. runners do GitHub Actions) falha em silêncio.
    try:
        ps_cmd = f'Import-Module BurntToast; New-BurntToastNotification -Text "{title}", "{text}"'
        subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd])
        log.info("Notificação BurntToast enviada.")
    except Exception as e:
        log.debug(f"Notificação BurntToast não enviada: {e}")

    # Resumo no GitHub Actions (aparece na app do GitHub depois de cada corrida)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(f"## {title}\n\n")
                f.write(text.replace("\n", "  \n") + "\n")
            log.info("Resumo escrito no GITHUB_STEP_SUMMARY.")
        except Exception as e:
            log.debug(f"Não foi possível escrever o step summary: {e}")

# ─── MAPA ─────────────────────────────────────────────────────────────────────

def clean_location(loc):
    if not loc:
        return None
    loc = loc.strip()
    return LOCATION_FIXES.get(loc, loc)

def build_map(calendar_events):
    """
    Gera o mapa a partir de TODOS os eventos do calendário (passados e
    futuros) — não usa site_events porque o scraping só devolve provas
    futuras (o site da Alpha não lista o que já passou).
    """
    if not os.path.exists(CITY_COORDS_FILE) or not os.path.exists(MAP_TEMPLATE_FILE):
        log.warning("Ficheiros do mapa (map/city_coords.json ou map/template.html) não encontrados — a saltar geração do mapa.")
        return

    with open(CITY_COORDS_FILE, encoding="utf-8") as f:
        coords_table = json.load(f)

    mapped = []
    no_location = []
    unknown = set()
    seen = set()

    for ev in calendar_events:
        name = ev.get("summary", "").strip()
        start = ev.get("start", {}).get("date")
        if not name or not start:
            continue
        # proteção extra: ignora duplicados que possam existir no calendário
        # (ex.: entradas antigas criadas antes da correção do dedupe_events)
        dedupe_key = (name.lower(), start, ev.get("location", "").strip().lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        loc = clean_location(ev.get("location", ""))
        if not loc:
            no_location.append({"summary": name, "start": start})
            continue
        coords = coords_table.get(loc)
        if coords is None:
            unknown.add(loc)
            no_location.append({"summary": name, "start": start})
            continue
        mapped.append({
            "summary": name,
            "location": loc,
            "start": start,
            "lat": coords[0],
            "lon": coords[1],
        })

    if unknown:
        log.warning(f"Localizações novas sem coordenadas na tabela (mapa fica sem estas provas): {sorted(unknown)}")
        log.warning("Acrescenta-as a map/city_coords.json (formato \"CIDADE (PAÍS)\": [lat, lon]).")

    with open(MAP_TEMPLATE_FILE, encoding="utf-8") as f:
        html = f.read()

    now = datetime.now(timezone.utc)
    html = html.replace("__EVENTS_JSON__", json.dumps(mapped, ensure_ascii=False))
    html = html.replace("__NO_LOCATION_JSON__", json.dumps(no_location, ensure_ascii=False))
    html = html.replace("__LAST_UPDATED__", now.strftime("%d/%m/%Y %H:%M UTC"))
    html = html.replace("__TODAY_ISO__", now.strftime("%Y-%m-%dT00:00:00Z"))

    with open(MAP_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"Mapa gerado: {MAP_OUTPUT_FILE} ({len(mapped)} provas geolocalizadas, {len(no_location)} sem localização).")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    service     = get_calendar_service()
    calendar_id = get_or_create_calendar(service, CALENDAR_NAME)
    site_events = scrape_events()
    sync(service, calendar_id, site_events)

    all_events = get_all_calendar_events(service, calendar_id)
    build_map(all_events)

if __name__ == "__main__":
    main()
