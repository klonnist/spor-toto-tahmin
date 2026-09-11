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
- `matches.json` — örnek 15 maçlık bülten şablonu
- `.github/workflows/generate_predictions.yml` — otomasyon
- `requirements.txt` — Python bağımlılıkları

## Yerel Çalıştırma

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="AIza..."
python main.py
```

Bu komut `matches.json` dosyasını okuyup `index.html` üretir. Tarayıcıda `index.html`
dosyasını açarak sonucu görebilirsiniz.

## 1. Google Gemini API Key Alma (ücretsiz)

1. https://aistudio.google.com/apikey adresine gidin ve Google hesabınızla giriş yapın.
2. **Create API key** butonuna basın (yeni bir proje seçebilir ya da var olanı
   kullanabilirsiniz).
3. Oluşan `AIza...` ile başlayan anahtarı kopyalayın.
4. Kredi kartı gerekmez; ücretsiz katmanın günlük/dakikalık istek limitleri vardır
   (güncel limitler için Google AI Studio'daki **Rate limits** sayfasına bakın) — 15
   maçlık haftalık bir bülten bu limitlerin çok altında kalır.

## 2. The Odds API Key Alma (ücretsiz) — gerçek maçlar, doğru tarihler ve gerçek oranlar için

`matches.json` sadece bir **örnek şablondur** (sabit tarih/maç içerir). Gerçek, güncel
fikstürleri, doğru tarihleri ve gerçek bahis oranlarını görmek için
[the-odds-api.com](https://the-odds-api.com) key'i şart:

1. https://the-odds-api.com adresine gidip **Get API Key** ile ücretsiz kaydolun
   (kredi kartı istemez, key e-postanıza gelir).
2. Ücretsiz plan: **ayda 500 kredi**, `h2h` (1X2) pazarı dahil — 15 maçlık haftalık bir
   bülten için fazlasıyla yeterli (her lig sorgusu 1 kredi harcar; `main.py` haftada
   6 lig sorgular).
3. `ODDS_API_KEY` secret'i tanımlıysa `main.py`, `main.py` içindeki `LEAGUE_SPORT_KEYS`
   listesindeki liglerin (Süper Lig, Premier League, La Liga, Serie A, Bundesliga,
   Ligue 1) önümüzdeki `FIXTURE_WINDOW_DAYS` (varsayılan 7) gün içindeki gerçek
   fikstürlerini ve gerçek 1X2 oranlarını otomatik çeker, en erken `MAX_MATCHES`
   (varsayılan 15) tanesini bültene koyar.

> The Odds API form/H2H istatistiği sağlamıyor — bu alanlar sitede "veri yok" olarak
> görünür ve Gemini yorumlarını sadece oranlara dayandırır. `ODDS_API_KEY` tanımlı
> değilse, ya da o hafta hiç maç bulunamazsa, script otomatik olarak `matches.json`
> şablonuna döner (site boş kalmasın diye).

## 3. GitHub Secrets Ayarları

Reponuzda:

1. **Settings → Secrets and variables → Actions** sekmesine gidin.
2. **New repository secret** ile aşağıdakileri ekleyin:
   - `GEMINI_API_KEY` → Google AI Studio'dan aldığınız ücretsiz anahtar
   - `ODDS_API_KEY` → (opsiyonel ama önerilir) the-odds-api.com'dan aldığınız ücretsiz anahtar
3. Workflow, `GITHUB_TOKEN`'ı otomatik sağlar; ek bir işlem gerekmez, ancak
   **Settings → Actions → General → Workflow permissions** altında
   **Read and write permissions** seçili olmalıdır (gh-pages branch'ine push
   yapabilmesi için).

## 4. GitHub Pages Ayarı

1. İlk workflow çalışmasından sonra `gh-pages` branch'i otomatik oluşur.
2. **Settings → Pages** sekmesine gidip **Source** olarak `gh-pages` branch'ini,
   klasör olarak `/ (root)` seçin ve kaydedin.
3. Siteniz birkaç dakika içinde `https://<kullanıcı-adiniz>.github.io/<repo-adi>/`
   adresinde yayında olur.

## 5. Workflow'u Çalıştırma

- **Manuel**: Repo → **Actions** → *Generate Spor Toto Predictions* → **Run workflow**.
- **Otomatik**: `matches.json` dosyasını güncelleyip `master` branch'ine push ettiğinizde,
  ya da her Pazartesi 06:00 UTC'de (cron) otomatik tetiklenir.

## Sorumluluk Reddi

Bu proje istatistiksel bir modelin çıktısını gösterir; yatırım veya bahis tavsiyesi
değildir. Sorumlu oynayın.
