"""CI'a yazilan .credentials/token.json'i, hicbir sey uretilmeden dogrula.

Neden var: depo 2026-09-28'de public'e cevrildi ve YouTube yetkisi artik repoda
degil — her calismada YT_TOKEN_JSON secret'indan diske yaziliyor. Bozuk ya da
eksik bir secret'i fark etmezsek hata ancak 40 dakikalik render'in SONUNDA,
yukleme adiminda ortaya cikiyor ve o calismanin tamami bosa gidiyor. Bu dogrulama
bunu ilk saniyede yakalar.

Kullanim: python3 -m pipeline.check_token   (hatada exit 1 + tek satir aciklama)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(os.path.dirname(HERE), ".credentials", "token.json")

# token.json bunlar olmadan ise yaramaz: refresh_token olmadan yenilenemez,
# client_id/client_secret olmadan yenileme istegi reddedilir.
ZORUNLU_ALANLAR = ("refresh_token", "client_id", "client_secret")

# Yukleme + yorum icin gereken kapsamlar (comment.py ve visibility.py de ayni
# kapsamlari ariyor; eksikse hata YouTube tarafinda 403 olarak donuyor).
BEKLENEN_KAPSAMLAR = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)


def main():
    if not os.path.exists(TOKEN_PATH):
        sys.exit("token.json yok — YT_TOKEN_JSON secret'i yazilmamis")

    try:
        with open(TOKEN_PATH) as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"token.json gecerli JSON degil: {e}")

    eksik = [a for a in ZORUNLU_ALANLAR if not data.get(a)]
    if eksik:
        sys.exit(f"token.json icinde su alanlar yok: {', '.join(eksik)}")

    kapsamlar = data.get("scopes") or []
    eksik_kapsam = [k for k in BEKLENEN_KAPSAMLAR if k not in kapsamlar]
    if eksik_kapsam:
        # Olumcul degil: yorum atma kapsami olmadan yukleme hala yapilabiliyor.
        kisa = ", ".join(k.rsplit("/", 1)[-1] for k in eksik_kapsam)
        print(f"UYARI: token.json su kapsamlari tasimiyor: {kisa}")

    print("token.json gecerli, kapsamlar: "
          + ", ".join(k.rsplit("/", 1)[-1] for k in kapsamlar))


if __name__ == "__main__":
    main()
