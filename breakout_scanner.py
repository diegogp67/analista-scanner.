#!/usr/bin/env python3
"""Escaner de rupturas (breakouts) con alertas por Telegram."""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",
    "SPY", "QQQ",
    "BTC-USD", "ETH-USD",
    "EURUSD=X", "GC=F",
]
INTERVAL = "1d"
PERIOD = "1y"
LOOKBACK = 20
VOL_MULT = 1.5
ATR_N = 14
ATR_STOP = 1.5
RR = 2.0
CHECK_EVERY = 300

STATE_FILE = "alertas_enviadas.json"


def cargar_estado() -> set:
    try:
        with open(STATE_FILE) as f:
            return set(tuple(x) for x in json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def guardar_estado(estado: set):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(estado)[-500:], f)


alertas_enviadas = cargar_estado()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def analizar(symbol: str, interval: str, period: str):
    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    df = df.dropna(subset=["High", "Low", "Close"])
    if len(df) < max(LOOKBACK, ATR_N) + 2:
        return None

    resistencia = df["High"].shift(1).rolling(LOOKBACK).max()
    soporte = df["Low"].shift(1).rolling(LOOKBACK).min()
    vol_prom = df["Volume"].shift(1).rolling(LOOKBACK).mean()
    df["ATR"] = atr(df, ATR_N)

    ultima = df.iloc[-1]
    ts = df.index[-1]
    res, sop, vp = resistencia.iloc[-1], soporte.iloc[-1], vol_prom.iloc[-1]
    cierre, vol, a = ultima["Close"], ultima["Volume"], ultima["ATR"]

    vol_ok = True if (pd.isna(vp) or vp == 0) else vol >= VOL_MULT * vp

    if cierre > res and vol_ok:
        stop = cierre - ATR_STOP * a
        objetivo = cierre + RR * (cierre - stop)
        return dict(symbol=symbol, tipo="ALCISTA", ts=ts, cierre=cierre,
                    nivel=res, stop=stop, objetivo=objetivo,
                    vol_ratio=(vol / vp if vp else float("nan")))
    if cierre < sop and vol_ok:
        stop = cierre + ATR_STOP * a
        objetivo = cierre - RR * (stop - cierre)
        return dict(symbol=symbol, tipo="BAJISTA", ts=ts, cierre=cierre,
                    nivel=sop, stop=stop, objetivo=objetivo,
                    vol_ratio=(vol / vp if vp else float("nan")))
    return None


def formatear(s: dict) -> str:
    emoji = "🟢" if s["tipo"] == "ALCISTA" else "🔴"
    return (
        f"{emoji} RUPTURA {s['tipo']} - {s['symbol']}\n"
        f"Cierre: {s['cierre']:.4f} (nivel roto: {s['nivel']:.4f})\n"
        f"Volumen: {s['vol_ratio']:.1f}x el promedio\n"
        f"Stop sugerido: {s['stop']:.4f} | Objetivo {RR:.0f}R: {s['objetivo']:.4f}\n"
        f"Vela: {s['ts']}"
    )


def notificar(msg: str):
    print("\n" + msg + "\a")
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": msg},
                timeout=10,
            )
        except requests.RequestException as e:
            print(f"[!] No se pudo enviar a Telegram: {e}")


def escanear(symbols, interval, period):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Escaneando {len(symbols)} simbolos ({interval})...")
    encontrados = 0
    for sym in symbols:
        try:
            senal = analizar(sym, interval, period)
        except Exception as e:
            print(f"[!] {sym}: {e}")
            continue
        if not senal:
            continue
        clave = (senal["symbol"], str(senal["ts"]), senal["tipo"])
        if clave in alertas_enviadas:
            continue
        alertas_enviadas.add(clave)
        notificar(formatear(senal))
        encontrados += 1
    guardar_estado(alertas_enviadas)
    if encontrados == 0:
        print("Sin rupturas nuevas.")


def main():
    global LOOKBACK, VOL_MULT
    p = argparse.ArgumentParser(description="Escaner de rupturas")
    p.add_argument("--symbols", help="Lista separada por comas (ej: AAPL,BTC-USD)")
    p.add_argument("--interval", default=INTERVAL)
    p.add_argument("--period", default=PERIOD)
    p.add_argument("--lookback", type=int, default=LOOKBACK)
    p.add_argument("--vol", type=float, default=VOL_MULT)
    p.add_argument("--loop", action="store_true")
    args = p.parse_args()

    LOOKBACK, VOL_MULT = args.lookback, args.vol
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else WATCHLIST

    if not args.loop:
        escanear(symbols, args.interval, args.period)
        return
    try:
        while True:
            escanear(symbols, args.interval, args.period)
            time.sleep(CHECK_EVERY)
    except KeyboardInterrupt:
        print("\nDetenido.")
        sys.exit(0)


if __name__ == "__main__":
    main()
