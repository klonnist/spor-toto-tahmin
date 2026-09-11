# Spor Toto Haftalık Tahmin Botu

Google Gemini (`gemini-3.6-flash`, ücretsiz katman) ile 15 maçlık bültenler için haftalık,
istatistiksel gerekçeli tahminler üreten ve sonucu GitHub Pages üzerinde statik bir sitede
yayınlayan proje.

> Not: Proje başlangıçta Claude, ardından GitHub Models (`gpt-4o-mini`) ile denendi.
> GitHub Models servisi kullanımdan kaldırılma sürecinde olduğu (retirement brownout)
> için isteklerin tamamı başarısız oluyordu; bu yüzden ücretsiz ve kalıcı bir seçenek olan
> **Google Gemini**'ye geçildi. Hâlâ tek bir `GEMINI_API_KEY` secret'i gerekir, ama bu
> anahtar Google AI Studio'dan kredi kartı istenmeden ücretsiz alınabilir.

## Dosyalar

- `main.py` — veri okuma, Gemini'ye istek, `index.html` üretimi
- `official_matches.json` — **o haftaki gerçek 15 maçlık Spor Toto kuponu** (bkz. aşağıdaki
  "Haftalık Maç Listesini Güncelleme" bölümü)
- `matches.json` — `official_matches.json` yoksa/boşsa kullanılan örnek şablon
- `.github/workflows/generate_predictions.yml` — otomasyon
- `requirements.txt` — Python bağımlılıkları

## Haftalık Maç Listesini Güncelleme (önemli)

sportoto.gov.tr, otomatik isteklere (curl, `requests`, hatta Playwright ile headless
tarayıcı) bilinçli olarak boş bir tablo döndürüyor — bot koruması. Bu korumayı aşmaya
çalışmak yerine (bir devlet sitesinde bunu yapmamak gerekir), **her hafta gerçek 15
maçlık listeyi `official_matches.json`'a siz aktarıyorsunuz** — bu, projenin otomatik
kısmının doğru maçlarla çalışması için gereken tek manuel adım:

1. https://www.sportoto.gov.tr/spor-toto-listeler adresine kendi tarayıcınızdan girin.
2. O haftaki 15 maçı görün. İki seçenek:
   - **Ekran görüntüsü**: Sayfanın ekran görüntüsünü alıp bana (Claude'a) gönderin —
     takım adları, tarih ve saatleri okuyup `official_matches.json`'u güncelleyip
     commit/push ederim.
   - **Elle**: Aşağıdaki formata göre kendiniz doldurun:
     ```json
     {
       "week": "5. Hafta",
       "matches": [
         {"home_team": "Beşiktaş", "away_team": "Erzurumspor FK", "kickoff": "2026-09-11T20:00:00+03:00"}
       ]
     }
     ```
     (`kickoff` Türkiye saatiyle `+03:00` uzantılı ISO 8601 formatındadır.)
3. Dosyayı `master` branch'ine push edin (ya da benim push etmemi isteyin) — bu,
   `official_matches.json` yolundaki değişiklik nedeniyle workflow'u otomatik tetikler.

`main.py`, bu 15 gerçek maçın her biri için the-odds-api.com'da takım adı eşleştirmesiyle
gerçek oranı ve hangi ligde/kupada oynandığını bulur; lig football-data.org'un ücretsiz
kapsamındaysa (Premier League/La Liga/Serie A/Bundesliga/Ligue 1) gerçek form/H2H de
eklenir. `official_matches.json` yoksa ya da boşsa `matches.json` şablonuna döner.

## Yerel Çalıştırma

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="AIza..."
python main.py
```

Bu komut `official_matches.json` varsa onu, yoksa `matches.json` şablonunu okuyup
`index.html` üretir. Tarayıcıda `index.html` dosyasını açarak sonucu görebilirsiniz.

## 1. Google Gemini API Key Alma (ücretsiz)

1. https://aistudio.google.com/apikey adresine gidin ve Google hesabınızla giriş yapın.
2. **Create API key** butonuna basın (yeni bir proje seçebilir ya da var olanı
   kullanabilirsiniz).
3. Oluşan `AIza...` ile başlayan anahtarı kopyalayın.
4. Kredi kartı gerekmez; ücretsiz katmanın günlük/dakikalık istek limitleri vardır
   (güncel limitler için Google AI Studio'daki **Rate limits** sayfasına bakın) — 15
   maçlık haftalık bir bülten bu limitlerin çok altında kalır.

## 2. The Odds API Key Alma (ücretsiz) — gerçek oranlar için

`official_matches.json`'daki 15 gerçek maçın hangi ligde/kupada oynandığını ve gerçek
1X2 oranlarını bulmak için [the-odds-api.com](https://the-odds-api.com) key'i şart
(bu olmadan maçlar oransız, "Spor Toto" genel etiketiyle gösterilir):

1. https://the-odds-api.com adresine gidip **Get API Key** ile ücretsiz kaydolun
   (kredi kartı istemez, key e-postanıza gelir).
2. Ücretsiz plan: **ayda 500 kredi**. `main.py`, resmi listedeki 15 maçın hangi lig/kupaya
   ait olduğunu bulmak için Süper Lig + büyük Avrupa ligleri + yaygın kupalardan oluşan
   ~15 turnuvayı tarar (`main.py` → `ODDS_SEARCH_LEAGUES`); bu haftalık ~30 kredi eder,
   500 kredilik ücretsiz plana rahatça sığar.
3. `ODDS_API_KEY` secret'i tanımlıysa `main.py`, her resmi maç için takım adı
   eşleştirmesiyle gerçek 1X2 oranını ve lig adını otomatik bulur.

## 3. football-data.org Key Alma (ücretsiz) — gerçek form ve H2H istatistiği için

The Odds API form/H2H sağlamıyor. Bu yüzden resmi 15 maç içinden Premier League, La Liga,
Serie A, Bundesliga ya da Ligue 1'e denk gelenler için gerçek **son 5 maç formu** ve
**H2H geçmişi** [football-data.org](https://www.football-data.org)'dan zenginleştirilir
(Süper Lig ve diğer kupalar bu API'nin ücretsiz planında yok, o yüzden onlarda form/H2H
hâlâ "veri yok" görünür):

1. https://www.football-data.org/client/register adresinden ücretsiz kaydolun (kredi
   kartı istemez).
2. Kayıt sonrası e-postanıza gelen ya da hesabınızdaki **X-Auth-Token** anahtarını alın.
3. Ücretsiz plan: **10 istek/dakika**. `main.py` bu limite uymak için istekler arasına
   otomatik ~6.5 saniye bekleme koyar; bu yüzden bu adım (kaç maç eşleşirse o kadar
   sürer, tipik olarak 2-4 dakika) normaldir — workflow zaman aşımı 15 dakikaya
   ayarlıdır.
4. `FOOTBALL_DATA_API_KEY` secret'i tanımlı değilse, bu 5 lige denk gelen maçlar da
   sadece The Odds API'den (form/H2H olmadan) gösterilir; sistem çökmez, sadece
   zenginleştirme atlanır.

## 4. GitHub Secrets Ayarları

Reponuzda:

1. **Settings → Secrets and variables → Actions** sekmesine gidin.
2. **New repository secret** ile aşağıdakileri ekleyin:
   - `GEMINI_API_KEY` → Google AI Studio'dan aldığınız ücretsiz anahtar
   - `ODDS_API_KEY` → (önerilir) the-odds-api.com'dan aldığınız ücretsiz anahtar
   - `FOOTBALL_DATA_API_KEY` → (opsiyonel, form/H2H zenginleştirmesi için) football-data.org'dan aldığınız ücretsiz anahtar
3. Workflow, `GITHUB_TOKEN`'ı otomatik sağlar; ek bir işlem gerekmez, ancak
   **Settings → Actions → General → Workflow permissions** altında
   **Read and write permissions** seçili olmalıdır (gh-pages branch'ine push
   yapabilmesi için).

## 5. GitHub Pages Ayarı

1. İlk workflow çalışmasından sonra `gh-pages` branch'i otomatik oluşur.
2. **Settings → Pages** sekmesine gidip **Source** olarak `gh-pages` branch'ini,
   klasör olarak `/ (root)` seçin ve kaydedin.
3. Siteniz birkaç dakika içinde `https://<kullanıcı-adiniz>.github.io/<repo-adi>/`
   adresinde yayında olur.

## 6. Workflow'u Çalıştırma

- **Manuel**: Repo → **Actions** → *Generate Spor Toto Predictions* → **Run workflow**.
- **Otomatik**: `official_matches.json` (ya da `matches.json`) dosyasını güncelleyip
  `master` branch'ine push ettiğinizde, ya da her Pazartesi 06:00 UTC'de (cron) otomatik
  tetiklenir.

> Cron her Pazartesi çalıştığı için, o haftaki resmi listeyi **Pazartesi 06:00 UTC'den
> (TR saatiyle 09:00) önce** `official_matches.json`'a işlemiş olmanız gerekir; aksi
> halde workflow o hafta için `matches.json` şablonuna döner.

## Sorumluluk Reddi

Bu proje istatistiksel bir modelin çıktısını gösterir; yatırım veya bahis tavsiyesi
değildir. Sorumlu oynayın.
