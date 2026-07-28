"""Interpola hora del pico/mínimo entre slots de 3h de WindBorne."""


def _horas_decimal(dt):
    return dt.hour + dt.minute / 60.0


def _decimal_a_hora(valor):
    horas = int(valor) % 24
    minutos = int(round((valor - int(valor)) * 60)) % 60
    return f"{horas:02d}:{minutos:02d}"


def _extremo_parabolico(puntos, campo_temp="temp_f", buscar="max"):
    """Estima hora (y opcional temp) del extremo entre vecinos."""
    if not puntos:
        return None, None, None
    orden = sorted(puntos, key=lambda p: p["dt"])
    if buscar == "max":
        extremo = max(orden, key=lambda p: p[campo_temp])
    else:
        extremo = min(orden, key=lambda p: p[campo_temp])
    slot_hora = extremo["hora"]
    idx = orden.index(extremo)

    if idx == 0 or idx == len(orden) - 1:
        return extremo[campo_temp], slot_hora, slot_hora

    p0, p1, p2 = orden[idx - 1], orden[idx], orden[idx + 1]
    t0, t1, t2 = _horas_decimal(p0["dt"]), _horas_decimal(p1["dt"]), _horas_decimal(p2["dt"])
    y0, y1, y2 = p0[campo_temp], p1[campo_temp], p2[campo_temp]
    denom = (t0 - t1) * (t0 - t2) * (t1 - t2)
    if abs(denom) < 1e-9:
        return y1, slot_hora, slot_hora

    a = (t2 * (y1 - y0) + t1 * (y0 - y2) + t0 * (y2 - y1)) / denom
    b = (t0 * t0 * (y1 - y2) + t1 * t1 * (y2 - y0) + t2 * t2 * (y0 - y1)) / denom

    if abs(a) < 1e-9:
        return y1, slot_hora, slot_hora

    t_vertex = max(t0, min(t2, -b / (2 * a)))
    hora_est = _decimal_a_hora(t_vertex)
    temp_est = round(a * t_vertex * t_vertex + b * t_vertex + (
        y0 - a * t0 * t0 - b * t0
    ), 1)
    if buscar == "max":
        temp_est = max(y0, y1, y2, temp_est)
    else:
        temp_est = min(y0, y1, y2, temp_est)
    return temp_est, hora_est, slot_hora


def enriquecer_pico(punto_pico, puntos):
    temp, hora, slot = _extremo_parabolico(puntos, buscar="max")
    if hora:
        punto_pico = {
            **punto_pico,
            "temp_f": temp if temp is not None else punto_pico["temp_f"],
            "hora": hora,
            "hora_slot": slot,
        }
    return punto_pico


def enriquecer_min(puntos):
    temp, hora, slot = _extremo_parabolico(puntos, buscar="min")
    return {"temp_f": temp, "hora": hora, "hora_slot": slot}


def futuros_sin_repetir(puntos, punto_ahora, punto_pico):
    pico_dt = punto_pico.get("dt")
    return sorted(
        [
            p
            for p in puntos
            if p["dt"] > punto_ahora["dt"]
            and (pico_dt is None or p["dt"] != pico_dt)
        ],
        key=lambda p: p["dt"],
    )


def texto_hora(punto):
    hora = punto.get("hora") or "N/D"
    slot = punto.get("hora_slot")
    if slot and slot != hora:
        return f"~{hora} (slot {slot})"
    return f"~{hora}"