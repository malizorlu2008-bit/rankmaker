# RankMaker

Tek bir YouTube Shorts kanalı (**[@RankForMee](https://www.youtube.com/@RankForMee)**)
için yazılmış, kendi kendine çalışan bir video üretim hattı. Her gün dört tane
"Ranking …" formatında dikey video üretir, YouTube'a saati ayarlanmış olarak yükler
ve yayınlandıktan beş dakika sonra her videoya bir soru yorumu bırakır.
Hiçbir bilgisayarın açık olması gerekmez — her şey GitHub Actions üzerinde koşar.

## Akış

1. **Konu seçimi** — `pipeline/daily_run.py`, işlenmemiş bir konuyu ölçülmüş klip
   bolluğuna göre seçer. Günün dört slotundan ikisi oyun (gaming) konusudur.
2. **Klip bulma** — `pipeline/discovery.py` + `tiktok.py`, TikTok hashtag'lerini
   tarar ve en az 50.000 beğenili gerçek klipleri süzer.
3. **Kalite kontrolü** — `pipeline/quality.py` + `motion.py`, OpenCV ile aksiyon
   anını bulur; donuk ve derleme (kompilasyon) klipleri eler.
4. **Etiketleme** — `pipeline/labeler.py`, ekran üstü etiketleri, başlığı,
   hashtag'leri ve yorum sorusunu Gemini API ile üretir.
5. **Kurgu** — `pipeline/compose.py` + `render.py` + `ffmpeg_utils.py`, klipleri
   numaralandırarak 9:16 tek videoya birleştirir.
6. **Yükleme** — `.credentials/upload.py`, YouTube Data API v3 ile `status.publishAt`
   kullanıp videoyu ileri bir saate programlar.
7. **Yorum** — `pipeline/comment_runner.py`, her videonun yayınından beş dakika
   sonra ayrı bir çalışmada tetiklenir.
8. **Bildirim** — `pipeline/notify.py`, çalışma özetini e-postayla gönderir.

Kaynak erişilemediği günler için iki tampon var: `pipeline/bank.py` (bol günlerde
biriktirilen fazla klipler) ve `pipeline/reserve.py` (kanalın eski videolarından
oluşan son çare kütüphanesi).

## Tetikleme

Bütün workflow'lar yalnızca `workflow_dispatch` ile çalışır; **GitHub'ın kendi
`schedule:` cron'u bu depoda hiç ateşlenmedi** (2026-08-03), o yüzden zamanlamayı
dışarıdan [cron-job.org](https://cron-job.org) yapıyor:

| Saat (Europe/Istanbul) | Workflow |
|---|---|
| 07:00, 13:00 | `daily_rankmaker.yml` — günün dört videosunu üretir |
| 18:05, 19:05, 20:05, 21:05 | `post_comments.yml` — o saatte yayınlanan videoya yorum atar |

## Gizli bilgiler

Depoda hiçbir kimlik bilgisi yoktur; hepsi GitHub Actions Secrets'ta durur:
`YT_TOKEN_JSON`, `YT_CLIENT_SECRET_JSON`, `SECRETS_PAT`, `GEMINI_API_KEY`,
`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `NOTIFY_TO`. CI, `.credentials/token.json`
dosyasını her çalışmada secret'tan yazar (`pipeline/check_token.py` doğrular) ve
Google token'ı yenilediğinde secret'ı kendisi günceller — dosya asla commit edilmez.

## Not

Bu kod tek bir kanala, onun konu geçmişine ve hesaplarına göre yazıldı; olduğu gibi
kopyalayıp çalıştırmak için değil. `pipeline/state/` altındaki dosyalar bu kanalın
üretim geçmişidir. Lisans verilmemiştir.
