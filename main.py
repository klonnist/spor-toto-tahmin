"""Spor Toto haftalık tahmin üreticisi.

Akış:
  1. Maç verisini oku (matches.json ya da The Odds API üzerinden gerçek fikstür/oran)
  2. Kompakt istatistik JSON'u hazırla (token tasarrufu için)
  3. Google Gemini'den (ücretsiz katman) structured output ile tahmin al
  4. index.html olarak render et
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

MODEL_ID = "gemini-3.6-flash"
MATCHES_FILE = os.environ.get("MATCHES_FILE", "matches.json")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "index.html")

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"

# the-odds-api.com sport key'leri (https://the-odds-api.com/sports-odds-data/sports-apis.html
# adresinden doğrulanmıştır). Form/H2H bu API'de yok; sadece gerçek fikstür + gerçek oran sağlar.
LEAGUE_SPORT_KEYS = {
    "soccer_turkey_super_league": "Süper Lig",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
}
MAX_MATCHES = int(os.environ.get("MAX_MATCHES", "15"))
FIXTURE_WINDOW_DAYS = int(os.environ.get("FIXTURE_WINDOW_DAYS", "7"))

VALID_PREDICTIONS = {"1", "X", "2", "1X", "X2", "12"}


# ---------------------------------------------------------------------------
# 1. Veri kaynağı
# ---------------------------------------------------------------------------

def load_matches_from_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_1x2_odds(event: dict) -> dict:
    """the-odds-api.com'un h2h (moneyline) pazarından ilk kullanılabilir bookmaker'ın
    1/X/2 oranlarını çıkarır."""
    home_name = event.get("home_team")
    away_name = event.get("away_team")
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            odds = {}
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                if name == home_name:
                    odds["1"] = price
                elif name == away_name:
                    odds["2"] = price
                elif isinstance(name, str) and name.lower() == "draw":
                    odds["X"] = price
            if odds:
                return odds
    return {}


def discover_fixtures_with_odds(session: requests.Session, api_key: str, sport_keys: dict,
                                 date_from: str, date_to: str) -> list:
    """Verilen liglerde belirtilen tarih aralığındaki gerçek fikstürleri + gerçek oranları
    the-odds-api.com'dan toplar, tarihe göre sıralar."""
    matches = []
    for sport_key, league_name in sport_keys.items():
        resp = session.get(
            f"{ODDS_API_BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": api_key,
                "regions": "eu,uk",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": date_from,
                "commenceTimeTo": date_to,
            },
            timeout=15,
        )
        if not resp.ok:
            print(
                f"UYARI: {league_name} ({sport_key}) fikstürü alınamadı "
                f"({resp.status_code}): {resp.text[:300]}",
                file=sys.stderr,
            )
            continue
        for event in resp.json():
            matches.append({
                "match_id": f"m{event['id']}",
                "league": league_name,
                "home_team": event["home_team"],
                "away_team": event["away_team"],
                "kickoff": event["commence_time"],
                "odds": extract_1x2_odds(event),
                # the-odds-api.com form/H2H sağlamıyor; dürüstçe "veri yok" gösterilir.
                "home_form_last5": "veri yok",
                "away_form_last5": "veri yok",
                "h2h_last5": "veri yok",
            })
    matches.sort(key=lambda m: m["kickoff"])
    return matches


def load_matches_from_api(odds_api_key: str) -> dict:
    session = requests.Session()
    today = datetime.now(timezone.utc)
    date_from = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (today + timedelta(days=FIXTURE_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    matches = discover_fixtures_with_odds(
        session, odds_api_key, LEAGUE_SPORT_KEYS, date_from, date_to
    )[:MAX_MATCHES]
    return {"week": today.isoformat(), "matches": matches}


def load_matches() -> dict:
    odds_api_key = os.environ.get("ODDS_API_KEY")
    if odds_api_key:
        bulletin = load_matches_from_api(odds_api_key)
        if bulletin["matches"]:
            return bulletin
        print("UYARI: The Odds API'den maç bulunamadı, matches.json şablonuna dönülüyor.", file=sys.stderr)
    return load_matches_from_file(MATCHES_FILE)


# ---------------------------------------------------------------------------
# 2. Kompakt payload (token tasarrufu)
# ---------------------------------------------------------------------------

def build_compact_payload(bulletin: dict) -> list:
    compact = []
    for m in bulletin["matches"]:
        compact.append({
            "id": m["match_id"],
            "home": m["home_team"],
            "away": m["away_team"],
            "odds": m["odds"],
            "home_form": m["home_form_last5"],
            "away_form": m["away_form_last5"],
            "h2h": m["h2h_last5"],
        })
    return compact


# ---------------------------------------------------------------------------
# 3. Google Gemini ile tahmin üretimi (structured output)
# ---------------------------------------------------------------------------

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "match_id": {"type": "string"},
                    "prediction": {
                        "type": "string",
                        "enum": sorted(VALID_PREDICTIONS),
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "comment": {"type": "string"},
                },
                "required": ["match_id", "prediction", "confidence", "comment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["predictions"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Sen bir futbol istatistik analistisin. Sana verilen maçlar için oranlara, ve "
    "varsa son 5 maçlık form durumuna ve H2H (head-to-head) geçmişine dayanarak "
    "MS 1X2 tercihi (1, X, 2, 1X, X2 veya 12) öner. form/h2h alanları 'veri yok' ise "
    "bunları uydurma, sadece oranlara dayalı bir değerlendirme yap. Yorumların kısa "
    "(en fazla 2 cümle), somut verilere dayalı ve net olsun. Spekülasyon yapma, "
    "sadece verilen verilerdeki gerekçelere atıf yap. confidence 1-10 arası, "
    "10 en yüksek güven; veri az olduğunda güveni düşük tut."
)


def get_predictions(client: genai.Client, compact_matches: list, max_retries: int = 3) -> dict:
    last_error = None
    for attempt in range(max_retries):
        if attempt > 0:
            delay = 2 ** attempt
            print(f"UYARI: Gemini geçici hata verdi, {delay}s sonra tekrar denenecek "
                  f"({attempt}/{max_retries - 1}).", file=sys.stderr)
            time.sleep(delay)
        try:
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=(
                    "Aşağıdaki maçlar için tahmin üret:\n\n"
                    + json.dumps(compact_matches, ensure_ascii=False)
                ),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_json_schema=PREDICTION_SCHEMA,
                ),
            )
            data = json.loads(response.text)
            return {p["match_id"]: p for p in data["predictions"]}
        except genai_errors.ServerError as e:
            last_error = e
    raise last_error


# ---------------------------------------------------------------------------
# 4. HTML üretimi
# ---------------------------------------------------------------------------

def confidence_color(confidence: int) -> str:
    if confidence >= 8:
        return "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
    if confidence >= 5:
        return "text-amber-400 border-amber-500/40 bg-amber-500/10"
    return "text-rose-400 border-rose-500/40 bg-rose-500/10"


def render_card(match: dict, prediction: dict) -> str:
    confidence = prediction["confidence"]
    color_classes = confidence_color(confidence)
    odds = match["odds"]
    kickoff = match.get("kickoff", "")
    try:
        kickoff_display = datetime.fromisoformat(kickoff).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        kickoff_display = kickoff

    return f"""
    <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-5 shadow-lg hover:border-slate-500 transition-colors">
      <div class="flex justify-between items-start mb-3">
        <span class="text-xs uppercase tracking-wide text-slate-400">{match.get('league', '')}</span>
        <span class="text-xs text-slate-500">{kickoff_display}</span>
      </div>
      <div class="flex items-center justify-between mb-4">
        <span class="font-semibold text-slate-100 text-right flex-1">{match['home_team']}</span>
        <span class="mx-3 text-slate-500 text-sm">vs</span>
        <span class="font-semibold text-slate-100 flex-1">{match['away_team']}</span>
      </div>
      <div class="flex gap-2 mb-4 text-xs text-slate-400">
        <span class="bg-slate-900/60 rounded px-2 py-1">1: {odds.get('1', '-')}</span>
        <span class="bg-slate-900/60 rounded px-2 py-1">X: {odds.get('X', '-')}</span>
        <span class="bg-slate-900/60 rounded px-2 py-1">2: {odds.get('2', '-')}</span>
      </div>
      <div class="flex items-center justify-between mb-3">
        <span class="inline-flex items-center rounded-lg border px-3 py-1 text-lg font-bold {color_classes}">
          {prediction['prediction']}
        </span>
        <div class="text-right">
          <div class="text-xs text-slate-400">Güven Skoru</div>
          <div class="font-bold {color_classes.split()[0]}">{confidence}/10</div>
        </div>
      </div>
      <p class="text-sm text-slate-300 leading-relaxed border-t border-slate-700 pt-3">
        {prediction['comment']}
      </p>
    </div>
    """


def render_html(bulletin: dict, predictions: dict) -> str:
    cards = "\n".join(
        render_card(m, predictions[m["match_id"]])
        for m in bulletin["matches"]
        if m["match_id"] in predictions
    )
    generated_at = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spor Toto Haftalık Tahminler</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">
  <header class="border-b border-slate-800 bg-slate-900/50">
    <div class="max-w-6xl mx-auto px-4 py-6">
      <h1 class="text-2xl md:text-3xl font-bold">⚽ Spor Toto Haftalık Tahminler</h1>
      <p class="text-slate-400 text-sm mt-1">Hafta: {bulletin.get('week', '-')} · Oluşturulma: {generated_at}</p>
      <p class="text-slate-500 text-xs mt-2">
        Tahminler istatistiksel bir modelin çıktısıdır, yatırım/bahis tavsiyesi değildir.
      </p>
    </div>
  </header>
  <main class="max-w-6xl mx-auto px-4 py-8">
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      {cards}
    </div>
  </main>
  <footer class="text-center text-slate-600 text-xs py-8">
    Google Gemini ({MODEL_ID}) ile üretildi · Sorumlu oyun oynayın.
  </footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        print("HATA: GEMINI_API_KEY ortam değişkeni tanımlı değil.", file=sys.stderr)
        return 1

    bulletin = load_matches()
    if not bulletin.get("matches"):
        print("HATA: Maç verisi bulunamadı.", file=sys.stderr)
        return 1

    compact_matches = build_compact_payload(bulletin)
    client = genai.Client(api_key=gemini_api_key)

    try:
        predictions = get_predictions(client, compact_matches)
    except genai_errors.APIError as e:
        print(f"HATA: Gemini API isteği başarısız oldu ({e.code}): {e.message}", file=sys.stderr)
        return 1

    html = render_html(bulletin, predictions)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"{len(predictions)} maç için tahmin üretildi -> {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
