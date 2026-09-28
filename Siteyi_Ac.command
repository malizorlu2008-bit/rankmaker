#!/bin/bash
cd "$(dirname "$0")"
PORT=8743

if ! lsof -i :$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  (python3 -m http.server $PORT >/dev/null 2>&1 &)
  sleep 1
fi

open "http://localhost:$PORT"
echo "RankMaker açıldı: http://localhost:$PORT"
echo "Bu pencereyi kapatabilirsin, sunucu arka planda çalışmaya devam eder."
echo "Durdurmak istersen: pkill -f 'http.server $PORT'"
sleep 2
