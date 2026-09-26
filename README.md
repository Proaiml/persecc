# SecondX: High-Precision Infrastructure Telemetry & Monitoring Agent

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![InfluxDB](https://img.shields.io/badge/InfluxDB-v2.x-00C9FF.svg)](https://www.influxdata.com/)
[![Grafana](https://img.shields.io/badge/Grafana-dashboard_included-F46800.svg)](https://grafana.com/)
[![Platforms](https://img.shields.io/badge/platform-Windows_%7C_Linux-lightgrey.svg)](#-kurulum)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**SecondX**, sunucuları **saniyelik doğrulukla** izleyen hafif bir izleme ajanıdır. Her saniye CPU, RAM, disk ve ağ kullanımını ölçer. **CPU, RAM, disk okuma ve disk yazmada en çok kaynak tüketen süreçleri** tespit eder. Verileri **InfluxDB**'ye aktarır; **Grafana** panosu hazır gelir.

> Geleneksel izleme araçları (CloudWatch, Datadog, varsayılan Prometheus döngüleri) metrikleri **1-5 dakikalık** ortalamalarla toplar. Birkaç saniyelik CPU kilitlenmeleri, anlık disk patlamaları ve belleği hızla tüketen süreçler bu ortalamaların içinde kaybolur. SecondX bunları **saniyesinde ve süreç adıyla** gösterir.

> 🔒 **Temel ilke: izlenen sunucuya asla yük olmamak.** SecondX önemli sunucularda çalışacak şekilde tasarlandı. Kendisine ayrılan CPU veya RAM sınırını aştığı **anda durur**. Saniyelik hassasiyeti kaybederse durur. InfluxDB uzun süre kapalı kalırsa belleği şişirmez: veriyi diske bloklar halinde yazar ve belirlenen sınırlar aşılırsa durur. Ayrıntılar: [Sıkı mod](#-sıkı-mod-kritik-sunucular-için).

---

## 📑 İçindekiler

- [Kurumunuz için güven özeti](#%EF%B8%8F-kurumunuz-için-güven-özeti)
- [Öne çıkanlar](#-öne-çıkanlar)
- [Nasıl çalışır?](#%EF%B8%8F-nasıl-çalışır)
- [1 dakikada deneyin](#-1-dakikada-deneyin)
- [Kurulum](#-kurulum)
  - [A. Windows: tek dosyayla hızlı kurulum (önerilen)](#a-windows-tek-dosyayla-hızlı-kurulum-önerilen)
  - [B. InfluxDB + Grafana (Docker)](#b-influxdb--grafana-tek-komutla-docker)
  - [C. Windows: Python ile kurulum](#c-windows-python-ile-kurulum-alternatif)
  - [D. Linux sunucular](#d-linux-sunucular-systemd-servisi)
- [Sıkı mod (kritik sunucular için)](#-sıkı-mod-kritik-sunucular-için)
- [Olay ve risk tablosu](#-olay-ve-risk-tablosu)
- [Yapılandırma](#%EF%B8%8F-yapılandırma)
- [Grafana panosu](#-grafana-panosu)
- [Veri şeması](#-veri-şeması)
- [İşletim ve güvenlik](#%EF%B8%8F-i%CC%87%C5%9Fletim-ve-g%C3%BCvenlik)
- [Sorun giderme](#-sorun-giderme)
- [1.x'ten yükseltme](#-1xten-yükseltme)
- [Geliştirme ve testler](#-geliştirme-ve-testler)

---

## 🏛️ Kurumunuz için güven özeti

Bir izleme ajanını üretim sunucusuna kurmadan önce sorulması gereken soruların kısa cevapları. Her cevap koddan ve testlerden doğrulanabilir.

| Soru | Cevap |
| :--- | :--- |
| **Ne toplar?** | Her saniye: CPU, RAM, disk okuma/yazma ve ağ **sayıları** ile en çok tüketen süreçlerin **adları** (ör. `sqlservr.exe`). |
| **Ne toplamaz?** | Dosya içerikleri, süreçlerin komut satırları, kullanıcı adları, parolalar, ağ trafiğinin içeriği, ekran veya klavye. Hiçbir sürece müdahale etmez, yalnızca sayaçları **okur**. |
| **Veri nereye gider?** | **Yalnızca sizin belirttiğiniz InfluxDB adresine.** Başka hiçbir yere bağlanmaz: güncelleme denetimi, kullanım istatistiği, "eve arama" yoktur. Kodda ağa çıkan tek yer `influx_exporter.py` içindeki `InfluxSink`'tir. `netstat` / Kaynak İzleyicisi'nde yalnızca InfluxDB bağlantısını görürsünüz. |
| **Hangi yetkiyle çalışır?** | Windows: SYSTEM (tüm süreçlerin sayaçlarını okumak için gerekir). Linux: root **değil**; ayrı bir kullanıcı ve yalnızca 2 okuma yetkisi (`CAP_SYS_PTRACE`, `CAP_DAC_READ_SEARCH`). |
| **Sunucuyu yorabilir mi?** | Kendine ayrılan sınır: tek çekirdeğin **%25**'i ve **200 MB** RAM. Aşarsa **anında durur**. Ölçülen: ortalama **%1.8** CPU (8 çekirdekli bir sunucuda toplam CPU'nun ~%0.2'si) ve **59 MB** RAM (16 GB'ın ~%0.4'ü). |
| **Diski doldurabilir mi?** | Hayır. InfluxDB'ye ulaşılamazken bekleyen veri en fazla 1 GB olur ve diskte her zaman en az 1 GB boş yer bırakılır; aksi halde ajan durur. Günlükler 14 günde bir silinir. |
| **Token (şifre) nerede durur?** | Windows: `C:\ProgramData\SecondX\secondx.token`, yalnızca SYSTEM ve Yöneticiler açabilir. Linux: `/etc/secondx/secondx.env`, yalnızca root okur. Ayar dosyasında ve ekranda görünmez. Ajana InfluxDB'de yalnızca **yazma** yetkili ayrı bir token verin. |
| **Sorun çıkarsa ne olur?** | Ajan ya sorunsuz çalışır ya da **durur**; yavaşlayarak, belleği şişirerek çalışmaya devam etmez. Tüm olasılıklar: [Olay ve risk tablosu](#-olay-ve-risk-tablosu). |
| **Kodu kim denetleyebilir?** | Tamamı açık kaynak (MIT), yaklaşık **1 700 satır** Python ve 48 otomatik test. Kurulum dosyasını [kaynaktan kendiniz derleyebilirsiniz](#a-windows-tek-dosyayla-hızlı-kurulum-önerilen). |
| **Nasıl kaldırılır?** | Ayarlar → Uygulamalar → **SecondX telemetry agent** → Kaldır. Görev, program, ayarlar ve kayıt defteri girdisi silinir; iz kalmaz. |

---

## ✨ Öne çıkanlar

| | |
| :--- | :--- |
| ⏱️ **Gerçek saniyelik örnekleme** | Windows'ta tüm süreçler tek bir çekirdek çağrısıyla okunur: **~5 ms**. Ajan her saniye düzenli ölçer. |
| 🔝 **En çok tüketenler** | CPU, RAM, disk okuma, disk yazma için ilk N süreç (varsayılan 6). Aynı adlı süreçler toplanır (ör. 30 × `chrome.exe` → tek satır). |
| 🛑 **Sorun çıkarsa anında durur** | Kendi CPU/RAM sınırı, saniyelik hassasiyet ve dışa aktarım güvenlik sınırları sürekli denetlenir. Sınır aşılırsa ajan ~1 sn içinde durur ve nedeni günlüğe yazar. |
| 🧱 **Veri kaybı yok, RAM şişmez** | InfluxDB kapansa bile ölçüm durmaz. Veri bellekte küçük bir tamponda, taşarsa **ayrı bir iş parçacığında diske bloklar halinde** bekletilir. Bağlantı gelince **orijinal zaman damgalarıyla** yazılır. |
| 🔁 **Kontrollü yeniden başlatma** | Geçici hatalarda 5 dk arayla en fazla 5 deneme. Kaynak sınırı aşımında **asla** kendiliğinden yeniden başlamaz. |
| 🚀 **Toplu yazım** | Her saniyenin tüm noktaları tek istekte gönderilir; ölçüm döngüsü asla ağı beklemez. |
| 🖥️ **Çoklu sunucu** | Her nokta `host` etiketi taşır. Tüm sunucular tek bucket'ta, panoda sunucu seçiciyle izlenir. |
| 📊 **Hazır pano** | `docker compose up -d` → Grafana'da veri kaynağı ve pano otomatik kurulu gelir. |
| 🔐 **Kurumsal kurulum** | Windows: tek dosyalık `SecondX-Setup.exe` (Python gerekmez, sessiz kurulum desteği, "Uygulamalar"dan kaldırma). Linux: root olmayan kullanıcı + güvenlik sıkılaştırmalı systemd servisi. Token yalnızca yöneticilerin açabildiği dosyada. |
| 🗂️ **InfluxDB'siz mod** | Token girilmezse metrikler günlük JSON-lines dosyalarına yazılır (otomatik saklama süresiyle). |

---

## 🏗️ Nasıl çalışır?

```mermaid
flowchart LR
    subgraph S["Her sunucu"]
        A["SecondX ajanı<br/>(her 1 sn)"]
    end
    A -- "sistem: CPU, RAM, disk, ağ<br/>süreç: ilk N tüketici" --> B[("InfluxDB v2")]
    A -. "InfluxDB yoksa" .-> F["data/metrics-GÜN.jsonl"]
    B --> G["Grafana<br/>SecondX - Host Overview"]
```

| Ölçülen | Kapsam | Birim |
| :--- | :--- | :--- |
| CPU kullanımı | sistem + süreç başına | % (tüm çekirdeklere göre) |
| RAM kullanımı | sistem (% ve GB) + süreç başına | % |
| Disk okuma / yazma | sistem + süreç başına | KB/s |
| Ağ gönderilen / alınan | sistem | KB/s |

> ℹ️ İşletim sistemleri süreç başına ağ trafiği sayacı sunmaz; ağ bu yüzden sistem genelinde ölçülür.

### Akış diyagramı: her saniye ne olur?

```mermaid
flowchart TD
    S(["Servis başlar<br/>Windows: --supervise · Linux: systemd"]) --> Y{"config.json geçerli mi?"}
    Y -->|hayır| C2["Dur · kod 2<br/>yapılandırma hatası"]
    Y -->|evet| T["Saniye başı: ölçüm al<br/>sistem + en çok tüketen N süreç"]
    T --> Q["Noktaları RAM tamponuna ekle<br/>ve göndericiyi uyandır"]
    T -. "beklenmedik hata" .-> C1["Dur · kod 1<br/>hata"]
    Q --> G1{"Dışa aktarım güvenlik sınırı?<br/>kesinti > 6 sa · spool > 1 GB<br/>boş disk < 1 GB · RAM tamponu dolu"}
    G1 -->|evet| C4["Dur · kod 4<br/>veri diskte kalır"]
    G1 -->|hayır| G2{"Kendi kaynağı aşıldı mı?<br/>RAM > 200 MB<br/>CPU 3 sn ort. > %25"}
    G2 -->|evet| C3["Dur · kod 3<br/>kaynak sınırı"]
    G2 -->|hayır| G3{"Art arda 3 ölçüm<br/>500 ms'den fazla geç mi?"}
    G3 -->|evet| C5["Dur · kod 5<br/>hassasiyet kaybı"]
    G3 -->|hayır| W["Bir sonraki saniyeyi bekle"] --> T

    subgraph P["Arka plan: ölçüm döngüsüyle aynı anda çalışır"]
        direction TB
        SN["Canlı gönderici<br/>yeni noktaları hemen yazar<br/>eski verinin arkasında beklemez"] --> I[("InfluxDB")]
        SN -. "InfluxDB yanıt vermezse" .-> SP["Diske yazıcı<br/>1500 noktalık bloklar: spool/"]
        SP -. "bağlantı gelince" .-> UW["Yükleme işçileri 1-4<br/>gerektikçe açılır, iş bitince kapanır<br/>ortak CPU bütçesi %10"]
        UW --> I
    end
    Q == "her saniye" ==> SN

    C1 --> R{"5 yeniden deneme<br/>doldu mu?"}
    C5 --> R
    R -->|hayır| B["5 dk bekle"] --> S
    R -->|evet| K(["Kapalı kalır<br/>yönetici bakmalı"])
    C2 --> K
    C3 --> K
    C4 --> K

    classDef dur fill:#fde2e2,stroke:#c0392b,color:#000
    classDef tamam fill:#e3f4e1,stroke:#2e7d32,color:#000
    classDef arka fill:#e3eefc,stroke:#1f5fa8,color:#000
    class C1,C2,C3,C4,C5,K dur
    class W tamam
    class SN,SP,UW,I arka
```

Ölçüm döngüsü InfluxDB'ye kendisi yazmaz: noktaları RAM tamponuna koyar ve **aynı anda çalışan** canlı göndericiyi uyandırır. Canlı gönderici noktaları hemen (normalde milisaniyeler içinde, bir sonraki saniye gelmeden) InfluxDB'ye yazar. Böylece InfluxDB yavaşlasa, donsa ya da kapansa bile ölçüm döngüsü hiç beklemez ve saniyelik ritim bozulmaz. Kesintiden kalan veriyi ayrı yükleme işçileri gönderir; canlı veri onların arkasında sıraya girmez. Mavi kutular bu arka plan işidir. Kırmızı kutular ajanın durduğu noktalardır. Kod 1 ve 5 geçici sorunlardır: 5 dk arayla en fazla 5 kez yeniden denenir. Kod 2, 3 ve 4 bir yöneticinin bakmasını gerektirir: ajan kendiliğinden yeniden başlamaz. Ayrıntılar: [Sıkı mod](#-sıkı-mod-kritik-sunucular-için).

---

## ⚡ 1 dakikada deneyin

```bash
git clone https://github.com/Proaiml/persecc.git
cd persecc
pip install -r requirements.txt
python SecondX.py --once
```

```
SecondX 2.3.0 | host WEB-01 | interval 1.00s
CPU   7.6%   RAM  77.4% (24.7 GB)   Disk R/W    108.7 /    164.6 KB/s   Net out/in      0.6 /      1.1 KB/s

Top cpu (%):
  1. chrome.exe                                     1.75
  2. python.exe                                     1.48
  ...
Top disk_write (KB/s):
  1. sqlservr.exe                                 870.20
  ...
```

Sürekli çalıştırmak için `python SecondX.py`. InfluxDB token'ı girilmediyse veriler `data/` klasörüne yazılır.

---

## 🚀 Kurulum

### A. Windows: tek dosyayla hızlı kurulum (önerilen)

Python veya başka bir program kurmanız gerekmez.

1. [Releases](https://github.com/Proaiml/persecc/releases/latest) sayfasından `SecondX-Setup.exe` ve `SHA256SUMS.txt` dosyalarını indirin.
2. **Dosyanın değiştirilmediğini doğrulayın** (önerilir). Çıkan değer `SHA256SUMS.txt` içindekiyle aynı olmalı:
   ```powershell
   Get-FileHash .\SecondX-Setup.exe -Algorithm SHA256
   ```
3. `SecondX-Setup.exe`'ye çift tıklayın ve yönetici onayını verin. Kurulum önce **ne yapacağını** gösterir, sonra sırayla sorar: veri nereye yazılsın (InfluxDB ya da yerel dosya), InfluxDB adresi, org, bucket, token (yazarken görünmez), panoda görünecek sunucu adı.
4. Kurulum bağlantıyı dener, ajanı başlatır ve çalıştığını günlükten doğrulayarak biter.

```mermaid
flowchart LR
    A(["SecondX-Setup.exe"]) --> B["Yönetici onayı"]
    B --> C["Ne yapılacağını göster<br/>onay al"]
    C --> D["Ayarları sor<br/>InfluxDB / yerel dosya"]
    D --> E["Programı kopyala<br/>Program Files"]
    E --> F["Ayarlar + token<br/>yalnız SYSTEM ve Yöneticiler"]
    F --> G{"Bağlantı denemesi<br/>--check"}
    G -->|başarılı| H["Görev + kaldırma kaydı"]
    G -->|sorun var| U{"Yine de kur?"}
    U -->|evet| H
    U -->|hayır| X(["İptal"])
    H --> I["Başlat ve günlükten doğrula"]
    I --> J(["Kurulum tamam"])
```

| Ne | Nerede | Kim erişebilir |
| :--- | :--- | :--- |
| Program (`SecondX.exe`) | `C:\Program Files\SecondX` | Herkes okur, yalnızca yöneticiler değiştirir |
| Ayarlar, günlükler, bekleyen veri | `C:\ProgramData\SecondX` | Yalnızca SYSTEM ve Yöneticiler |
| InfluxDB token'ı | `C:\ProgramData\SecondX\secondx.token` | Yalnızca SYSTEM ve Yöneticiler |
| Otomatik başlatma | Görev Zamanlayıcı → `SecondX` (açılışta, SYSTEM) | |
| Kaldırma | Ayarlar → Uygulamalar → SecondX telemetry agent | |

**Güncelleme:** yeni `SecondX-Setup.exe`'yi çalıştırın. Mevcut ayarları korumayı teklif eder; çalışan ajanı durdurur, programı yeniler ve yeniden başlatır. Diskte bekleyen veri korunur.

**Toplu dağıtım (soru sormadan):** GPO, SCCM/Intune ya da uzak PowerShell ile:

```powershell
# InfluxDB'ye yazan kurulum (token'ı dosyadan okur)
SecondX-Setup.exe --quiet --url http://influx.kurum.local:8086 --org secondx --bucket secondx --token-file \\paylasim\secondx.token
# Yalnızca yerel dosyaya yazan kurulum
SecondX-Setup.exe --quiet --local
# Kaldırma (ayarları korumak için --keep-data)
SecondX-Setup.exe --uninstall --quiet
```

> ⚠️ `--token` seçeneği de vardır, ancak komut satırları süreç listesinde diğer kullanıcılara görünebilir. Toplu dağıtımda `--token-file` kullanın.

**Windows SmartScreen / antivirüs uyarısı:** `SecondX-Setup.exe` dijital imzalı değildir. Windows "Windows kişisel bilgisayarınızı korudu" diyebilir: **Ek bilgi → Yine de çalıştır**. Kurumunuz imzasız dosyalara izin vermiyorsa iki yol vardır:

- Dosyayı SHA256 değeriyle güvenilir listeye alın.
- Kurulum dosyasını **kaynaktan kendiniz derleyin** (temiz bir sanal ortam kurar, yalnızca `requirements.txt` ve PyInstaller'ı kullanır):

  ```powershell
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1     # → dist\SecondX-Setup.exe + SHA256SUMS.txt
  ```

### B. InfluxDB + Grafana tek komutla (Docker)

Mevcut bir InfluxDB v2'niz varsa bu adımı atlayın.

```bash
cp .env.example .env        # Windows: copy .env.example .env
# .env içindeki TÜM şifreleri ve INFLUXDB_TOKEN'ı değiştirin
docker compose up -d
```

| Servis | Adres | Giriş |
| :--- | :--- | :--- |
| InfluxDB | http://localhost:8086 | `.env` → `INFLUXDB_ADMIN_USER` / `INFLUXDB_ADMIN_PASSWORD` |
| Grafana | http://localhost:3000 | `.env` → `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |

Grafana açılışta **SecondX - Host Overview** panosunu gösterir. Veri kaynağı token'ı `.env`'den otomatik alınır.

> 💡 Güçlü bir token üretmek için: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

### C. Windows: Python ile kurulum (alternatif)

Kurulum dosyası yerine kaynak koddan kurmak isterseniz. **Yönetici** olarak açılmış PowerShell'de, SecondX klasörünün içinde:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -InfluxToken "<INFLUXDB_TOKEN>"
```

Betik şunları yapar:

1. Klasörün içinde özel bir Python ortamı (`.venv`) kurar ve bağımlılıkları yükler.
2. Token'ı yalnızca SYSTEM ve Yöneticiler'in açabildiği `secondx.token` dosyasına yazar (config dosyasına ve herkesin okuyabildiği ortam değişkenlerine yazılmaz).
3. `--check` ile yapılandırmayı ve InfluxDB bağlantısını doğrular.
4. **SecondX** adlı zamanlanmış görevi oluşturur: bilgisayar açılınca SYSTEM hesabıyla, `--supervise` (gözetmen) modunda başlar. Yeniden başlatma politikasını gözetmen uygular (bkz. [Sıkı mod](#-sıkı-mod-kritik-sunucular-için)). Süre sınırı yoktur.

| İşlem | Komut |
| :--- | :--- |
| Durum | `Get-ScheduledTask -TaskName SecondX \| Get-ScheduledTaskInfo` |
| Günlük | `Get-Content .\logs\secondx.log -Tail 50 -Wait` |
| Durdur / başlat | `Stop-ScheduledTask SecondX` / `Start-ScheduledTask SecondX` |
| Kaldır | `powershell -ExecutionPolicy Bypass -File .\uninstall_windows.ps1 -RemoveToken` |

Servis kurmadan elle çalıştırmak için `run_agent.bat` dosyasına çift tıklayın.

### D. Linux sunucular (systemd servisi)

```bash
sudo ./install_linux.sh --token "<INFLUXDB_TOKEN>"
```

Betik şunları yapar:

1. `secondx` adlı, oturum açamayan bir **sistem kullanıcısı** oluşturur. Ajan root olarak **çalışmaz**.
2. Dosyaları `/opt/secondx` altına kopyalar ve özel bir `venv` kurar. Mevcut `config.json` korunur, güncellemede üzerine yazılmaz.
3. Token'ı `/etc/secondx/secondx.env` dosyasına yazar (izin `600`, yalnızca root okur).
4. `secondx.service`'i kurup başlatır.

Servis, tüm süreçlerin disk sayaçlarını okuyabilmek için yalnızca `CAP_SYS_PTRACE` ve `CAP_DAC_READ_SEARCH` yetkilerini alır. `ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges` ve `PrivateTmp` ile sıkılaştırılmıştır.

Yeniden başlatma politikasını systemd uygular: hata (1) ve hassasiyet kaybında (5) 5 dk arayla, 2 saat içinde en fazla 5 kez. Çıkış kodu 2, 3 ve 4'te **yeniden başlatmaz** (`RestartPreventExitStatus`). Ayrıca çekirdek düzeyinde ikinci bir emniyet vardır: `MemoryMax=400M`, `CPUQuota=50%`. Bunlar ajanın kendi sınırlarının (200 MB, %25) üstündedir; ajanın kendi denetimi hiç çalışmasa bile sunucu korunur.

| İşlem | Komut |
| :--- | :--- |
| Durum | `systemctl status secondx` (durduysa `status=N` çıkış kodunu gösterir) |
| Canlı günlük | `journalctl -u secondx -f` |
| Yapılandırma | `sudo nano /opt/secondx/config.json && sudo systemctl restart secondx` |
| Güncelleme | `git pull && sudo ./install_linux.sh` |
| Kaldırma | `sudo systemctl disable --now secondx && sudo rm -rf /opt/secondx /etc/secondx /etc/systemd/system/secondx.service` |

---

## 🔒 Sıkı mod (kritik sunucular için)

`strict_mode: true` varsayılandır. Amaç şudur: izleme ajanını üretim sunucusuna **korkmadan** koyabilmek. Ajan ya saniyelik hassasiyetle sorunsuz çalışır ya da **hemen durur**. Arada bir durum yoktur: yavaşlayan, belleği şişen, sunucuyu yoran bir ajan olmaz.

### Anında durduran denetimler

| Denetim | Varsayılan | Ne zaman durur? | Çıkış kodu |
| :--- | :--- | :--- | :---: |
| **Kendi RAM'i** | `limits.max_memory_mb: 200` | Her saniye ölçülür; sınır aşıldığı **ilk örnekte** | `3` |
| **Kendi CPU'su** | `limits.max_cpu_percent: 25` (tek çekirdeğin %'si) | Son `cpu_window_seconds` (3 sn) ortalaması sınırı aşınca. İlk 5 sn (Python'un açılışı) ısınma sayılır | `3` |
| **Saniyelik hassasiyet** | `precision.max_missed_slots: 3`, `slot_tolerance_ms: 500` | Art arda 3 örnek zamanını 500 ms'den fazla kaçırırsa. Saat atlaması (>30 sn, ör. uyku / NTP) durdurmaz, yeniden hizalar | `5` |
| **InfluxDB kesintisi** | `influx.max_outage_hours: 6` | InfluxDB 6 saatten uzun süre yazılamazsa | `4` |
| **Disk tamponu boyutu** | `influx.max_spool_mb: 1024` | Diskteki bekleyen veri 1 GB'ı geçerse | `4` |
| **Boş disk alanı** | `influx.min_free_disk_mb: 1024` | Diskte 1 GB'tan az yer kalırsa (sunucunun diskini doldurmaz) | `4` |
| **RAM tamponu** | `influx.max_memory_points: 30000` | Disk tamponu yetişemez / yazılamazsa (veri **sessizce atılmaz**) | `4` |
| **Beklenmedik hata** | | Bir ölçüm turu hata verirse | `1` |

Durunca günlüğe tek satırlık, açık bir neden yazılır:

```
CRITICAL secondx: STOPPING: own memory 212 MB exceeds limit 200 MB (exit code 3 = own CPU/RAM limit exceeded).
```

### Çıkış kodları ve yeniden başlatma

| Kod | Anlamı | Yeniden başlatılır mı? |
| :---: | :--- | :--- |
| `0` | İstenerek durduruldu | Hayır |
| `1` | Beklenmedik hata | **Evet**: 5 dk arayla en fazla 5 kez |
| `2` | Yapılandırma hatası | Hayır (önce config düzeltilmeli) |
| `3` | Kendi CPU/RAM sınırı aşıldı | **Asla**: bir yönetici bakmalı |
| `4` | Dışa aktarım güvenlik sınırı (kesinti / disk) | **Asla**: bir yönetici bakmalı |
| `5` | Saniyelik hassasiyet kayboldu (sunucu aşırı yüklü) | **Evet**: 5 dk arayla en fazla 5 kez |

Politika `config.json` → `restart` bölümündedir: `max_attempts: 5`, `delay_seconds: 300`. Ajan `reset_after_seconds` (1 saat) boyunca sorunsuz çalışırsa deneme sayacı sıfırlanır. 5 deneme de başarısız olursa ajan kapalı kalır.

- **Windows:** görev `SecondX.py --supervise` ile çalışır. Gözetmen, ajanı ayrı bir süreç olarak başlatır ve politikayı uygular. Gözetmen öldürülürse (ör. Görev Zamanlayıcı → *Sonlandır*) ajan da ~1 sn içinde kendiliğinden kapanır; sahipsiz süreç kalmaz. Gözetmen günlüğü: `logs/secondx-supervisor.log`.
- **Linux:** aynı politikayı `secondx.service` içinde systemd uygular.

### InfluxDB kesintisinde veri nasıl bekletilir?

```mermaid
flowchart LR
    A["Ölçüm döngüsü<br/>(her 1 sn, asla beklemez)"] --> R["RAM tamponu<br/>(en fazla 30 000 nokta)"]
    R -->|"canlı gönderici"| I[("InfluxDB")]
    R -->|"kesintide: diske yazıcı<br/>1500 noktalık bloklar"| D["spool/block-*.lp<br/>(hazır InfluxDB biçimi)"]
    D -->|"bağlantı gelince<br/>1-4 paralel yükleme işçisi"| I
```

1. Ölçüm döngüsü noktaları yalnızca RAM tamponuna ekler; ağı ya da diski **hiç** beklemez. Saniyelik ritim bu yüzden bozulmaz.
2. InfluxDB yanıt vermezse **ayrı bir iş parçacığı** en eski noktaları 1500'lük bloklar halinde diske yazar (`fsync` + atomik ad değiştirme; yarım kalmış blok olmaz). Bloklar doğrudan InfluxDB'nin satır biçimindedir: gönderirken dönüştürme gerekmez (blok başına CPU 6.6 ms → ~0). RAM kullanımı sabit kalır: testte 45 sn kesintide ajan 60 MB'ta kaldı.
3. Bağlantı gelince **canlı veri hemen** gönderilir; diskteki birikmiş bloklar onu bekletmez. Birikmiş blokları ayrı **yükleme işçileri** gönderir, hepsi **orijinal zaman damgalarıyla**. Panoda boşluk kalmaz.
4. Yükleme işçileri gerektikçe açılır: gönderim süresinin çoğu sunucunun yanıtını beklemekle geçiyorsa (uzak InfluxDB), saniyede bir işçi eklenir, en fazla `backlog_max_workers` (4). Beklerken Python kilidi (GIL) serbest kalır; paralellik ölçüm döngüsünden zaman çalmaz. İş bitince işçiler kapanır. Hepsi **tek bir ortak CPU bütçesi** (`backlog_cpu_percent: 10`) içinde kalır.
5. InfluxDB'nin kesin olarak reddettiği bir blok (HTTP 400) `*.rejected`, okunamayan bir blok `*.bad` olarak kenara alınır ve incelemeye bırakılır. Kuyruk tek bir bozuk blok yüzünden takılmaz.
6. Ajan kesinti sırasında durdurulursa (servis durdurma, yeniden başlatma) RAM'deki noktalar ve o an yazılmakta olan paket de diske yazılır ve bir sonraki açılışta gönderilir. Aynı noktanın iki kez yazılması InfluxDB'de zararsızdır (üzerine yazar); kaybolması ise kabul edilemez.

**Gönderici her zaman canlı mı?** Ölçüm döngüsü her saniye arka plan iş parçacıklarını da denetler. Biri çökerse ya da tek bir yazma işlemi 30 sn'den uzun asılı kalırsa (normalde `timeout_ms` = 5 sn ile sınırlı) ajan kod 1 ile durur ve 5 dk sonra temiz bir bağlantıyla yeniden başlatılır. InfluxDB bağlantısı açık tutulur (keep-alive havuzu): her saniye yeniden bağlantı kurma gecikmesi olmaz. Durum satırı 5 dakikada bir en kötü teslim gecikmesini de yazar (`max delay ... ms`).

### Ölçülen ayak izi (Windows 11, gerçek InfluxDB 2.7)

| Senaryo | Sonuç |
| :--- | :--- |
| Normal çalışma | CPU ortalama **%1.8**, en yüksek %4.7 (tek çekirdeğin %'si) · RAM **59 MB** |
| 45 sn InfluxDB kesintisi | 3 blok diske yazıldı · RAM en yüksek 60 MB · kesinti sonrası **boşluksuz** toparlandı |
| Kesinti sınırı aşıldı | Ajan kendiliğinden durdu (kod 4) · bekleyen veri diskte kaldı, yeniden başlayınca gönderildi |
| InfluxDB 60 sn **dondu** (bağlantı açık, yanıt yok) | Ölçüm hiç aksamadı: 112 sn'de 112 örnek, aralık 979-1016 ms · veri diske gitti, çözülünce eksiksiz yüklendi |
| 6 saatlik birikmiş veri (540 000 nokta) | **6.4 sn**'de yüklendi (2.1'de 110 sn) · RAM 62 MB |
| 24 saatlik birikmiş veri (2 160 000 nokta) | 1 işçi 46.5 sn, 4 işçi **33.2 sn** · CPU ortalama %13.5, 3 sn en yüksek %16.9 (sınır 25) · yükleme sırasında canlı veri en fazla 1 sn geride · ölçüm ritmi 1000 ms'den en fazla 19 ms saptı, **eksik saniye yok** |
| RAM sınırı 50 MB'a düşürüldü | Ajan **1.2 sn** içinde durdu (kod 3) · gözetmen yeniden başlatmadı |
| Gözetmen sert biçimde öldürüldü | Ajan 1.2 sn içinde kendiliğinden kapandı |

> ℹ️ Testteki InfluxDB aynı bilgisayarda çalıştığı için yükleme hızını sunucunun kendi yazma hızı sınırlar. Ağ gecikmesi olan uzak bir InfluxDB'de paralel işçilerin kazancı daha büyüktür.

> ℹ️ `strict_mode: false` yapılırsa ajan hatalarda durmaz, günlüğe yazıp devam eder ve geride kalırsa sessizce yeniden hizalanır. Diske bekletme ve güvenlik sınırları bu modda da geçerlidir. Kritik sunucular için önerilmez.

---

## 🧯 Olay ve risk tablosu

Bir sunucuda olabilecek tüm olaylar, ajanın her birine tepkisi ve sizin yapmanız gerekenler.

- **Yıllık olasılık:** tipik bir kurum sunucusu için mühendislik tahminidir; kendi ortamınıza göre değişir. Ölçülmüş değerler ayrıca belirtilmiştir.
- **Veri korunumu:** o olay sırasında ölçülen verinin yüzde kaçının sonunda InfluxDB'ye ulaştığı. ✓ = gerçek InfluxDB ile ya da otomatik testle doğrulandı.

### InfluxDB ve ağ

| # | Olay | Yıllık olasılık | Ajanın tepkisi | Veri korunumu | Sizin yapmanız gereken |
| :-: | :--- | :-: | :--- | :-: | :--- |
| 1 | InfluxDB kısa süre kapalı (bakım, yeniden başlatma, ağ kopması; 6 saatten kısa) | %60 | Ölçüm sürer. Veri RAM'den diske bloklar halinde geçer. Bağlantı gelince canlı veri hemen, birikmiş veri paralel yüklenir | **%100** ✓ | Hiçbir şey |
| 2 | InfluxDB **donar** (bağlantı açık, yanıt yok) | %10 | Her yazma 5 sn'de zaman aşımına uğrar ve kesinti gibi davranılır. Ölçüm hiç aksamaz (testte 60 sn donmada 112 sn'de 112 örnek) | **%100** ✓ | Hiçbir şey |
| 3 | Uzun kesinti (6 saatten fazla) | %5 | İlk 6 saatin verisi diskte tutulur, sonra ajan durur (kod 4) | İlk 6 sa **%100** ✓; duruştan sonrası ölçülmez | InfluxDB'yi düzeltip görevi başlatın; diskteki veri kendiliğinden yüklenir |
| 4 | Token iptal edildi / yetkisi yok (HTTP 401/403) | %5 | Kesinti gibi: veri diske gider, günlüğe nedeni yazılır; 6 sa içinde düzelmezse durur (kod 4) | **%100** (6 sa içinde düzeltilirse) | Token dosyasını güncelleyip görevi yeniden başlatın |
| 5 | InfluxDB veriyi reddeder (HTTP 400) | <%1 | Blok `*.rejected` olarak saklanır, kuyruk devam eder | Diskte **%100** ✓ | Günlükteki nedene bakın |
| 6 | Yavaş / uzak InfluxDB | %20 | Canlı veri önce yazılır; birikmiş veri 1-4 paralel işçiyle yüklenir | **%100** ✓ | Hiçbir şey |

### Sunucu ve disk

| # | Olay | Yıllık olasılık | Ajanın tepkisi | Veri korunumu | Sizin yapmanız gereken |
| :-: | :--- | :-: | :--- | :-: | :--- |
| 7 | Planlı yeniden başlatma / kapanma (Windows güncellemesi vb.) | %95 | Bekleyen veri 5 sn içinde gönderilir, kalanı diske yazılır. Açılışta ajan kendiliğinden başlar | **%100** | Hiçbir şey |
| 8 | Elektrik kesintisi / sert kapanma | %10 | Açılışta kendiliğinden başlar. Bloklar atomik yazıldığı için yarım dosya oluşmaz | Normalde son **≤1 sn** kaybolur; InfluxDB de o sırada kapalıysa en fazla son **~1 dk** (RAM'de bekleyen, diske henüz geçmemiş <1500 nokta) | Hiçbir şey |
| 9 | Disk doluyor | %5 | Diskte 1 GB boş yer kalınca durur (kod 4); sunucunun diskini asla doldurmaz | Duruşa kadar **%100** ✓ | Yer açıp görevi başlatın |
| 10 | Okunamayan / bozuk bekleyen blok (disk hatası) | <%1 | Blok `*.bad` olarak kenara alınır, kuyruk devam eder | O blok (**≈1 dk**) dışında **%100** ✓ | Hiçbir şey |
| 11 | Sunucu %100 yükte, ajan saniyeyi tutturamıyor | %3 | Art arda 3 kaçırılan saniyede durur (kod 5); 5 dk sonra yeniden dener, en fazla 5 kez | Kaçırılan saniyeler ölçülmemiştir | Sürekliyse `interval_seconds` değerini artırın |
| 12 | Saat atlaması (NTP düzeltmesi, sanal makine uykusu) | %10 | 30 sn'den büyük atlamada yeniden hizalanır, durmaz | **%100** ✓ | Hiçbir şey |

### Ajanın kendisi

| # | Olay | Yıllık olasılık | Ajanın tepkisi | Veri korunumu | Sizin yapmanız gereken |
| :-: | :--- | :-: | :--- | :-: | :--- |
| 13 | Ajan RAM sınırını aşar (200 MB) | <%1 (ölçülen 59-65 MB = sınırın **%33**'ü) | Aşıldığı ilk saniyede durur (kod 3); kendiliğinden yeniden **başlamaz** ✓ | Duruşa kadar **%100** | Günlükteki değere bakıp `limits.max_memory_mb`'ı yükseltin |
| 14 | Ajan CPU sınırını aşar (%25) | %1 (ölçülen ort. %1.8, en yüksek %17 = sınırın **%68**'i) | 3 sn ortalaması aşınca durur (kod 3) ✓ | Duruşa kadar **%100** | Binlerce süreçli sunucularda `limits.max_cpu_percent`'i yükseltin |
| 15 | Yazılım hatası | <%1 (48 otomatik test) | Durur (kod 1); 5 dk arayla en fazla 5 kez yeniden başlar ✓ | **%100** (veri diske yazılır) | 5 deneme de başarısızsa günlüğü iletin |
| 16 | Gönderici iş parçacığı çöker ya da bir yazma 30 sn'den uzun takılır | <%1 | Her saniye denetlenir; ajan durur (kod 1) ve temiz bağlantıyla yeniden başlar. Yazılmakta olan paket diske kaydedilir ✓ | **%100** | Hiçbir şey |
| 17 | Biri ajanı elle ikinci kez çalıştırır | %2 | İkinci kopya hemen çıkar (kod 2); çalışan ajan etkilenmez ✓ | **%100** | Hiçbir şey |
| 18 | Görev / gözetmen zorla sonlandırılır | %2 | Ajan ~1 sn içinde kendiliğinden kapanır, sahipsiz süreç kalmaz ✓ | Sonlandırmadan sonrası ölçülmez | Görevi yeniden başlatın |
| 19 | Yapılandırma hatası | %5 (kurulumda) | Kurulumdaki bağlantı denemesi yakalar; çalışırken ajan başlamaz (kod 2) | | Günlükte belirtilen satırı düzeltin |

### Kurum ortamı

| # | Olay | Yıllık olasılık | Ajanın tepkisi | Sizin yapmanız gereken |
| :-: | :--- | :-: | :--- | :--- |
| 20 | Antivirüs / EDR imzasız `SecondX-Setup.exe`'yi engeller | %20 | | SHA256 ile güvenilir listeye alın ya da [kaynaktan derleyin](#a-windows-tek-dosyayla-hızlı-kurulum-önerilen) |
| 21 | Korumalı sistem süreçleri | %100 (her sistemde var) | Okunamayan süreçler atlanır, hata üretmez | Hiçbir şey |
| 22 | Aynı miktarda kaynak tüketen süreçler | CPU ölçümlerinin **%37**'sinde (ölçüldü: 30 örneğin 11'i) | Eşit sırayı alırlar, sınırdakilerin hiçbiri dışarıda kalmaz ✓ | Hiçbir şey |

### Beklenen yıllık veri bütünlüğü (örnek hesap)

Kaybın en büyük kaynağı ajanın kendisi değil, ajan **bilerek durduğunda** bunu fark edip yeniden başlatma süresidir. Tablodaki tahminlerle:

| Kalem | Hesap | Beklenen kayıp / yıl |
| :--- | :--- | :-: |
| Durduran olaylar (#3, #9, #13, #14) | (%5 + %5 + %1 + %1) = %12 × fark edilme süresi **24 sa** | 2.9 sa |
| Kendiliğinden yeniden başlayanlar (#11, #15, #16) | (%3 + %1 + %1) = %5 × **5 dk** | 0.25 dk |
| Sert kapanma (#8) | %10 × 1 sn | 0.1 sn |
| **Toplam** | 2.9 sa / 8 760 sa | **%0.033 → veri bütünlüğü ≈ %99.97** |

Grafana'da "bu sunucudan 2 dakikadır veri gelmiyor" alarmı kurar ve 1 saat içinde müdahale ederseniz: %12 × 1 sa ≈ 7 dk/yıl → **≈ %99.999**. Önerimiz bu alarmı mutlaka kurmanızdır.

---

## ⚙️ Yapılandırma

`config.json` (değiştirmediğiniz ayarlar varsayılanla çalışır):

```json
{
  "interval_seconds": 1,
  "top_n": 6,
  "exclude_processes": ["System Idle Process", "Idle", "MemCompression", "Memory Compression", "Secure System", "Registry", "System"],
  "host": "",
  "log_dir": "logs",
  "log_retention_days": 14,
  "log_level": "INFO",
  "strict_mode": true,
  "limits":    { "max_cpu_percent": 25, "max_memory_mb": 200, "cpu_window_seconds": 3 },
  "precision": { "max_missed_slots": 3, "slot_tolerance_ms": 500 },
  "restart":   { "max_attempts": 5, "delay_seconds": 300, "reset_after_seconds": 3600 },
  "influx": {
    "enabled": true,
    "url": "http://localhost:8086",
    "token": "",
    "org": "secondx",
    "bucket": "secondx",
    "timeout_ms": 5000,
    "max_memory_points": 30000,
    "spool_dir": "spool",
    "spool_block_points": 1500,
    "max_outage_hours": 6,
    "max_spool_mb": 1024,
    "min_free_disk_mb": 1024,
    "backlog_cpu_percent": 10,
    "backlog_max_workers": 4
  },
  "local_output": { "enabled": "auto", "dir": "data", "retention_days": 14 }
}
```

| Ayar | Varsayılan | Açıklama |
| :--- | :---: | :--- |
| `interval_seconds` | `1` | Örnekleme aralığı (0.2 - 3600 sn) |
| `top_n` | `6` | Her metrikte raporlanacak süreç sayısı |
| `exclude_processes` | *(liste)* | Sıralamaya alınmayacak süreç adları (büyük/küçük harf duyarsız) |
| `host` | bilgisayar adı | Sunucu etiketi (panoda görünen ad) |
| `log_dir`, `log_retention_days` | `logs`, `14` | Günlük dosyası (`secondx.log`, gece yarısı döner) ve saklama süresi |
| `log_level` | `INFO` | `DEBUG` her örneği yazar |
| `influx.*` | | InfluxDB v2 bağlantısı. `token` boşsa InfluxDB kullanılmaz |
| `influx.token_file` | `""` | Token'ı bu dosyadan oku (config klasörüne göre). Önerilen yol; Windows kurulumu `secondx.token` kullanır |
| `strict_mode` | `true` | Sorun çıkınca anında dur (bkz. [Sıkı mod](#-sıkı-mod-kritik-sunucular-için)) |
| `limits.max_cpu_percent` | `25` | Ajanın kendi CPU sınırı, **tek çekirdeğin** %'si olarak |
| `limits.max_memory_mb` | `200` | Ajanın kendi RAM (RSS) sınırı, MB |
| `limits.cpu_window_seconds` | `3` | CPU sınırının ortalandığı pencere (kısa anlık sıçramalar durdurmaz) |
| `precision.max_missed_slots` | `3` | Art arda kaçırılabilecek örnek sayısı |
| `precision.slot_tolerance_ms` | `500` | Bir örneğin "kaçırıldı" sayılması için gecikme |
| `restart.max_attempts` / `delay_seconds` / `reset_after_seconds` | `5` / `300` / `3600` | Gözetmenin yeniden başlatma politikası (Windows `--supervise`) |
| `influx.max_memory_points` | `30000` | RAM tamponu. Yarısı dolunca diske yazmaya başlanır, tamamı dolarsa ajan durur |
| `influx.spool_dir` | `spool` | Kesintide blokların yazılacağı klasör (boş = diske yazma yok) |
| `influx.spool_block_points` | `1500` | Bir bloktaki nokta sayısı (1 sn, top 6 ile ≈ 1 dakikalık veri) |
| `influx.max_outage_hours` | `6` | Bu süreden uzun kesintide ajan durur (kod 4) |
| `influx.max_spool_mb` | `1024` | Diskteki bekleyen verinin üst sınırı |
| `influx.min_free_disk_mb` | `1024` | Diskte en az bu kadar boş yer bırakılır |
| `influx.backlog_cpu_percent` | `10` | Birikmiş veri yüklenirken tüm yükleme işçilerinin **toplam** CPU bütçesi (tek çekirdeğin %'si). `max_cpu_percent`'in yarısını geçemez |
| `influx.backlog_max_workers` | `4` | Birikmiş veri için en fazla paralel yükleme işçisi (1-16). Gerektikçe açılır, iş bitince kapanır |
| `local_output.enabled` | `auto` | Token yoksa `data/metrics-GÜN.jsonl` dosyalarına yaz |

**Ortam değişkenleri** config dosyasını geçersiz kılar. Token'ı dosyada tutmamak için önerilen yol budur:

| Değişken | Karşılığı |
| :--- | :--- |
| `SECONDX_INFLUX_TOKEN` | `influx.token` |
| `SECONDX_INFLUX_TOKEN_FILE` | `influx.token_file` |
| `SECONDX_INFLUX_URL` / `_ORG` / `_BUCKET` | `influx.url` / `org` / `bucket` |
| `SECONDX_HOST` | `host` |
| `SECONDX_INTERVAL` | `interval_seconds` |
| `SECONDX_CONFIG` | config dosyasının yolu |

**Komut satırı:**

```text
python SecondX.py              ajanı çalıştır
python SecondX.py --supervise  ajanı yeniden başlatma politikasıyla (gözetmen altında) çalıştır
python SecondX.py --once       tek örnek al, tablo olarak yazdır, çık
python SecondX.py --check      config + InfluxDB bağlantısı ve yazma iznini test et
python SecondX.py --dry-run    ölç ama hiçbir yere yazma
python SecondX.py --verbose    ayrıntılı günlük
python SecondX.py --config /yol/config.json
```

---

## 📊 Grafana panosu

`grafana/dashboards/secondx.json` (Docker kurulumunda otomatik yüklenir; mevcut Grafana'ya **Dashboards → Import** ile eklenebilir).

| Bölüm | İçerik |
| :--- | :--- |
| Üst satır | Anlık CPU, RAM, disk yazma, ağ giriş (eşik renkli) |
| Sistem | CPU & RAM %, disk ve ağ KB/s zaman grafikleri |
| Süreçler | CPU, RAM, disk okuma, disk yazma için en çok tüketenler (süreç adıyla) |
| Tablo | Seçili zaman aralığında süreç başına ortalama tüketim |

Üstteki **Host** seçicisi bir veya birden fazla sunucuyu seçer. **Bucket** kutusu farklı bir bucket adı kullanıyorsanız değiştirilir. Pano 5 saniyede bir yenilenir.

---

## 🧬 Veri şeması

**`secondx_system`**: her örnekte bir nokta

| Etiket | Alanlar |
| :--- | :--- |
| `host` | `cpu_percent`, `ram_percent`, `ram_used_gb`, `process_count`, `disk_read_kbps`, `disk_write_kbps`, `net_sent_kbps`, `net_recv_kbps` |

**`secondx_process`**: her örnekte, her metrik için ilk N süreç

| Etiketler | Alan |
| :--- | :--- |
| `host`, `metric` (`cpu` \| `ram` \| `disk_read` \| `disk_write`), `process`, `rank` (1..N) | `value` (float) |

**Eşit tüketim:** iki süreç tam olarak aynı değeri ölçerse (Windows CPU süresini saat tıkları halinde saydığı için küçük süreçlerde sık görülür, ör. 4 süreç birden `%0.233`):

- aynı `rank` değerini alırlar (1, 2, 2, 4 ...). Biri diğerinin "önünde" gösterilmez;
- N. sıradakiyle eşit olanların **hepsi** listeye girer, hiçbiri rastgele dışarıda kalmaz (en fazla 2N süreç);
- eşitler kendi içinde alfabetik sıralanır. Sonuç, işletim sisteminin süreçleri listeleme sırasına bağlı değildir.

Örnek Flux sorgusu, son 5 dakikada en çok disk yazan süreçler:

```flux
from(bucket: "secondx")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "secondx_process" and r.metric == "disk_write")
  |> group(columns: ["process"])
  |> mean()
  |> group()
  |> sort(columns: ["_value"], desc: true)
```

---

## 🛡️ İşletim ve güvenlik

- **Kaynak kullanımı:** bir örnek Windows'ta ~5-40 ms, Linux'ta birkaç ms CPU harcar. Ajan kendi CPU/RAM'ini sürekli ölçer ve sınırı aşarsa durur. Linux'ta systemd ikinci bir çekirdek sınırı uygular.
- **Kesinti davranışı:** InfluxDB kapalıyken noktalar 1 → 60 sn aralıklarla yeniden denenir. Bekleyen veri diske bloklar halinde yazılır. Bağlantı gelince boşluksuz gönderilir. **Veri hiçbir zaman sessizce atılmaz**; sınır aşılırsa ajan durur ve veri diskte kalır.
- **Durum satırı:** ajan 5 dakikada bir özet yazar: örnek sayısı, kaçırılan örnekler, hatalar, gönderilen / RAM'de bekleyen / diske yazılan nokta.
- **Düzgün kapanma:** SIGTERM / Ctrl+C / servis durdurma → bekleyen noktalar gönderilir (en fazla 5 sn). Gönderilemeyenler diske yazılır, sonra ajan kapanır.
- **Sırlar:** `.env`, `token.json`, `secondx.env`, `*.token`, `logs/`, `data/` ve `spool/` git'e alınmaz (`.gitignore`). Token'ı `influx.token_file` ile yalnızca yöneticilerin açabildiği bir dosyadan verin (kurulum dosyası bunu kendisi yapar).
- **Tek kopya:** aynı ayar dosyasıyla ikinci bir ajan başlatılırsa hemen çıkar (`secondx.lock`); veri iki kez yazılmaz.
- **InfluxDB yetkisi:** üretimde ajan için yalnızca ilgili bucket'a **yazma** yetkisi olan ayrı bir token oluşturun (Influx UI → API Tokens → Custom).

---

## 🛠 Sorun giderme

| Belirti | Çözüm |
| :--- | :--- |
| `--check` → **NOT reachable** | URL doğru mu? Güvenlik duvarı 8086'ya izin veriyor mu? Docker: `docker compose ps` |
| `--check` → **write failed** | Token'ın bucket'a yazma yetkisi, `org` ve `bucket` adları |
| Günlükte **'influxdb-client' package is not installed** | Ajanı çalıştıran Python için: `<python> -m pip install -r requirements.txt`. Bu sırada veriler `data/`'ya yazılır, kaybolmaz |
| Linux'ta başka kullanıcıların süreçleri disk listelerinde görünmüyor | Ajanı `secondx.service` ile çalıştırın (gerekli yetkiler orada tanımlı); elle çalıştırıyorsanız `sudo` kullanın |
| Panoda **No data** | Pano üstündeki Bucket adı doğru mu? Host seçili mi? Zaman aralığı son 15 dk mı? |
| `ERROR: config.json: invalid JSON at line N` | Belirtilen satırda eksik tırnak veya fazla virgül |
| Ajan durdu, **kod 3** (`own CPU/RAM limit exceeded`) | Sunucuda çok sayıda süreç varsa ajan daha fazla kaynak isteyebilir. Günlükteki ölçülen değere bakıp `limits` sınırlarını yükseltin, sonra elle başlatın |
| Ajan durdu, **kod 4** (`unreachable for ...`) | InfluxDB'yi düzeltin, sonra ajanı başlatın: diskteki bloklar otomatik gönderilir |
| Ajan durdu, **kod 4** (`spool size` / `free disk space`) | Diskte yer açın ya da `max_spool_mb` / `min_free_disk_mb` değerlerini gözden geçirin |
| Ajan durdu, **kod 5** (`precision lost`) | Sunucu aşırı yüklü, ajan saniyelik ritmi tutturamıyor. 5 dk sonra yeniden denenir. Sürekliyse `interval_seconds` değerini artırın |
| Windows'ta ajan kendiliğinden yeniden başlamıyor | `logs/secondx-supervisor.log`'a bakın: kod 2/3/4 ya da 5 başarısız denemeden sonra bilerek kapalı kalır |

---

## 🔁 1.x'ten yükseltme

- `config.json` içindeki `wait_time` hâlâ okunur (`interval_seconds` olarak).
- `token.json` hâlâ okunur, ancak değerleri `config.json` → `influx` bölümüne ya da ortam değişkenlerine taşıyın.
- Veri şeması değişti: `Custom_scripts` / `EX134*` alanlarının yerine [`secondx_system` ve `secondx_process`](#-veri-şeması) geldi. Eski panolar yeni ölçümlere göre güncellenmeli. Hazır pano yeni şemayı kullanır.
- `influx_exporter.influx_creator(...)` fonksiyonu eski betikler için korunmuştur.
- 2.0 → 2.1: `influx.max_buffer_points` hâlâ okunur (`max_memory_points` olarak). Sıkı mod varsayılan olarak açıktır; eski davranış için `"strict_mode": false`. Windows'ta görevi `install_windows.ps1` ile yeniden kurun (`--supervise` eklenir). Linux'ta `sudo ./install_linux.sh` yeterlidir.
- 2.1 → 2.2: disk blokları artık `block-*.lp` (InfluxDB satır biçimi). 2.1'den kalan `block-*.jsonl` blokları da okunur ve yüklenir.
- 2.2 → 2.3: Windows'ta en kolay yol `SecondX-Setup.exe`'dir. Betikle kurduysanız `install_windows.ps1`'i yeniden çalıştırın: token, herkesin okuyabildiği `SECONDX_INFLUX_TOKEN` ortam değişkeninden korumalı `secondx.token` dosyasına taşınır.

Ayrıntılar: [CHANGELOG.md](CHANGELOG.md)

---

## 🧪 Geliştirme ve testler

```bash
python -m unittest discover -s tests -v
```

```
persecc/
├── SecondX.py            # Ajan: yapılandırma, ana döngü, komut satırı
├── collector.py          # Ölçüm: sistem ve süreç metrikleri, hız hesapları
├── win_snapshot.py       # Windows hızlı süreç listesi (NtQuerySystemInformation)
├── lissozis.py           # Sıralama: en çok tüketen N süreç
├── influx_exporter.py    # Aktarım: toplu, bloklamayan yazıcı + diske bloklu bekletme + yerel dosya
├── config.json           # Ajan ayarları (sır içermez)
├── requirements.txt
├── packaging/windows/            # SecondX-Setup.exe: kurulum programı + derleme betiği (build.ps1)
├── run_agent.bat                 # Windows: elle çalıştırma
├── install_windows.ps1           # Windows: servis kurulumu
├── uninstall_windows.ps1
├── install_linux.sh              # Linux: servis kurulumu
├── secondx.service               # systemd birimi
├── secondx.env.example           # Linux token dosyası şablonu
├── docker-compose.yml            # InfluxDB + Grafana
├── .env.example                  # Docker şifre/token şablonu
├── grafana/                      # Hazır veri kaynağı ve pano
└── tests/
```

---

## 👨‍💻 Yazar

- **İlhan Koçaslan** — [GitHub: @Proaiml](https://github.com/Proaiml)

Lisans: [MIT](LICENSE)
