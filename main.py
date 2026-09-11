"""Spor Toto haftalık tahmin üreticisi.

Akış:
  1. Maç verisini oku:
     - Resmi 15 maçlık liste `official_matches.json` dosyasından okunur (bu dosya
       sportoto.gov.tr/spor-toto-listeler adresinden elle ya da bir ekran
       görüntüsünden haftalık olarak güncellenir - bkz. README; site otomatik
       isteklere [curl/requests, Playwright headless dahil] boş tablo döndürerek
       bot koruması uyguluyor, bu yüzden otomatik kazıma yapılmıyor).
     - Her maç için the-odds-api.com'da takım adı eşleştirmesiyle gerçek 1X2 oranı
       ve hangi ligde/kupada oynandığı bulunur.
     - Eşleşen lig football-data.org'un ücretsiz kapsamındaysa (PL/La Liga/Serie A/
       Bundesliga/Ligue 1) gerçek son-5 form ve H2H geçmişi de eklenir.
     - `official_matches.json` yoksa/boşsa matches.json şablonuna dönülür.
  2. Kompakt istatistik JSON'u hazırla (token tasarrufu için)
  3. Google Gemini'den (ücretsiz katman) structured output ile tahmin al
  4. index.html olarak render et
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

MODEL_ID = "gemini-3.6-flash"
FALLBACK_MODEL_ID = "gemini-flash-latest"  # birincisi sürekli 503 verirse buna geçilir
MATCHES_FILE = os.environ.get("MATCHES_FILE", "matches.json")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "index.html")

SPOR_TOTO_URL = "https://www.sportoto.gov.tr/spor-toto-listeler"

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
FOOTBALL_DATA_RATE_LIMIT_DELAY = 7.5  # saniye; ücretsiz plan 10 istek/dk (pay bırakıldı)

# the-odds-api.com sport key -> görünen ad. Spor Toto kuponu genelde Süper Lig +
# büyük Avrupa ligleri + kupalardan (Şampiyonlar Ligi, EFL Cup vb.) oluşur; resmi
# listedeki her maçın hangi organizasyona ait olduğunu bulmak için bunların hepsi
# taranır (https://the-odds-api.com/sports-odds-data/sports-apis.html'den doğrulandı).
ODDS_SEARCH_LEAGUES = {
    "soccer_turkey_super_league": "Süper Lig",
    "soccer_epl": "Premier League",
    "soccer_efl_champ": "Championship",
    "soccer_england_efl_cup": "EFL Cup",
    "soccer_spain_la_liga": "La Liga",
    "soccer_spain_copa_del_rey": "Copa del Rey",
    "soccer_italy_serie_a": "Serie A",
    "soccer_italy_coppa_italia": "Coppa Italia",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_germany_dfb_pokal": "DFB-Pokal",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_france_coupe_de_france": "Coupe de France",
    "soccer_uefa_champs_league": "Şampiyonlar Ligi",
    "soccer_uefa_europa_league": "Avrupa Ligi",
    "soccer_uefa_europa_conference_league": "Konferans Ligi",
}

# the-odds-api sport key -> football-data.org rekabet kodu. Sadece football-data.org'un
# ücretsiz planında olan 5 büyük lig için form/H2H zenginleştirmesi yapılabilir.
ODDS_KEY_TO_FD_CODE = {
    "soccer_epl": "PL",
    "soccer_spain_la_liga": "PD",
    "soccer_italy_serie_a": "SA",
    "soccer_germany_bundesliga": "BL1",
    "soccer_france_ligue_one": "FL1",
}

ODDS_SEARCH_WINDOW_DAYS = int(os.environ.get("ODDS_SEARCH_WINDOW_DAYS", "10"))
FD_H2H_HISTORY_LIMIT = int(os.environ.get("FD_H2H_HISTORY_LIMIT", "50"))

VALID_PREDICTIONS = {"1", "X", "2", "1X", "X2", "12"}


# ---------------------------------------------------------------------------
# 1. Veri kaynağı
# ---------------------------------------------------------------------------

def load_matches_from_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


OFFICIAL_MATCHES_FILE = os.environ.get("OFFICIAL_MATCHES_FILE", "official_matches.json")


def load_official_spor_toto_fixtures(path: str) -> tuple[list, str | None]:
    """sportoto.gov.tr'nin resmi haftalık 15 maçlık listesini bu dosyadan okur.

    sportoto.gov.tr, veriyi düz HTTP isteklerine (curl/requests, Playwright headless
    tarayıcı dahil) boş bir tablo döndürerek bot korumasıyla veriyor - otomatik
    kazıma bu korumayı aşmayı gerektireceği için yapılmıyor. Bunun yerine haftalık
    listeyi (takım + tarih/saat) sportoto.gov.tr/spor-toto-listeler adresinden elle
    ya da bir ekran görüntüsünden bu dosyaya aktarmanız gerekir - format için
    README'ye bakın."""
    if not os.path.exists(path):
        return [], None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("matches", []), data.get("week")


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


def fetch_odds_events(session: requests.Session, api_key: str, sport_key: str,
                       date_from: str, date_to: str, league_name: str) -> list:
    """Bir the-odds-api.com sport key'i için tarih aralığındaki ham event listesini döner."""
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
            f"UYARI: {league_name} ({sport_key}) oran alınamadı "
            f"({resp.status_code}): {resp.text[:300]}",
            file=sys.stderr,
        )
        return []
    return resp.json()


_NAME_JUNK = (" fc", " cf", " afc", " sv", " tsg", " vfl", " vfb", " fsv", " sc", " ac",
              " cd", " ud", " rc", " 1900", " 1899", " 1904", " 04", " 05")


def normalize_team_name(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"^\d+\.\s*", "", n)  # "1. FC Union Berlin" -> "fc union berlin"
    for junk in _NAME_JUNK:
        n = n.replace(junk, " ")
    return re.sub(r"\s+", " ", n).strip()


def names_roughly_match(a: str, b: str) -> bool:
    na, nb = normalize_team_name(a), normalize_team_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def fetch_fd(session: requests.Session, headers: dict, path: str, params: dict | None = None) -> dict | None:
    for attempt in range(2):  # 429 alınırsa bir kez daha dene
        try:
            resp = session.get(f"{FOOTBALL_DATA_BASE_URL}{path}", headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            print(f"UYARI: football-data.org isteği başarısız ({path}): {e}", file=sys.stderr)
            time.sleep(FOOTBALL_DATA_RATE_LIMIT_DELAY)
            return None
        if resp.status_code == 429 and attempt == 0:
            print(f"UYARI: football-data.org {path} -> 429, {FOOTBALL_DATA_RATE_LIMIT_DELAY * 2}s bekleyip "
                  f"tekrar denenecek.", file=sys.stderr)
            time.sleep(FOOTBALL_DATA_RATE_LIMIT_DELAY * 2)
            continue
        time.sleep(FOOTBALL_DATA_RATE_LIMIT_DELAY)
        if not resp.ok:
            print(f"UYARI: football-data.org {path} -> {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return None
        return resp.json()
    return None


def fetch_fd_competition_teams(session: requests.Session, headers: dict, code: str) -> dict:
    data = fetch_fd(session, headers, f"/competitions/{code}/teams")
    if not data:
        return {}
    return {t["name"]: t["id"] for t in data.get("teams", [])}


def resolve_fd_team_id(name_to_id: dict, team_name: str) -> int | None:
    for fd_name, team_id in name_to_id.items():
        if names_roughly_match(team_name, fd_name):
            return team_id
    return None


def fetch_fd_team_form(session: requests.Session, headers: dict, team_id: int) -> str:
    data = fetch_fd(session, headers, f"/teams/{team_id}/matches", {"status": "FINISHED", "limit": 5})
    if not data:
        return "veri yok"
    results = []
    for m in data.get("matches", []):
        score = m.get("score", {}).get("fullTime", {})
        home_goals, away_goals = score.get("home"), score.get("away")
        if home_goals is None or away_goals is None:
            continue
        is_home = m["homeTeam"]["id"] == team_id
        if home_goals == away_goals:
            results.append("D")
        elif (home_goals > away_goals) == is_home:
            results.append("W")
        else:
            results.append("L")
    return "".join(results) if results else "veri yok"


def fetch_fd_h2h(session: requests.Session, headers: dict, team_id: int, opponent_id: int,
                  current_home_name: str) -> str:
    """team_id'nin genel maç geçmişini çekip opponent_id ile oynadığı maçları filtreler
    (belirli bir FD fikstür id'sine ihtiyaç duymaz - maçlar resmi Spor Toto listesinden
    geldiği için football-data.org'un kendi fikstür id'si elimizde yok)."""
    data = fetch_fd(
        session, headers, f"/teams/{team_id}/matches",
        {"status": "FINISHED", "limit": FD_H2H_HISTORY_LIMIT},
    )
    if not data:
        return "veri yok"
    h2h_matches = []
    for m in data.get("matches", []):
        home_t, away_t = m.get("homeTeam", {}), m.get("awayTeam", {})
        other_id = away_t.get("id") if home_t.get("id") == team_id else home_t.get("id")
        if other_id != opponent_id:
            continue
        score = m.get("score", {}).get("fullTime", {})
        if score.get("home") is None or score.get("away") is None:
            continue
        h2h_matches.append(m)
    h2h_matches.sort(key=lambda m: m.get("utcDate", ""))
    results = []
    for m in h2h_matches[-5:]:
        home_t = m["homeTeam"]
        score = m["score"]["fullTime"]
        if score["home"] == score["away"]:
            results.append("X")
        elif (score["home"] > score["away"]) == (home_t["name"] == current_home_name):
            results.append("1")
        else:
            results.append("2")
    return "-".join(results) if results else "veri yok"


def load_matches_from_api(odds_api_key: str, football_data_key: str | None = None) -> dict:
    official_fixtures, week_label = load_official_spor_toto_fixtures(OFFICIAL_MATCHES_FILE)
    if not official_fixtures:
        return {"week": week_label, "matches": []}

    session = requests.Session()
    today = datetime.now(timezone.utc)
    date_from = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (today + timedelta(days=ODDS_SEARCH_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    tagged_events = []  # (sport_key, league_name, event)
    for sport_key, league_name in ODDS_SEARCH_LEAGUES.items():
        for event in fetch_odds_events(session, odds_api_key, sport_key, date_from, date_to, league_name):
            tagged_events.append((sport_key, league_name, event))

    fd_headers = {"X-Auth-Token": football_data_key} if football_data_key else None
    fd_team_cache: dict[str, dict] = {}

    matches = []
    for i, fx in enumerate(official_fixtures):
        home_name, away_name = fx["home_team"], fx["away_team"]
        match = {
            "match_id": f"st{i + 1}",
            "league": "Spor Toto",
            "home_team": home_name,
            "away_team": away_name,
            "kickoff": fx["kickoff"],
            "odds": {},
            "home_form_last5": "veri yok",
            "away_form_last5": "veri yok",
            "h2h_last5": "veri yok",
        }

        matched = next(
            (
                (sport_key, league_name, event)
                for sport_key, league_name, event in tagged_events
                if names_roughly_match(home_name, event.get("home_team", ""))
                and names_roughly_match(away_name, event.get("away_team", ""))
            ),
            None,
        )
        if matched:
            sport_key, league_name, event = matched
            match["league"] = league_name
            match["odds"] = extract_1x2_odds(event)

            fd_code = ODDS_KEY_TO_FD_CODE.get(sport_key)
            if fd_code and fd_headers:
                if fd_code not in fd_team_cache:
                    fd_team_cache[fd_code] = fetch_fd_competition_teams(session, fd_headers, fd_code)
                team_map = fd_team_cache[fd_code]
                home_id = resolve_fd_team_id(team_map, home_name)
                away_id = resolve_fd_team_id(team_map, away_name)
                if home_id:
                    match["home_form_last5"] = fetch_fd_team_form(session, fd_headers, home_id)
                if away_id:
                    match["away_form_last5"] = fetch_fd_team_form(session, fd_headers, away_id)
                if home_id and away_id:
                    match["h2h_last5"] = fetch_fd_h2h(session, fd_headers, home_id, away_id, home_name)

        matches.append(match)

    return {"week": week_label or today.strftime("%Y-%m-%d"), "matches": matches}


def load_matches() -> dict:
    odds_api_key = os.environ.get("ODDS_API_KEY")
    if odds_api_key:
        football_data_key = os.environ.get("FOOTBALL_DATA_API_KEY")
        try:
            bulletin = load_matches_from_api(odds_api_key, football_data_key)
        except Exception as e:
            print(f"UYARI: Gerçek veri işlenirken hata oluştu ({e}), matches.json şablonuna dönülüyor.",
                  file=sys.stderr)
            return load_matches_from_file(MATCHES_FILE)
        if bulletin["matches"]:
            return bulletin
        print("UYARI: Gerçek maç verisi bulunamadı, matches.json şablonuna dönülüyor.", file=sys.stderr)
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


def get_predictions(client: genai.Client, compact_matches: list, max_retries: int = 5) -> dict:
    last_error = None
    for model_id in (MODEL_ID, FALLBACK_MODEL_ID):
        for attempt in range(max_retries):
            if attempt > 0:
                delay = min(2 ** attempt, 30)
                print(f"UYARI: {model_id} geçici hata verdi, {delay}s sonra tekrar denenecek "
                      f"({attempt}/{max_retries - 1}).", file=sys.stderr)
                time.sleep(delay)
            try:
                response = client.models.generate_content(
                    model=model_id,
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
        print(f"UYARI: {model_id} tüm denemelerde başarısız oldu, farklı modele geçiliyor.",
              file=sys.stderr)
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
    kickoff = match.get("kickoff")
    try:
        kickoff_display = datetime.fromisoformat(kickoff).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        kickoff_display = kickoff or "-"

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
