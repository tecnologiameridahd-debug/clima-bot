import requests
import re
import csv
import os
from datetime import datetime

HEADERS = {
    "User-Agent": "AlbertoWeatherBot (tu_email@ejemplo.com)",
    "Accept": "application/ld+json"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "denver_cli_report_log.csv")

# DEN = codigo de localidad de clima usado por NWS para el reporte CLI de Denver
LOCATION_ID = "DEN"


def get_latest_cli_product_id():
    """Lista los productos CLI mas recientes para Denver y devuelve el ID del ultimo."""
    url = f"https://api.weather.gov/products/types/CLI/locations/{LOCATION_ID}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    products = data.get("@graph", [])
    if not products:
        return None, None
    latest = products[0]  # vienen ordenados del mas reciente al mas viejo
    return latest["id"], latest.get("issuanceTime")


def get_product_text(product_id):
    """Descarga el texto completo del reporte CLI."""
    url = f"https://api.weather.gov/products/{product_id}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()["productText"]


_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


def parse_report_date(text):
    """
    Extrae la fecha del reporte, ej:
    '...CLIMATE SUMMARY FOR JULY 7 2026...' -> '2026-07-07'
    """
    match = re.search(
        r"CLIMATE SUMMARY FOR\s+(\w+)\s+(\d{1,2})\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    month_str, day, year = match.groups()
    month = _MONTHS.get(month_str.upper())
    if not month:
        return None
    return f"{int(year)}-{month:02d}-{int(day):02d}"


def _parse_cli_clock(raw_time, ampm):
    raw_time = raw_time.zfill(4)
    hour = int(raw_time[:2])
    minute = int(raw_time[2:])
    if ampm.upper() == "PM" and hour != 12:
        hour += 12
    if ampm.upper() == "AM" and hour == 12:
        hour = 0
    return hour, minute, f"{hour:02d}:{minute:02d}"


def parse_max_temp(text):
    """
    Extrae la temperatura maxima del texto del reporte CLI.
    El formato tipico tiene una linea como:
    MAXIMUM         94   1234 PM     93    2017
    """
    det = parse_max_temp_detail(text)
    return det[0] if det else None


def parse_max_temp_detail(text):
    """
    Devuelve (temp_f, hora_local_str, hora_24) del MAXIMUM observado.
    Ej: MAXIMUM  73  1200 AM  -> (73, '00:00', 0)
    """
    match = re.search(r"MAXIMUM\s+(\d+)\s+(\d{3,4})\s?(AM|PM)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"MAXIMUM\s+(\d+)", text)
        if match:
            return int(match.group(1)), None, None
        return None
    temp = int(match.group(1))
    hour, _minute, time_str = _parse_cli_clock(match.group(2), match.group(3))
    return temp, time_str, hour


def parse_valid_as_of(text):
    """
    Extrae la hora 'VALID TODAY AS OF' del reporte, ej: '0500 PM' o '1159 PM'.
    Devuelve (hora_24h_str, es_final).
    Se considera FINAL si la hora es 11 PM o mas tarde (cierre del dia climatologico).
    """
    match = re.search(
        r"VALID(?:\s+TODAY)?\s+AS\s+OF\s+(\d{3,4})\s?(AM|PM)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, False

    raw_time, ampm = match.groups()
    raw_time = raw_time.zfill(4)
    hour = int(raw_time[:2])
    minute = int(raw_time[2:])

    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0

    time_str = f"{hour:02d}:{minute:02d}"
    es_final = hour == 23 or (hour == 0)  # 11 PM o medianoche en adelante
    return time_str, es_final


def fetch_and_log():
    product_id, issuance_time = get_latest_cli_product_id()
    if not product_id:
        print(f"[{datetime.now()}] No se encontro ningun reporte CLI todavia.")
        return

    text = get_product_text(product_id)
    max_temp = parse_max_temp(text)
    valid_as_of, es_final = parse_valid_as_of(text)

    row = {
        "scrape_time": datetime.now().isoformat(),
        "issuance_time": issuance_time,
        "product_id": product_id,
        "max_temp_f": max_temp,
        "valid_as_of": valid_as_of,
        "es_final": es_final,
    }

    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"[{row['scrape_time']}] CLI Report encontrado:")
    print(f"  Emitido: {issuance_time}")
    print(f"  Valido hasta: {valid_as_of}")
    print(f"  Maxima observada: {max_temp} F")
    if es_final:
        print(f"  *** ESTE ES EL REPORTE FINAL DEL DIA ***")
    else:
        print(f"  (Reporte PRELIMINAR - el maximo puede cambiar todavia)")
    print(f"\n--- Texto completo del reporte ---\n{text}\n")


if __name__ == "__main__":
    fetch_and_log()
