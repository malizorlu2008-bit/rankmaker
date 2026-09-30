"""İndirilen klibin kullanılabilirlik kontrolleri.

Kullanıcı geri bildirimi (2026-08-02): sessiz klipler videoya giriyordu ve
bazı klipler aslında derleme olduğu için tek bir "sıra"da 2 farklı sahne
görünüyordu. Bu iki durumu klip seçilmeden önce tespit ediyoruz.
"""
import re
import subprocess

from . import motion
from .ffmpeg_utils import get_ffmpeg_path

MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
SILENCE_DB_THRESHOLD = -50.0


def has_audio(path):
    """Ses akışı yoksa ya da neredeyse tamamen sessizse False."""
    cmd = [get_ffmpeg_path(), "-i", path, "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    stderr = proc.stderr.decode(errors="ignore")

    if "Audio:" not in stderr:
        return False
    match = MEAN_VOLUME_RE.search(stderr)
    if not match:
        return False
    return float(match.group(1)) > SILENCE_DB_THRESHOLD


# motion.CUT_HIST_THRESHOLD (0.45) uzun klibi kırparken segment sınırı bulmak
# için ayarlanmış ve hızlı kamera hareketini de kesme sayıyor — burada onu
# kullanırsak tek çekim klipleri de eleriz. Kullanıcının onayladığı kısa
# kliplerde histogram farkı en fazla ~0.7-0.9 çıkarken, gerçekten derleme olan
# kliplerde 1.17-1.89 ölçüldü (2026-08-02 kalibrasyonu), o yüzden ayrı ve daha
# katı bir eşik kullanıyoruz. motion.py'deki eşiğe DOKUNMA.
COMPILATION_HIST_THRESHOLD = 1.0


def looks_like_compilation(path, ilk_saniye=None, pencere=None, samples=None):
    """Klip tam oynatıldığı için içindeki her sahne kesmesi izleyiciye
    'tek sırada 2 ayrı video' olarak görünüyor.

    BAKILAN PENCERE, VIDEOYA GIRECEK PARCAYLA AYNI OLMALI. Videoya klibin
    tamamı girmiyorsa, girmeyen kısımdaki sahne kesmesinin izleyici için bir
    anlamı yok. 2026-08-16'da satisfying videosunda 14 adayın 10'u bu filtreye
    takıldı; ölçünce reddedilenlerin sahne değişimi 25. saniyedeydi ve ilk 10
    saniyedeki histDiff 0.63-0.77'ydi, yani biz ELİMİZDEKİ 10 SANİYEYİ değil,
    hiç kullanmayacağımız kuyruğu eliyorduk.

    Aynı hata ranking tarafında 2026-09-29'a kadar sürdü: orada hiçbir pencere
    geçilmiyordu, yani 25 saniyelik aday klibin tamamı taranıp videoya girecek
    ~10 saniye yüzünden değil, hiç görünmeyecek kuyruk yüzünden eleniyordu.
    29 Eylül #174 çalışmasında beğeni barını geçen 83 adaydan sadece 4'ü bu
    filtreden geçti ve 21:00 slotu boş kaldı. Üstelik motion.best_motion_window
    zaten kesmeleri bulup pencereyi KESMELERIN ARASINDAKI tek bir segmentten
    seçiyor — yani render temiz parçayı seçecekken biz klibi baştan atıyorduk.

    pencere: (start, length) ikilisi — sadece bu aralıktaki örnekler taranır.
    ilk_saniye: pencere=(0, ilk_saniye) ile aynı (eski çağrılar bozulmasın).
    samples: analiz dışarıda yapıldıysa tekrar çözmemek için geçilir.

    Eşik (COMPILATION_HIST_THRESHOLD) DEĞİŞMEDİ. Bu bir gevşetme değil isabet
    düzeltmesi: gösterilecek pencerede gerçekten kesme varsa klip yine elenir.
    """
    if samples is None:
        try:
            _, samples = motion.analyze_motion(path)
        except Exception:
            return False  # analiz edilemiyorsa klibi bu yüzden elemeyelim

    if pencere is None and ilk_saniye:
        pencere = (0.0, ilk_saniye)
    if pencere:
        bas, uzunluk = pencere
        son = bas + uzunluk
        samples = [s for s in samples if bas <= (s.get("t") or 0) <= son]

    for i in range(1, len(samples) - 1):
        d = samples[i].get("histDiff") or 0
        if d < COMPILATION_HIST_THRESHOLD:
            continue
        prev_d = samples[i - 1].get("histDiff") or 0
        next_d = samples[i + 1].get("histDiff") or 0
        if d >= prev_d and d >= next_d:  # yerel tepe = tek geçiş
            return True
    return False


# Hareketsiz klip esigi. Olcum (2026-08-13, bankaya giren 5 slime klibi):
#   tweet ekran goruntusu videosu -> ortalama hareket 1.75
#   gercek slime klipleri         -> 27.0 / 41.9 / 61.3 / 73.8
# Aradaki fark 15 kat, esik ikisinin ortasinda genis paylarla duruyor.
STATIC_MOTION_THRESHOLD = 10.0


def is_static(path, samples=None):
    """Klip pratikte hareketsizse True — ekran goruntusu, tweet/post paylasimi,
    sabit resim uzerine muzik gibi seyler.

    Neden gerekli: alaka filtresi klibin ACIKLAMASINA bakiyor ve bazi hesaplar
    tamamen alakasiz videoya populer hashtag yigiyor. 2026-08-13'te bir slime
    videosuna, aciklamasi "#satisfying #asmr #slime #oddlysatisfying..." olan
    ama icerigi dolu agi hakkinda bir TWEET EKRAN GORUNTUSU olan klip girdi
    (kullanici bildirdi). Aciklamadan anlasilmiyor, ama goruntuden anlasiliyor:
    boyle klipler neredeyse hic hareket icermiyor.
    """
    if samples is None:
        _, samples = motion.analyze_motion(path)
    if not samples:
        return False
    ort = sum(s["motion"] for s in samples) / len(samples)
    return ort < STATIC_MOTION_THRESHOLD
