#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: Patrón completo Cola → Retest → Continuación en BTC

FASES:
  1. BTC hace vela con cola larga (rechazo)
  2. Precio se mueve en dirección de la cola (confirma rechazo)
  3. Precio REGRESA a la zona de la cola (retest)
  4. Precio rebota en el retest → continúa

Mide:
  - Cuántas veces ocurre cada fase
  - Tasa de éxito del patrón completo
  - Frecuencia (cada cuántas horas)
  - Duración de cada fase
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests


CSV_URLS = {
    "top100": "https://raw.githubusercontent.com/mattluna3/inspector/main/data/cmc/market_history_top100.csv",
    "emerging": "https://raw.githubusercontent.com/mattluna3/inspector/main/data/cmc/market_history_emerging.csv",
    "201_300": "https://raw.githubusercontent.com/emerging2/inspector2/main/data/cmc/market_history_201_300.csv",
}


# ============================================================
# CONFIGURACIÓN DEL PATRÓN
# ============================================================

# Fase 1: Cola larga
RANGO_COLA_MIN = 0.6        # cola >= 60% del rango total
RANGO_CUERPO_MAX = 0.3      # cuerpo <= 30% del rango total

# Fase 2: Movimiento inicial en dirección de la cola
MOVIMIENTO_INICIAL_MIN = 1.0   # +1% en dirección esperada
VENTANA_INICIAL = 6            # en 6 velas (6h)

# Fase 3: Retest del nivel
TOLERANCIA_RETEST = 0.5     # % de tolerancia del nivel
VENTANA_RETEST = 12         # en 12 velas (12h)

# Fase 4: Continuación
CONTINUACION_MIN = 2.0      # +2% desde el retest
VENTANA_CONTINUACION = 12   # en 12 velas (12h)


# ============================================================
# CARGA
# ============================================================

def cargar_datos():
    dfs = []
    for _, url in CSV_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            df = pd.read_csv(StringIO(r.text))
            dfs.append(df)
        except Exception:
            continue
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["timestamp", "price"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


def construir_velas_1h(g):
    g = g.set_index("timestamp").sort_index()
    agg = g["price"].resample("1h").agg(["first", "max", "min", "last"])
    agg.columns = ["open", "high", "low", "close"]
    return agg.dropna()


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
            "nivel": l,           # el mínimo de la cola
            "nivel_extremo": l,
            "nivel_close": c,
            "esperado": "subida",
        }
    if cola_sup / rango >= RANGO_COLA_MIN:
        return {
            "tipo": "superior",
            "magnitud": cola_sup / rango,
            "nivel": h,           # el máximo de la cola
            "nivel_extremo": h,
            "nivel_close": c,
            "esperado": "bajada",
        }
    return None


# ============================================================
# FASES 2-4
# ============================================================

def analizar_patron(velas, idx, cola):
    """
    Analiza las fases 2, 3 y 4 después de la cola en idx.
    """
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
    
    # ═══ FASE 2: Movimiento inicial en dirección esperada ═══
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
    
    # ═══ FASE 3: Retest del nivel de la cola ═══
    fin_fase3 = min(idx_fin_fase2 + VENTANA_RETEST, n)
    if fin_fase3 <= idx_fin_fase2:
        return resultado
    
    ventana3 = velas.iloc[idx_fin_fase2:fin_fase3]
    if ventana3.empty:
        return resultado
    
    # Buscar retest del nivel
    for j, (ts, row) in enumerate(ventana3.iterrows()):
        if esperado == "subida":
            # Retest = precio regresa cerca del mínimo de la cola
            distancia = abs(row["low"] - nivel) / nivel * 100
        else:
            # Retest = precio regresa cerca del máximo de la cola
            distancia = abs(row["high"] - nivel) / nivel * 100
        
        if distancia <= TOLERANCIA_RETEST:
            resultado["fase3_retest"] = True
            resultado["fase3_velas"] = j + 1
            idx_retest = idx_fin_fase2 + j
            precio_retest = row["close"]
            break
    
    if not resultado["fase3_retest"]:
        return resultado
    
    # ═══ FASE 4: Continuación tras retest ═══
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
    print("🔬 TEST: Patrón Cola → Movimiento → Retest → Continuación")
    print("=" * 90)
    print(f"\nConfiguración:")
    print(f"  Fase 1 — Cola: ≥{RANGO_COLA_MIN*100:.0f}% del rango, cuerpo ≤{RANGO_CUERPO_MAX*100:.0f}%")
    print(f"  Fase 2 — Movimiento inicial: ≥{MOVIMIENTO_INICIAL_MIN}% en {VENTANA_INICIAL}h")
    print(f"  Fase 3 — Retest: ±{TOLERANCIA_RETEST}% del nivel en {VENTANA_RETEST}h")
    print(f"  Fase 4 — Continuación: ≥{CONTINUACION_MIN}% en {VENTANA_CONTINUACION}h")
    
    df = cargar_datos()
    print(f"\n📊 {len(df):,} filas | {df['symbol'].nunique()} monedas")
    
    btc = df[df["symbol"] == "BTC"].sort_values("timestamp")
    print(f"📊 BTC: {len(btc)} snapshots")
    
    if len(btc) < 200:
        print("⚠️ Pocos datos")
        return
    
    velas = construir_velas_1h(btc)
    print(f"📊 Velas 1h: {len(velas)}")
    
    # Analizar cada vela
    resultados = []
    for i in range(len(velas) - VENTANA_INICIAL - VENTANA_RETEST - VENTANA_CONTINUACION):
        row = velas.iloc[i]
        cola = detectar_cola(row)
        if not cola:
            continue
        
        patron = analizar_patron(velas, i, cola)
        
        resultados.append({
            "timestamp": velas.index[i],
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
    
    print(f"\n{'='*90}")
    print(f"📊 RESULTADOS: {total} colas largas detectadas")
    print(f"{'='*90}")
    
    # ═══ Análisis por fase ═══
    print(f"\n📈 ANÁLISIS POR FASE (de {total} colas):")
    print("-" * 90)
    
    # Fase 1: siempre cierta (son las colas detectadas)
    print(f"  Fase 1 — Colas detectadas:                {total:>5} (100.0%)")
    
    # Fase 2: movimiento inicial
    f2 = rdf["fase2_mov"].sum()
    print(f"  Fase 2 — Movimiento inicial en dirección: {f2:>5} ({f2/total*100:>5.1f}%)")
    
    # Fase 3: retest
    f3 = rdf["fase3_retest"].sum()
    f3_de_f2 = rdf[rdf["fase2_mov"]]["fase3_retest"].sum() if f2 > 0 else 0
    print(f"  Fase 3 — Retest del nivel:                {f3:>5} ({f3/total*100:>5.1f}%)")
    if f2 > 0:
        print(f"           (de las que tuvieron Fase 2:     {f3_de_f2:>5} ({f3_de_f2/f2*100:>5.1f}%))")
    
    # Fase 4: continuación
    f4 = rdf["fase4_cont"].sum()
    f4_de_f3 = rdf[rdf["fase3_retest"]]["fase4_cont"].sum() if f3 > 0 else 0
    print(f"  Fase 4 — Continuación tras retest:        {f4:>5} ({f4/total*100:>5.1f}%)")
    if f3 > 0:
        print(f"           (de las que llegaron a Fase 3:   {f4_de_f3:>5} ({f4_de_f3/f3*100:>5.1f}%))")
    
    # Patrón completo
    completo = rdf["completo"].sum()
    print(f"\n  🎯 PATRÓN COMPLETO (todas las fases):     {completo:>5} ({completo/total*100:>5.1f}%)")
    
    # ═══ Por tipo de cola ═══
    print(f"\n{'='*90}")
    print(f"📊 POR TIPO DE COLA")
    print(f"{'='*90}")
    
    for tipo in ["inferior", "superior"]:
        sub = rdf[rdf["tipo"] == tipo]
        if sub.empty:
            continue
        n = len(sub)
        comp = sub["completo"].sum()
        print(f"\n  Cola {tipo.upper()} ({n} casos):")
        print(f"    Fase 2 (mov inicial):  {sub['fase2_mov'].sum():>3} ({sub['fase2_mov'].sum()/n*100:.1f}%)")
        print(f"    Fase 3 (retest):       {sub['fase3_retest'].sum():>3} ({sub['fase3_retest'].sum()/n*100:.1f}%)")
        print(f"    Fase 4 (continuación): {sub['fase4_cont'].sum():>3} ({sub['fase4_cont'].sum()/n*100:.1f}%)")
        print(f"    Patrón completo:       {comp:>3} ({comp/n*100:.1f}%)")
        if comp > 0:
            print(f"    → Movimiento prom. Fase 4: {sub[sub['fase4_cont']]['fase4_pct'].mean():+.2f}%")
    
    # ═══ Frecuencia ═══
    print(f"\n{'='*90}")
    print(f"⏰ FRECUENCIA")
    print(f"{'='*90}")
    
    if len(rdf) > 1:
        duracion_h = (rdf["timestamp"].max() - rdf["timestamp"].min()).total_seconds() / 3600
        colas_por_hora = len(rdf) / duracion_h
        completo_por_hora = completo / duracion_h
        
        print(f"\n  Período analizado: {duracion_h:.0f} horas ({duracion_h/24:.1f} días)")
        print(f"  Colas detectadas: {len(rdf)}")
        print(f"  → Frecuencia: 1 cola cada {1/colas_por_hora:.1f}h (aprox)")
        
        if completo > 0:
            print(f"\n  Patrones completos: {completo}")
            print(f"  → Frecuencia: 1 patrón completo cada {1/completo_por_hora:.1f}h")
            print(f"  → Es decir: ~{completo_por_hora*24:.1f} patrones por día")
    
    # ═══ Duración de fases ═══
    print(f"\n{'='*90}")
    print(f"⏱️ DURACIÓN DE FASES (promedio)")
    print(f"{'='*90}")
    
    if f3 > 0:
        velas_retest = rdf[rdf["fase3_retest"]]["fase3_velas"]
        print(f"\n  Fase 2 → Fase 3 (retest):")
        print(f"    Promedio: {velas_retest.mean():.1f}h")
        print(f"    Mediana:  {velas_retest.median():.1f}h")
        print(f"    Rango:    {velas_retest.min()}h a {velas_retest.max()}h")
    
    # ═══ Últimas 15 colas ═══
    print(f"\n{'='*90}")
    print(f"📋 ÚLTIMAS 15 COLAS")
    print(f"{'='*90}")
    
    print(f"\n{'FECHA':<18} {'TIPO':<10} {'F2':>4} {'F3':>4} {'F4':>4} {'COMPLETO':>9} {'MOV F4':>8}")
    print("-" * 90)
    
    for _, r in rdf.tail(15).iterrows():
        fecha = r["timestamp"].strftime("%m-%d %H:%M")
        f2 = "✅" if r["fase2_mov"] else "❌"
        f3 = "✅" if r["fase3_retest"] else "❌"
        f4 = "✅" if r["fase4_cont"] else "❌"
        comp = "🎯 SÍ" if r["completo"] else "—"
        mov = f"{r['fase4_pct']:+.2f}%" if r["fase4_cont"] else "—"
        print(f"{fecha:<18} {r['tipo']:<10} {f2:>4} {f3:>4} {f4:>4} {comp:>9} {mov:>8}")
    
    # ═══ VEREDICTO ═══
    print(f"\n{'='*90}")
    print(f"🎯 VEREDICTO")
    print(f"{'='*90}")
    
    if total > 0:
        tasa_completa = completo / total * 100
        if f3 > 0:
            tasa_f4_de_f3 = f4 / f3 * 100
        else:
            tasa_f4_de_f3 = 0
        
        print(f"\n  Probabilidad de patrón completo (desde cola): {tasa_completa:.1f}%")
        print(f"  Probabilidad de continuación (si hay retest): {tasa_f4_de_f3:.1f}%")
        
        if tasa_f4_de_f3 > 60:
            print(f"\n  ✅ EL PATRÓN FUNCIONA")
            print(f"     Cuando hay cola + movimiento + retest → continuación")
            print(f"     Tasa de acierto: {tasa_f4_de_f3:.1f}%")
        elif tasa_f4_de_f3 > 50:
            print(f"\n  🟡 SEÑAL MODERADA")
            print(f"     Utilizable como filtro pero no como señal única")
        else:
            print(f"\n  ❌ NO HAY PATRÓN CLARO")
            print(f"     Las colas no preceden movimientos predecibles")


if __name__ == "__main__":
    main()
