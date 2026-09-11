# Spor Toto Haftalık Tahmin Botu

Google Gemini (`gemini-2.5-flash`, ücretsiz katman) ile 15 maçlık bültenler için haftalık,
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

## 2. RapidAPI Key Alma (API-Football)

Gerçek maç/oran verisi çekmek isterseniz (opsiyonel — `matches.json` şablonu olmadan da
kullanılabilir):

1. https://rapidapi.com adresine gidip ücretsiz hesap açın.
2. Arama kutusuna **API-FOOTBALL** yazıp resmi API-Football sayfasına girin
   (yayıncı: `api-sports`).
3. Sağ üstteki bir plana (Basic/free plan günlük istek limitiyle gelir) **Subscribe** deyin.
4. Sayfadaki **X-RapidAPI-Key** değerini kopyalayın (Endpoints sekmesindeki örnek kod
   bloklarında da görünür).
5. `main.py` içindeki `load_matches_from_api` fonksiyonu bu anahtarla `fixtures/odds`,
   `teams/statistics` ve `fixtures/headtohead` uçlarını çağırır. Hangi maçların
   çekileceğini belirtmek için `FIXTURES_CONFIG` ortam değişkeniyle gösterilen bir JSON
   dosyasına (fixture/team/league id'leri) ihtiyaç vardır — API-Football'un fikstür arama
   ucundan (`/fixtures?date=...`) bu id'leri elde edebilirsiniz.

> RapidAPI anahtarı tanımlı değilse veya `FIXTURES_CONFIG` verilmezse script otomatik
> olarak `matches.json` dosyasını kullanır.

## 3. GitHub Secrets Ayarları

Reponuzda:

1. **Settings → Secrets and variables → Actions** sekmesine gidin.
2. **New repository secret** ile aşağıdakileri ekleyin:
   - `GEMINI_API_KEY` → Google AI Studio'dan aldığınız ücretsiz anahtar
   - `RAPIDAPI_KEY` → (opsiyonel) RapidAPI'dan aldığınız anahtar
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
