"""Spor Toto haftalık tahmin üreticisi.

Akış:
  1. Maç verisini oku (matches.json ya da API-Football/RapidAPI)
  2. Kompakt istatistik JSON'u hazırla (token tasarrufu için)
  3. Google Gemini'den (ücretsiz katman) structured output ile tahmin al
  4. index.html olarak render et
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

MODEL_ID = "gemini-3.6-flash"
MATCHES_FILE = os.environ.get("MATCHES_FILE", "matches.json")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "index.html")

RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
RAPIDAPI_BASE_URL = f"https://{RAPIDAPI_HOST}/v3"

VALID_PREDICTIONS = {"1", "X", "2", "1X", "X2", "12"}


# ---------------------------------------------------------------------------
# 1. Veri kaynağı
# ---------------------------------------------------------------------------

def load_matches_from_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_fixture_odds(session: requests.Session, headers: dict, fixture_id: int) -> dict:
    resp = session.get(
        f"{RAPIDAPI_BASE_URL}/odds",
        headers=headers,
        params={"fixture": fixture_id},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("response", [])
    if not data:
        return {}
    bookmaker = data[0]["bookmakers"][0]
    match_winner = next(
        (bet for bet in bookmaker["bets"] if bet["name"] == "Match Winner"), None
    )
    if not match_winner:
        return {}
    odds = {v["value"]: float(v["odd"]) for v in match_winner["values"]}
    return {"1": odds.get("Home"), "X": odds.get("Draw"), "2": odds.get("Away")}


def fetch_team_form(session: requests.Session, headers: dict, team_id: int, league_id: int, season: int) -> str:
    resp = session.get(
        f"{RAPIDAPI_BASE_URL}/teams/statistics",
        headers=headers,
        params={"team": team_id, "league": league_id, "season": season},
        timeout=15,
    )
    resp.raise_for_status()
    form = resp.json().get("response", {}).get("form", "")
    return form[-5:] if form else "?????"


def fetch_h2h(session: requests.Session, headers: dict, home_id: int, away_id: int, home_name: str) -> str:
    resp = session.get(
        f"{RAPIDAPI_BASE_URL}/fixtures/headtohead",
        headers=headers,
        params={"h2h": f"{home_id}-{away_id}", "last": 5},
        timeout=15,
    )
    resp.raise_for_status()
    results = []
    for fixture in resp.json().get("response", []):
        home_goals = fixture["goals"]["home"]
        away_goals = fixture["goals"]["away"]
        fixture_home = fixture["teams"]["home"]["name"]
        if home_goals == away_goals:
            results.append("X")
        elif (home_goals > away_goals) == (fixture_home == home_name):
            results.append("1")
        else:
            results.append("2")
    return "-".join(results) if results else "veri yok"


def load_matches_from_api(rapidapi_key: str, fixtures_config: list) -> dict:
    """fixtures_config: [{"fixture_id", "league_id", "season", "home_id", "away_id",
    "home_team", "away_team", "league", "kickoff"}, ...] - dış kaynaktan (ör. bir config
    dosyasından) sağlanmalıdır; API-Football fixture aramasını burada yapmıyoruz."""
    headers = {"x-rapidapi-key": rapidapi_key, "x-rapidapi-host": RAPIDAPI_HOST}
    session = requests.Session()
    matches = []
    for cfg in fixtures_config:
        odds = fetch_fixture_odds(session, headers, cfg["fixture_id"])
        home_form = fetch_team_form(session, headers, cfg["home_id"], cfg["league_id"], cfg["season"])
        away_form = fetch_team_form(session, headers, cfg["away_id"], cfg["league_id"], cfg["season"])
        h2h = fetch_h2h(session, headers, cfg["home_id"], cfg["away_id"], cfg["home_team"])
        matches.append({
            "match_id": f"m{cfg['fixture_id']}",
            "league": cfg["league"],
            "home_team": cfg["home_team"],
            "away_team": cfg["away_team"],
            "kickoff": cfg["kickoff"],
            "odds": odds,
            "home_form_last5": home_form,
            "away_form_last5": away_form,
            "h2h_last5": h2h,
        })
    return {"week": datetime.now(timezone.utc).isoformat(), "matches": matches}


def load_matches() -> dict:
    rapidapi_key = os.environ.get("RAPIDAPI_KEY")
    fixtures_config_path = os.environ.get("FIXTURES_CONFIG")
    if rapidapi_key and fixtures_config_path and os.path.exists(fixtures_config_path):
        with open(fixtures_config_path, "r", encoding="utf-8") as f:
            fixtures_config = json.load(f)
        return load_matches_from_api(rapidapi_key, fixtures_config)
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
    "Sen bir futbol istatistik analistisin. Sana verilen maçlar için oranlara, "
    "son 5 maçlık form durumuna ve H2H (head-to-head) geçmişine dayanarak "
    "MS 1X2 tercihi (1, X, 2, 1X, X2 veya 12) öner. Yorumların kısa (en fazla "
    "2 cümle), somut istatistiklere dayalı ve net olsun. Spekülasyon yapma, "
    "sadece verilen verilerdeki gerekçelere atıf yap. confidence 1-10 arası, "
    "10 en yüksek güven."
)


def get_predictions(client: genai.Client, compact_matches: list) -> dict:
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
