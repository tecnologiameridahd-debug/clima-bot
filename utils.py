"""Utilidades compartidas: conversion de unidades, distancia geografica, deteccion de errores WindBorne."""
import math


def c_to_f(c):
    return round(float(c) * 9 / 5 + 32, 1)


def f_to_c(f):
    return round((float(f) - 32) * 5 / 9, 2)


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia haversine en km entre dos puntos lat/lon."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def es_error_cuota(texto):
    """True si el texto de error indica cuota WindBorne agotada (free trial de por vida).

    Ojo: NO se usa el substring "2000" solo — es demasiado genérico y puede
    coincidir con un rate-limit pasajero (ej. "max 2000 req/hora") que sí se
    recupera solo, distinto del free trial agotado que no se recupera.
    """
    t = (texto or "").lower()
    return "quota exceeded" in t or "free trial" in t


def es_error_rate_limit(texto):
    """True si el error es un 429 (temporal o de cuota)."""
    t = (texto or "").lower()
    return "429" in t or es_error_cuota(texto)
