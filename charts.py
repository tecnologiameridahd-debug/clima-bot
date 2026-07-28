import csv
import io
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analysis import dist_f
from config import log_csv_path

BG = "#0f172a"
PANEL = "#1e293b"
TEXT = "#f1f5f9"
UMBRAL_COLORS = {95: "#22c55e", 96: "#84cc16", 97: "#f59e0b", 98: "#ef4444", 99: "#dc2626", 100: "#991b1b"}


def _to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()


def grafico_completo(a):
    puntos = a["puntos"]
    if not puntos:
        return None

    x = list(range(len(puntos)))
    horas = [p["hora"] for p in puntos]
    temps = [p["temp_f"] for p in puntos]
    p10, p25, p75, p90, means = [], [], [], [], []
    for p in puntos:
        d = dist_f(p.get("dist"))
        p10.append(d.get("p10", p["temp_f"]))
        p25.append(d.get("p25", p["temp_f"]))
        p75.append(d.get("p75", p["temp_f"]))
        p90.append(d.get("p90", p["temp_f"]))
        means.append(d.get("mean", p["temp_f"]))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor(BG)
    fig.suptitle(
        f"WindBorne WM-6 — {a['city']['nombre']} {a['fecha']}  ·  {a['init_txt']}",
        color=TEXT,
        fontsize=14,
        fontweight="bold",
    )

    ax1.set_facecolor(PANEL)
    ax1.fill_between(x, p10, p90, alpha=0.12, color="#a78bfa")
    ax1.fill_between(x, p25, p75, alpha=0.35, color="#8b5cf6", label="IA p25–p75")
    ax1.plot(x, means, "--", color="#c4b5fd", linewidth=1.5, label="Media IA")
    ax1.plot(x, temps, "o-", color="#f472b6", linewidth=2.5, markersize=9, label="Temp", zorder=5)

    for u, c in UMBRAL_COLORS.items():
        ax1.axhline(u, color=c, linestyle=":", alpha=0.4, linewidth=1)

    pico_i = temps.index(max(temps))
    ax1.annotate(
        f"PICO {temps[pico_i]}°F",
        xy=(pico_i, temps[pico_i]),
        xytext=(pico_i, temps[pico_i] + 3),
        color="#fbbf24",
        fontweight="bold",
        ha="center",
        arrowprops=dict(arrowstyle="->", color="#fbbf24"),
    )

    ax1.set_xticks(x)
    ax1.set_xticklabels(horas, color=TEXT)
    ax1.set_ylabel("°F", color=TEXT)
    ax1.tick_params(colors=TEXT)
    ax1.legend(facecolor=PANEL, labelcolor=TEXT, fontsize=9)
    for s in ax1.spines.values():
        s.set_color("#334155")

    umbrales = list(UMBRAL_COLORS.keys())
    probs = [a["probs_pico"].get(u, 0) for u in umbrales]
    ax2.set_facecolor(PANEL)
    bars = ax2.bar(
        [f"≥{u}" for u in umbrales],
        probs,
        color=[UMBRAL_COLORS[u] for u in umbrales],
        edgecolor=TEXT,
        linewidth=0.6,
    )
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("% prob.", color=TEXT)
    ax2.set_title("Probabilidades en el PICO (Kalshi)", color=TEXT, fontsize=11)
    ax2.tick_params(colors=TEXT)
    for bar, v in zip(bars, probs):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 2, f"{v}%", ha="center", color=TEXT, fontsize=10)
    for s in ax2.spines.values():
        s.set_color("#334155")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    return _to_bytes(fig)


def grafico_historial(city_id="denver"):
    path = log_csv_path(city_id)
    if not os.path.exists(path):
        return None

    with open(path) as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 2:
        return None

    horas = [r["time"].split("T")[1][:5] for r in rows[-20:]]
    picos = [float(r["pico_f"]) for r in rows[-20:]]
    prob97 = [float(r["prob_97"] or 0) for r in rows[-20:]]

    fig, ax1 = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor(BG)
    ax1.set_facecolor(PANEL)
    ax1.plot(range(len(horas)), picos, "o-", color="#f472b6", linewidth=2, markersize=7, label="Pico °F")
    for u in (95, 97, 98):
        ax1.axhline(u, color=UMBRAL_COLORS.get(u, "#fff"), linestyle=":", alpha=0.4)
    ax1.set_xticks(range(len(horas)))
    ax1.set_xticklabels(horas, rotation=45, color=TEXT, fontsize=9)
    ax1.set_ylabel("Pico (°F)", color=TEXT)
    ax1.set_title("Evolución del pico — WindBorne WM-6", color=TEXT, fontweight="bold")
    ax1.tick_params(colors=TEXT)

    ax2 = ax1.twinx()
    ax2.plot(range(len(horas)), prob97, "s--", color="#38bdf8", alpha=0.8, label="P≥97%")
    ax2.set_ylabel("Prob. ≥97°F", color="#38bdf8")
    ax2.set_ylim(0, 100)
    ax2.tick_params(axis="y", colors="#38bdf8")

    ax1.legend(loc="upper left", facecolor=PANEL, labelcolor=TEXT, fontsize=9)
    for s in ax1.spines.values():
        s.set_color("#334155")
    plt.tight_layout()
    return _to_bytes(fig)