#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: Patrón Cola → Movimiento → Retest → Continuación en BTC

Usa el cache de OKX (data/cache/BTC.json) que tiene 7 días de velas.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests


# ============================================================
# CONFIGURACIÓN DEL PATRÓN
# ============================================================

RANGO_COLA_MIN = 0.6
RANGO_CUERPO_MAX = 0.3

MOVIMIENTO_INICIAL_MIN = 1.0
VENTANA_INICIAL = 6

TOLERANCIA_RETEST = 0.5
VENTANA_RETEST = 12

CONTINUACION_MIN = 2.0
VENTANA_CONTINUACION = 12

# Cache de OKX (BTC)
CACHE_BTC_URL = (
    "https://raw.githubusercontent.com/Interpage188/"
    "interpage/main/data/cache/BTC.json"
)


# ============================================================
# CARGA — Desde cache de OKX
# ============================================================

def cargar_velas_btc():
    """Carga las velas de BTC desde el cache de OKX."""
    try:
        r = requests.get(CACHE_BTC_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"❌ Error cargando cache: {e}")
        sys.exit(1)
    
    # Buscar velas_1h
    velas_raw = data.get("velas_1h", [])
    if not velas_raw:
        print("❌ No hay velas_1h en el cache")
        sys.exit(1)
    
    print(f"📊 Velas 1h en cache: {len(velas_raw)}")
    
    # Convertir a DataFrame
    velas = []
    for v in velas_raw:
        try:
            velas.append({
                "timestamp": pd.to_datetime(int(v["ts"]), unit="ms", utc=True),
                "open": float(v["o"]),
                "high": float(v["h"]),
                "low": float(v["l"]),
                "close": float(v["c"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    
    if not velas:
        print("❌ No se pudieron parsear las velas")
        sys.exit(1)
    
    df = pd.DataFrame(velas)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ============================================================
# FASE 1: Detectar cola larga
# ============================================================

def detectar_cola(row):
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rango = h - l
    if rango <= 0:
        return None
    
    cuerpo = abs(c - o)
    cola_sup = h - max(o, c)
    cola_inf = min(o, c) - l
    ratio_cuerpo = cuerpo / rango
    
    if ratio_cuerpo > RANGO_CUERPO_MAX:
        return None
    
    if cola_inf / rango >= RANGO_COLA_MIN:
        return {
            "tipo": "inferior",
            "magnitud": cola_inf / rango,
            "nivel": l,
            "nivel_close": c,
            "esperado": "subida",
        }
    if cola_sup / rango >= RANGO_COLA_MIN:
        return {
            "tipo": "superior",
            "magnitud": cola_sup / rango,
            "nivel": h,
            "nivel_close": c,
            "esperado": "bajada",
        }
    return None


# ============================================================
# FASES 2-4
# ============================================================

def analizar_patron(velas, idx, cola):
    n = len(velas)
    esperado = cola["esperado"]
    nivel = cola["nivel"]
    precio_base = cola["nivel_close"]
    
    resultado = {
        "fase2_movimiento": False,
        "fase2_pct": 0.0,
        "fase3_retest": False,
        "fase3_velas": 0,
        "fase4_continuacion": False,
        "fase4_pct": 0.0,
        "patron_completo": False,
    }
    
    # FASE 2
    fin_fase2 = min(idx + 1 + VENTANA_INICIAL, n)
    if fin_fase2 <= idx + 1:
        return resultado
    
    ventana2 = velas.iloc[idx+1:fin_fase2]
    if ventana2.empty:
        return resultado
    
    if esperado == "subida":
        p_extremo = ventana2["high"].max()
        pct = ((p_extremo - precio_base) / precio_base) * 100
    else:
        p_extremo = ventana2["low"].min()
        pct = ((precio_base - p_extremo) / precio_base) * 100
    
    resultado["fase2_pct"] = pct
    
    if pct < MOVIMIENTO_INICIAL_MIN:
        return resultado
    
    resultado["fase2_movimiento"] = True
    idx_fin_fase2 = idx + 1 + VENTANA_INICIAL
    
    # FASE 3
    fin_fase3 = min(idx_fin_fase2 + VENTANA_RETEST, n)
    if fin_fase3 <= idx_fin_fase2:
        return resultado
    
    ventana3 = velas.iloc[idx_fin_fase2:fin_fase3]
    if ventana3.empty:
        return resultado
    
    idx_retest = None
    precio_retest = None
    for j in range(len(ventana3)):
        row = ventana3.iloc[j]
        if esperado == "subida":
            distancia = abs(row["low"] - nivel) / nivel * 100
        else:
            distancia = abs(row["high"] - nivel) / nivel * 100
        
        if distancia <= TOLERANCIA_RETEST:
            resultado["fase3_retest"] = True
            resultado["fase3_velas"] = j + 1
            idx_retest = idx_fin_fase2 + j
            precio_retest = row["close"]
            break
    
    if not resultado["fase3_retest"]:
        return resultado
    
    # FASE 4
    fin_fase4 = min(idx_retest + 1 + VENTANA_CONTINUACION, n)
    if fin_fase4 <= idx_retest + 1:
        return resultado
    
    ventana4 = velas.iloc[idx_retest+1:fin_fase4]
    if ventana4.empty:
        return resultado
    
    if esperado == "subida":
        p_extremo = ventana4["high"].max()
        pct = ((p_extremo - precio_retest) / precio_retest) * 100
    else:
        p_extremo = ventana4["low"].min()
        pct = ((precio_retest - p_extremo) / precio_retest) * 100
    
    resultado["fase4_pct"] = pct
    
    if pct >= CONTINUACION_MIN:
        resultado["fase4_continuacion"] = True
        resultado["patron_completo"] = True
    
    return resultado


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 90)
    print("🔬 TEST: Patrón Cola → Movimiento → Retest → Continuación (BTC)")
    print("=" * 90)
    print(f"\nConfiguración:")
    print(f"  Fase 1 — Cola: ≥{RANGO_COLA_MIN*100:.0f}% del rango, cuerpo ≤{RANGO_CUERPO_MAX*100:.0f}%")
    print(f"  Fase 2 — Movimiento inicial: ≥{MOVIMIENTO_INICIAL_MIN}% en {VENTANA_INICIAL}h")
    print(f"  Fase 3 — Retest: ±{TOLERANCIA_RETEST}% del nivel en {VENTANA_RETEST}h")
    print(f"  Fase 4 — Continuación: ≥{CONTINUACION_MIN}% en {VENTANA_CONTINUACION}h")
    
    velas = cargar_velas_btc()
    print(f"📊 Velas útiles: {len(velas)}")
    
    if len(velas) < 100:
        print(f"⚠️ Solo {len(velas)} velas — necesitamos 100+ para análisis útil")
        print("   El cache de OKX tiene ~168 velas de 1h (7 días)")
    
    # Analizar cada vela
    resultados = []
    for i in range(len(velas) - VENTANA_INICIAL - VENTANA_RETEST - VENTANA_CONTINUACION):
        row = velas.iloc[i]
        cola = detectar_cola(row)
        if not cola:
            continue
        
        patron = analizar_patron(velas, i, cola)
        
        resultados.append({
            "timestamp": velas.iloc[i]["timestamp"],
            "tipo": cola["tipo"],
            "magnitud": cola["magnitud"],
            "nivel": cola["nivel"],
            "precio_base": cola["nivel_close"],
            "fase2_mov": patron["fase2_movimiento"],
            "fase2_pct": patron["fase2_pct"],
            "fase3_retest": patron["fase3_retest"],
            "fase3_velas": patron["fase3_velas"],
            "fase4_cont": patron["fase4_continuacion"],
            "fase4_pct": patron["fase4_pct"],
            "completo": patron["patron_completo"],
        })
    
    if not resultados:
        print("\n⚪ No se detectaron colas largas")
        return
    
    rdf = pd.DataFrame(resultados)
    total = len(rdf)
    
    # ═══ Fases (con conversión explícita a int) ═══
    n_f2 = int(rdf["fase2_mov"].sum())
    n_f3 = int(rdf["fase3_retest"].sum())
    n_f4 = int(rdf["fase4_cont"].sum())
    n_completo = int(rdf["completo"].sum())
    
    n_f3_de_f2 = int(rdf[rdf["fase2_mov"]]["fase3_retest"].sum()) if n_f2 > 0 else 0
    n_f4_de_f3 = int(rdf[rdf["fase3_retest"]]["fase4_cont"].sum()) if n_f3 > 0 else 0
    
    print(f"\n{'='*90}")
    print(f"📊 RESULTADOS: {total} colas largas detectadas")
    print(f"{'='*90}\n")
    
    print(f"📈 ANÁLISIS POR FASE (de {total} colas):")
    print("-" * 90)
    print(f"  Fase 1 — Colas detectadas:                {total:>5} (100.0%)")
    print(f"  Fase 2 — Movimiento inicial en dirección: {n_f2:>5} ({n_f2/total*100:>5.1f}%)")
    print(f"  Fase 3 — Retest del nivel:                {n_f3:>5} ({n_f3/total*100:>5.1f}%)")
    if n_f2 > 0:
        print(f"           (de las que tuvieron Fase 2:     {n_f3_de_f2:>5} ({n_f3_de_f2/n_f2*100:>5.1f}%))")
    print(f"  Fase 4 — Continuación tras retest:        {n_f4:>5} ({n_f4/total*100:>5.1f}%)")
    if n_f3 > 0:
        print(f"           (de las que llegaron a Fase 3:   {n_f4_de_f3:>5} ({n_f4_de_f3/n_f3*100:>5.1f}%))")
    print(f"\n  🎯 PATRÓN COMPLETO (todas las fases):     {n_completo:>5} ({n_completo/total*100:>5.1f}%)")
    
    # ═══ Por tipo ═══
    print(f"\n{'='*90}")
    print(f"📊 POR TIPO DE COLA")
    print(f"{'='*90}")
    
    for tipo in ["inferior", "superior"]:
        sub = rdf[rdf["tipo"] == tipo]
        if sub.empty:
            continue
        n_tipo = len(sub)
        n_f2_tipo = int(sub["fase2_mov"].sum())
        n_f3_tipo = int(sub["fase3_retest"].sum())
        n_f4_tipo = int(sub["fase4_cont"].sum())
        n_comp_tipo = int(sub["completo"].sum())
        
        print(f"\n  Cola {tipo.upper()} ({n_tipo} casos):")
        print(f"    Fase 2 (mov inicial):  {n_f2_tipo:>3} ({n_f2_tipo/n_tipo*100:.1f}%)")
        print(f"    Fase 3 (retest):       {n_f3_tipo:>3} ({n_f3_tipo/n_tipo*100:.1f}%)")
        print(f"    Fase 4 (continuación): {n_f4_tipo:>3} ({n_f4_tipo/n_tipo*100:.1f}%)")
        print(f"    Patrón completo:       {n_comp_tipo:>3} ({n_comp_tipo/n_tipo*100:.1f}%)")
        if n_comp_tipo > 0:
            prom = sub[sub["fase4_cont"]]["fase4_pct"].mean()
            print(f"    → Movimiento prom. Fase 4: {prom:+.2f}%")
    
    # ═══ Frecuencia ═══
    print(f"\n{'='*90}")
    print(f"⏰ FRECUENCIA")
    print(f"{'='*90}")
    
    if len(rdf) > 1:
        duracion_h = (rdf["timestamp"].max() - rdf["timestamp"].min()).total_seconds() / 3600
        if duracion_h > 0:
            colas_por_hora = len(rdf) / duracion_h
            print(f"\n  Período analizado: {duracion_h:.0f} horas ({duracion_h/24:.1f} días)")
            print(f"  Colas detectadas: {len(rdf)}")
            print(f"  → Frecuencia: 1 cola cada {1/colas_por_hora:.1f}h")
            
            if n_completo > 0:
                completo_por_hora = n_completo / duracion_h
                print(f"\n  Patrones completos: {n_completo}")
                print(f"  → Frecuencia: 1 patrón cada {1/completo_por_hora:.1f}h")
                print(f"  → ~{completo_por_hora*24:.1f} patrones/día")
    
    # ═══ Duración de fases (usa las variables con nombres únicos) ═══
    print(f"\n{'='*90}")
    print(f"⏱️ DURACIÓN DE FASES")
    print(f"{'='*90}")
    
    if n_f3 > 0:
        velas_retest = rdf[rdf["fase3_retest"]]["fase3_velas"]
        print(f"\n  Fase 2 → Fase 3 (retest):")
        print(f"    Promedio: {velas_retest.mean():.1f}h")
        print(f"    Mediana:  {velas_retest.median():.1f}h")
        print(f"    Rango:    {velas_retest.min()}h a {velas_retest.max()}h")
    else:
        print("\n  ⚪ Sin datos (ninguna cola hizo retest)")
    
    # ═══ Últimas 15 colas (variables con nombres únicos) ═══
    print(f"\n{'='*90}")
    print(f"📋 ÚLTIMAS {min(15, len(rdf))} COLAS")
    print(f"{'='*90}")
    
    print(f"\n{'FECHA':<18} {'TIPO':<10} {'F2':>4} {'F3':>4} {'F4':>4} {'COMPLETO':>9} {'MOV F4':>8}")
    print("-" * 90)
    
    for _, r in rdf.tail(15).iterrows():
        fecha = r["timestamp"].strftime("%m-%d %H:%M")
        f2_icon = "✅" if r["fase2_mov"] else "❌"
        f3_icon = "✅" if r["fase3_retest"] else "❌"
        f4_icon = "✅" if r["fase4_cont"] else "❌"
        comp_icon = "🎯 SÍ" if r["completo"] else "—"
        mov = f"{r['fase4_pct']:+.2f}%" if r["fase4_cont"] else "—"
        print(f"{fecha:<18} {r['tipo']:<10} {f2_icon:>4} {f3_icon:>4} {f4_icon:>4} {comp_icon:>9} {mov:>8}")
    
    # ═══ Veredicto ═══
    print(f"\n{'='*90}")
    print(f"🎯 VEREDICTO")
    print(f"{'='*90}")
    
    if total > 0:
        tasa_completa = n_completo / total * 100
        tasa_f4_de_f3 = (n_f4_de_f3 / n_f3 * 100) if n_f3 > 0 else 0
        
        print(f"\n  Patrón completo (desde cola):       {tasa_completa:.1f}%")
        print(f"  Continuación (si hay retest):        {tasa_f4_de_f3:.1f}%")
        
        if n_f3 < 3:
            print(f"\n  ⚠️ MUESTRA INSUFICIENTE")
            print(f"     Solo {n_f3} casos llegaron a Fase 3")
            print(f"     Necesitas más datos (mínimo 15-20 retests)")
        elif tasa_f4_de_f3 > 60:
            print(f"\n  ✅ EL PATRÓN FUNCIONA")
            print(f"     Cuando hay cola + movimiento + retest → continuación")
        elif tasa_f4_de_f3 > 50:
            print(f"\n  🟡 SEÑAL MODERADA")
            print(f"     Utilizable como filtro, no como señal única")
        else:
            print(f"\n  ❌ NO HAY PATRÓN CLARO")


if __name__ == "__main__":
    main()
