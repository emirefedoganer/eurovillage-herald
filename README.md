# The Eurovillage Herald — Website

Eurovillage'ın (kurgusal Minecraft şehri) ilk ve bağımsız gazetesi için NYT/Washington Post
tarzında profesyonel bir haber sitesi. Türkçe içerik, bölüm sayfaları, PDF gazete okuyucu ve
bir yönetim (admin) paneli içerir.

## Çalıştırma

```bash
cd app
python3 -m pip install --user -r ../requirements.txt
python3 app.py
```

Sunucu `http://127.0.0.1:5050` adresinde başlar.

## Yönetim Paneli

`http://127.0.0.1:5050/admin/login` adresinden giriş yapın. Yönetim paneli genel siteden
kasıtlı olarak **hiçbir yere bağlantı vermez** — üst menüde, altbilgide ya da başka bir sayfada
"Yönetici Girişi" gibi bir link yoktur; adresi doğrudan bilmeniz gerekir.

- E-posta: `admin@eurovillageherald.com`
- Şifre: `eurovillage2026`

**Önce şifrenizi değiştirin:** Giriş yaptıktan sonra sağ üstteki "Şifre Değiştir" butonunu kullanın.

Panelden yeni makale ekleyebilir, mevcut makaleleri düzenleyip silebilir, anasayfada manşet/öne
çıkan haberleri belirleyebilir ve yeni PDF sayılar yükleyebilirsiniz.

## Yazar Profilleri ve Yazar Yönetimi

Site, tek bir kanonik yazar kimliği kullanır: Haberler, Görüş/Köşe Yazıları, Arı Magazin,
yazar kartları (hover card) ve arşivler hepsi aynı yazar kaydına bağlanır — her makalede ayrı
ayrı isim/biyografi kopyalanmaz. Bir yazarın profili güncellendiğinde değişiklik otomatik olarak
her yerde görünür.

**Herkese açık profil sayfası** — `/profil/<slug>`: fotoğraf, kapak görseli, kısa/uzun biyografi,
editoryal unvan rozeti, X/Twitter bağlantısı, ve varsa **Minecraft kullanıcı adına göre otomatik
çözülen** karakter görseli (Mojang API üzerinden UUID bulunur, Crafatar ile render edilir;
kullanıcı bulunamazsa ya da Crafatar erişilemezse mc-heads.net'e otomatik geçilir, o da
başarısız olursa ilgili blok sessizce gizlenir — hiçbir zaman kırık bir görsel gösterilmez).
Yazar adına her tıklandığında (haberde, görüş yazısında, Arı'da, Gazete Yönetimi sayfasında)
aynı profil sayfasına gidilir; üzerine gelindiğinde küçük bir önizleme kartı (hover card) açılır.

**Hesap Rolü vs Editoryal Rol** — bunlar kasıtlı olarak ayrı iki kavramdır:
- **Hesap Rolü** (`master_admin` / `author`) — giriş ve yetkiyi belirler.
- **Editoryal Rol** (ör. "Köşe Yazarı", "Genel Yayın Yönetmeni") ve "Köşe Yazarı" işareti —
  yalnızca profilde ve makalelerde nasıl göründüğünü belirler.

Bir yazarı "Köşe Yazarı" olarak işaretlemek onu otomatik olarak yönetici yapmaz.

**Normal Yazar hesabı** (`/admin/profilim`) yalnızca kendi profil fotoğrafını, kapak görselini,
biyografisini, Twitter ve Minecraft kullanıcı adını düzenleyebilir ve yalnızca kendi yazdığı
makaleleri ekleyip düzenleyebilir. Slug, editoryal unvan, hesap durumu, e-posta ya da diğer
yazarların hiçbir bilgisi bu hesaptan değiştirilemez — bu sınır tamamen sunucu tarafında,
formdan hangi alanların okunacağı sabit bir liste ile zorlanır (istemciden gelen fazladan
alanlar yok sayılır).

**Master Admin** (`/admin/yazarlar`) yeni yazar hesabı oluşturabilir (e-posta + rol + profil
tek adımda), şifre sıfırlayabilir (site henüz e-posta gönderemediği için tek seferlik bir geçici
şifre üretilip ekranda gösterilir), hesapları devre dışı bırakabilir/arşivleyebilir, ve
makalesi olmayan bir yazarı tamamen silebilir (makalesi olan bir yazar silinemez — önce
arşivlenmesi gerekir). Son aktif Master Admin hesabı devre dışı bırakılamaz veya rolü
değiştirilemez — sistemin kilitlenmesini önlemek için.

Tüm hassas işlemler (giriş, yazar oluşturma, şifre sıfırlama, hesap durumu değişikliği, silme)
`/admin/denetim-kaydi` sayfasındaki **Denetim Kaydı**'na otomatik olarak işlenir.

## Arı Magazin ve Opinion / Köşe Yazıları Ayrımı

`/magazin` sabit bir route'tur ve değişmez — dergi makaleleri site genelindeki diğer makaleler
gibi `/makale/<slug>` üzerinden sunulur (`section: "magazin"`). Sayfa hangi yayına ait
görünüyorsa (Arı dergisi sayfası ya da `section: "magazin"` bir makale) üst logo otomatik
olarak Arı'nın resmi SVG logosuna döner; site genelinde (yazar profilleri dahil) normal
"The Eurovillage Herald" logosu kullanılır. Bu, `app.py` içindeki tek bir `g.publication_context`
bayrağı ve `base.html`'deki paylaşılan masthead bloğu üzerinden merkezi olarak yönetilir —
sayfa sayfa elle eklenmez.

Görüş bölümü (`section: "gorus"`) ziyaretçiye artık **Opinion** olarak görünür. Bu bölümdeki bir
makale, yazarı "Opinion Columnist" olarak işaretliyse otomatik olarak **Köşe Yazısı** etiketiyle
gösterilir (aksi halde "Opinion") — bu ayrım `effective_kicker()` (app.py) üzerinden tek bir
yerden yönetilir, tüm kartlarda ve yazar profillerinde tutarlıdır. Bir yazarın Köşe Yazıları
varsa, profilinde diğer tüm içeriklerinden önce gösterilir.

Anasayfadaki **Oyun Köşesi** kutusu, `store.published_crosswords()` / `published_sudokus()`
üzerinden gerçek, güncel yayımlanmış bulmaca/sudoku kaydını çeker — statik değildir. Admin
panelinden yeni bir oyun yayımlandığında anasayfa otomatik güncellenir.

### Admin panelini ayrı bir alt alan adına (admin.eurovillageherald.com) taşımak

Kod buna hazır: `app/app.py` içindeki tüm `/admin/*` route'ları ayrı bir Flask Blueprint'te
toplanmış durumda. Bir `SERVER_NAME` ortam değişkeni tanımlandığında, bu Blueprint otomatik
olarak **yalnızca** `admin.<SERVER_NAME>` alt alan adından sunulur ve ana sitenin alan adında
(`/admin` dahil) hiçbir şekilde erişilemez hale gelir — iki ayrı site gibi davranırlar.
`SERVER_NAME` tanımlı değilken (yerel geliştirme gibi) panel otomatik olarak eskisi gibi
`/admin/` altında çalışmaya devam eder, ekstra kurulum gerekmez.

Gerçek bir sunucuya taşırken:

1. Alan adınızın DNS ayarlarına `admin` için bir A veya CNAME kaydı ekleyin (sunucunuzun
   IP'sine veya ana alan adına işaret etmeli), böylece `admin.eurovillageherald.com` de
   `eurovillageherald.com` ile aynı sunucuya ulaşır.
2. Uygulamayı başlatırken ortam değişkenini tanımlayın:
   ```bash
   SERVER_NAME=eurovillageherald.com python3 app.py
   ```
   (Gerçek dağıtımda `python3 app.py` yerine `gunicorn` gibi bir WSGI sunucusu kullanın —
   aşağıdaki Güvenlik Notu'na bakın.)
3. Sunucunun önündeki reverse proxy'nin (nginx vb.) hem `eurovillageherald.com` hem de
   `admin.eurovillageherald.com` host başlıklarını aynı Flask sürecine yönlendirdiğinden emin
   olun — Flask, `Host` başlığına bakarak hangi Blueprint'in eşleşeceğine karar verir.
4. Admin oturum çerezi varsayılan olarak yalnızca `admin.eurovillageherald.com` için
   ayarlanır (alt alan adları arasında paylaşılmaz) — bu kasıtlıdır ve ek bir güvenlik katmanı
   sağlar.

## Cloudflare Entegrasyonu (Turnstile, R2, Access)

Site üç noktada Cloudflare ile bütünleşecek şekilde hazırlanmıştır; üçü de
ortam değişkenleri boşken yerel geliştirmede tamamen devre dışıdır, yani
yerel geliştirme hiçbir ek kuruluma gerek kalmadan eskisi gibi çalışmaya
devam eder. **Üretimde** (`app/runtime_env.py` -- `APP_ENV=production` ya da
`SERVER_NAME` tanımlıyken) bu "yapılandırılmamış" durum farklı ele alınır:
Turnstile eksikse uygulama yine açılır ama her başlangıçta göz ardı
edilemeyecek bir uyarı basar; R2 *kısmen* (bazı `R2_*` değişkenleri var,
bazıları yok) yapılandırılmışsa bu bir hata sayılır ve yüklemeler yerel
diske sessizce düşmek yerine açık bir hatayla reddedilir. Her iki özellik
için de, kurulum tamamlandıktan sonra sessiz düşmeyi tamamen kapatmak
isterseniz `TURNSTILE_REQUIRED=true` / `R2_REQUIRED=true` ayarlanabilir
(detaylar `.env.example`'da).

- **Turnstile ziyaretçi doğrulaması** (`app/turnstile.py`, `app/templates/gate.html`) —
  `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` tanımlandığında genel siteye
  Managed modda bir Cloudflare Turnstile katmanı eklenir; doğrulama her
  zaman sunucu tarafında Siteverify ile kontrol edilir ve başarılı
  ziyaretçiye imzalı, HttpOnly bir çerez verilir (varsayılan 14 gün).
  Aynı anahtar çifti İletişim formunu da korur (`turnstile.check()`) —
  yeni bir form eklerken aynı fonksiyonu ve `_macros.html` içindeki
  `turnstile_widget()` makrosunu kullanmanız yeterlidir.
- **R2 medya depolama** (`app/storage.py`, `app/uploads.py`) — `R2_*`
  değişkenleri tanımlandığında makale/yazar görselleri ve gazete
  PDF'leri/kapakları doğrudan Cloudflare R2'ye yüklenir ve veritabanına
  (JSON) `matbaa.eurovillageherald.com` altında tam bir URL yazılır; boşken
  eskisi gibi yerel diske (`static/img/...`, `static/issues/`) kaydedilmeye
  devam eder. Şablonlar her iki durumu da tek bir `media_url()` yardımcı
  fonksiyonuyla çözer, bu yüzden eski kayıtlar (düz dosya adı) R2 açıldıktan
  sonra da kırılmadan çalışır. Mevcut medyayı R2'ye taşımak için
  `scripts/migrate_media_to_r2.py` betiğine bakın.
- **Cloudflare Access** (`app/cf_access.py`) — asıl erişim kontrolü
  Cloudflare panelinden `admin.eurovillageherald.com` üzerine kurulur
  (bkz. `CLOUDFLARE_SETUP.md`); `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD`
  tanımlanırsa uygulama ayrıca Access'in isteğe eklediği imzalı kimliği
  doğrular (savunma derinliği amaçlı, isteğe bağlı). Herald'ın kendi
  master_admin/author/rol yetkilendirme sistemi bundan tamamen bağımsız
  ve değişmeden çalışmaya devam eder.

Tüm yeni ortam değişkenlerinin listesi ve nereden alınacakları için
`.env.example`, Cloudflare panelinde elle yapılması gereken adımlar için
`CLOUDFLARE_SETUP.md` dosyasına bakın.

## Oyun Köşesi (Çapraz Bulmaca & Sudoku)

Site, tamamen etkileşimli ve kendi altyapımızda çalışan bir çapraz bulmaca + sudoku sistemi
içerir (üçüncü parti bir oyun eklentisi değildir).

**Ziyaretçi tarafı** — `/oyun-kosesi`, `/oyun-kosesi/bulmaca/<slug>`, `/oyun-kosesi/sudoku/<slug>`,
`/oyun-kosesi/arsiv`: gerçek klavye etkileşimi (tıkla-yaz, ok tuşları, Backspace, Soldan Sağa/
Yukarıdan Aşağıya geçişi), harf/kelime/bulmaca kontrolü ve sudoku için not modu, çakışma tespiti,
geri al/ileri al, ipucu ve tamamlanma ekranı içerir. İlerleme tarayıcının `localStorage`'ında
tutulur (sayfa yenilense de kaybolmaz). Doğru cevaplar **hiçbir zaman** ziyaretçiye gönderilen
HTML/JSON içinde yer almaz; her kontrol isteği sunucuda doğrulanır.

**Yönetim tarafı** — `/admin/oyunlar` altında:
- **Çapraz Bulmaca**: görsel bir ızgara editörü (kareye tıkla, harf yaz — JSON düzenlemek
  gerekmez), otomatik numaralandırma/kesişim tespiti, ipucu editörü, ve verdiğiniz
  `CEVAP | İpucu` listesinden ızgarayı otomatik yerleştirmeye çalışan bir oluşturucu.
- **Sudoku**: zorluk seviyesine göre (Kolay/Orta/Zor/Uzman) **tek çözümlü olduğu doğrulanmış**
  bulmacalar üreten bir otomatik oluşturucu (rastgele rakam silme değil, her silme sonrası
  çözüm sayısını kontrol eden bir algoritma kullanır) ve elle giriş için 9×9'luk bir editör
  (kaydedince otomatik doğrulanır: çakışma / çözümsüz / birden fazla çözüm uyarıları verir).
- Her iki oyun türü için de Taslak/Yayında/Arşiv durumları, kopyalama, silme, ön izleme ve
  **PNG olarak dışa aktarma** (gazetenin basılı sayısına eklemek için) mevcuttur. Yalnızca
  yayımlanmış oyunlar herkese açık URL'lerden erişilebilir; taslaklar URL tahmin edilse bile
  404 döner (yönetici olarak giriş yapmadığınız sürece).

## Proje Yapısı

```
app/
  app.py             Flask uygulaması ve tüm route'lar
  store.py           JSON veri okuma/yazma yardımcıları
  sections.py        Bölüm (Politika, Şehir, Kültür...) tanımları
  games_engine.py    Bulmaca numaralandırma/yerleştirme + sudoku çözücü/üretici (saf mantık)
  games_export.py    Bulmaca/sudoku için PNG dışa aktarma (Pillow)
  minecraft_service.py  Mojang/Crafatar/mc-heads.net üzerinden Minecraft profil çözümü (cache'li)
  runtime_env.py     Üretim/geliştirme tespiti (APP_ENV / SERVER_NAME) -- diğer modüllerin sıkılık kararları için
  turnstile.py       Cloudflare Turnstile sunucu tarafı doğrulama (Siteverify)
  storage.py         Cloudflare R2 istemcisi (S3 uyumlu API, boto3)
  uploads.py         Yükleme doğrulama + R2/yerel disk yönlendirme (tüm admin yüklemeleri buradan geçer)
  cf_access.py       Cloudflare Access imzalı kimlik doğrulama (opsiyonel, savunma derinliği)
  ratelimit.py        Hassas uç noktalar için basit bellek-içi hız sınırlama
  data/
    articles.json    Tüm makaleler (author_ids ile yazar profillerine bağlanır)
    issues.json      PDF gazete sayıları
    site.json        Site/masthead bilgileri
    users.json       Hesaplar: e-posta, şifre hash'i, hesap rolü (master_admin/author)
    authors.json     Yazar profilleri: biyografi, editoryal rol, Twitter/Minecraft, slug geçmişi
    audit_log.json   Hassas admin işlemlerinin kaydı
    messages.json    İletişim formu mesajları
    crosswords.json  Çapraz bulmacalar (ızgara + çözüm + ipuçları)
    sudokus.json     Sudokular (başlangıç ızgarası + çözüm, ayrı tutulur)
  templates/          Jinja2 şablonları (templates/games/ ve templates/admin/ dahil)
  static/
    css/style.css
    js/               crossword-play.js, sudoku-play.js, crossword-builder.js,
                       sudoku-builder.js, game-storage.js (localStorage yardımcıları),
                       author-card.js (paylaşılan yazar önizleme kartı)
    img/articles/     Makale görselleri
    img/authors/      Yazar profil/kapak fotoğrafları
    img/ari-logo.svg  Arı dergisi resmi logosu (Arı bağlamındaki sayfalarda masthead'i değiştirir)
    fonts/            FrutigerLTStd-Bold.otf — yalnızca "Buraya Bakarlar" başlığı için (lisanslı font)
    issues/           Yüklenen PDF sayılar (yalnızca R2 yapılandırılmamışken kullanılır)
scripts/
  migrate_media_to_r2.py  Mevcut yerel medyayı R2'ye taşıyan tek seferlik betik
.env.example          Tüm ortam değişkenlerinin listesi (yer tutucu değerlerle)
CLOUDFLARE_SETUP.md    Cloudflare panelinde elle yapılması gereken adımların kontrol listesi
```

## Güvenlik Notu

`app.py` şu an `debug=True` ile ve yalnızca `127.0.0.1` (localhost) üzerinde çalışacak şekilde
ayarlıdır — bu, geliştirme ve kişisel kullanım için uygundur ama **internete açık bir sunucuya
bu haliyle deploy etmeyin**. Gerçek bir yayına almadan önce: `debug=False` yapın, `python app.py`
yerine bir WSGI sunucusu kullanın (ör. `gunicorn app:app`), ve varsayılan admin şifresini mutlaka
değiştirin.

## Notlar

- Veri, veritabanı yerine JSON dosyalarında tutulur — küçük ölçekli, tek yönetici için
  yeterlidir. Farklı bir sunucuya taşırken `app/data` ve `app/static/img/articles` ile
  `app/static/issues` klasörlerini birlikte taşıyın.
- Gazete PDF'leri tarayıcının yerleşik PDF görüntüleyicisiyle okunur (`<iframe>`).
- `static/fonts/FrutigerLTStd-Bold.otf` lisanslı ticari bir fonttur (Frutiger LT Std, Monotype).
  Yalnızca sahip olunan bir lisansla kullanın; bu repoyu herkese açık paylaşırken bu dosyayı
  hariç tutmayı değerlendirin.
