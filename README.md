# SecondX: High-Precision Infrastructure Telemetry & Monitoring Agent

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![InfluxDB](https://img.shields.io/badge/InfluxDB-v2.x-00C9FF.svg)](https://www.influxdata.com/)
[![Grafana](https://img.shields.io/badge/Grafana-Ready-F46800.svg)](https://grafana.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**SecondX**, kurumsal sunucularda ve kritik BT altyapılarında (FinTech, Veri Merkezleri, Telekomünikasyon, Bulut Sunucuları) sistem kaynaklarını **saniyelik doğrulukla (Second-Level Precision)** izleyen, en çok kaynak tüketen süreçleri (prosesleri) gerçek zamanlı tespit eden ve telemetri verilerini **InfluxDB** zaman serisi veritabanına aktaran yüksek performanslı bir izleme (monitoring) ajanıdır.

---

## 🎯 Neden Saniyelik Doğruluk (Second-Level Monitoring)?

Geleneksel kurumsal izleme araçları (AWS CloudWatch, Datadog, standart Prometheus scrape döngüleri) sistem metriklerini genellikle **1 dakikalık veya 5 dakikalık** ortalamalarla toplar. 

Ancak kritik altyapılarda:
- **Mikro-Sıçramalar (Micro-Bursts):** Birkaç saniye süren ani CPU kilitlenmeleri ortalama değerlerin içinde kaybolur.
- **I/O Darboğazları:** Anlık yoğun disk okuma/yazma patlamaları standart araçlarla tespit edilemez.
- **Rogue / Bellek Sızdıran Prosesler:** Saniyeler içinde belleği tüketen işlemler zamanında fark edilemez.

**SecondX**, her saniye (`wait_time: 1`) altyapıyı derinlemesine tarayarak darboğazları milisaniyelik gecikmeyle görünür kılar.

---

## 🏗️ Mimari ve İzlenen Metrikler

SecondX, arka planda hafif (lightweight) bir daemon olarak çalışır ve şu metrikleri toplar:

```
                  ┌─────────────────────────────────────┐
                  │       SecondX Monitoring Agent      │
                  └──────────────────┬──────────────────┘
                                     │ (Her 1 saniyede bir)
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
   [ CPU & RAM ]               [ Disk I/O ]                 [ Network I/O ]
- Sistem geneli CPU %        - Proses bazlı Disk Okuma    - Proses bazlı gönderilen
- Toplam RAM % ve GB         - Proses bazlı Disk Yazma      ve alınan ağ trafiği
- En yüksek 6 CPU prosesi    - Diferansiyel KB/s hesabı   - Anlık socket kullanımı
- En yüksek 6 RAM prosesi
        │                            │                            │
        └────────────────────────────┼────────────────────────────┘
                                     ▼
                   ┌───────────────────────────────────┐
                   │    InfluxDB v2.x (Time Series)    │
                   │    Bucket: SeconX                 │
                   └─────────────────┬─────────────────┘
                                     ▼
                   ┌───────────────────────────────────┐
                   │        Grafana Dashboard          │
                   └───────────────────────────────────┘
```

---

## 📁 Proje Dosya Yapısı

```
persecc/
│
├── SecondX.py               # Ana izleme motoru (İlhan Koçaslan'ın orijinal çekirdek kodu)
├── influx_exporter.py       # InfluxDB telemetri aktarım ve bağlantı modülü
├── lissozis.py              # Süreçleri kaynak tüketimine göre sıralayan optimizasyon modülü
├── config.json              # Ajan çalışma yapılandırması (örnekleme aralığı)
├── token.json               # InfluxDB bağlantı adresi ve erişim anahtarı
├── requirements.txt         # Python kütüphaneleri (psutil, influxdb-client)
├── run_agent.bat            # Windows tek tıkla çalıştırma başlatıcısı
├── secondx.service          # Linux sunucular için Systemd servis şablonu
├── docker-compose.yml       # 1 dakikada InfluxDB + Grafana ayağa kaldıran yapılandırma
└── README.md                # Kurumsal entegrasyon kılavuzu
```

---

## 🚀 Kurumsal Entegrasyon Kılavuzu (Adım Adım)

### 1. Adım: Bağımlılıkların Kurulması
Sunucunuzda (Windows veya Linux):
```bash
pip install -r requirements.txt
```

---

### 2. Adım: Yapılandırma (`config.json` & `token.json`)

#### A. Ajan Ayarı (`config.json`):
Örnekleme aralığını saniye cinsinden belirleyin (Varsayılan: 1 saniye):
```json
{
  "wait_time": 1,
  "description": "Sampling interval in seconds for second-level accuracy monitoring",
  "log_retention_days": 14
}
```

#### B. InfluxDB Bağlantısı (`token.json`):
InfluxDB sunucunuzun erişim bilgilerini girin:
```json
{
  "url": "http://localhost:8086",
  "token": "YOUR_INFLUXDB_API_TOKEN",
  "org": "H",
  "bucket": "SeconX"
}
```
> **Not:** InfluxDB sunucusu hazır değilse ajan çökmez; otomatik olarak yerel loglama modunda çalışmaya devam eder.

---

### 3. Adım: Ajanı Çalıştırma

#### Seçenek A (Windows Üzerinde):
Doğrudan **`run_agent.bat`** dosyasına çift tıklayın veya:
```bash
python SecondX.py
```

#### Seçenek B (Linux Sunucularda Systemd Servisi Olarak - 7/24 Daemon):
Kurumsal Linux sunucularda (Ubuntu, Debian, RHEL, CentOS) ajanın sunucu açılışında otomatik başlaması için:

```bash
# 1. Projeyi /opt/secondx dizinine kopyalayın
sudo cp -r . /opt/secondx

# 2. Servis dosyasını sisteme tanıtın
sudo cp secondx.service /etc/systemd/system/

# 3. Servisi aktifleştirin ve başlatın
sudo systemctl daemon-reload
sudo systemctl enable secondx
sudo systemctl start secondx

# 4. Canlı logları izleyin
sudo journalctl -u secondx -f
```

---

## 🐳 Hızlı Telemetri Yığını (Docker Compose ile InfluxDB + Grafana)

Eğer halihazırda bir InfluxDB veya Grafana sunucunuz yoksa, tek bir komutla tüm telemetri altyapısını başlatabilirsiniz:

```bash
docker compose up -d
```
Bu komut:
- **InfluxDB v2:** `http://localhost:8086` (Bucket: `SeconX`, Org: `H`, Token: `my-super-secret-auth-token-123`)
- **Grafana:** `http://localhost:3000` (Kullanıcı: `admin`, Şifre: `admin`)
hizmetlerini anında çalışır hale getirir.

---

## 📊 InfluxDB Veri Şeması (Data Schema)

Ajan tarafından gönderilen telemetri noktaları:

| Ölçüm (Measurement) | Alan (Field Key) | Etiket (Tag0) | Etiket (Tag1) | Veri Tipi | Açıklama |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `Custom_scripts` | `EX134cpu` | `cpu_usage` | `36` | Integer | Toplam sistem CPU kullanım yüzdesi (%) |
| `Custom_scripts` | `EX134pram` | `EX58ram` | `35` | Integer | Toplam sistem RAM kullanım yüzdesi (%) |
| `Custom_scripts` | `EX134proc` | *Proses Adı* | `34` | Integer | En çok CPU tüketen ilk 6 proses |
| `Custom_scripts` | `EX134procram` | *Proses Adı* | `34` | Integer | En çok RAM tüketen ilk 6 proses |
| `Custom_scripts` | `EX134diskr` | *Proses Adı* | `34` | Integer | Proses bazlı diferansiyel Disk Okuma hızı (KB/s) |
| `Custom_scripts` | `EX134diskw` | *Proses Adı* | `34` | Integer | Proses bazlı diferansiyel Disk Yazma hızı (KB/s) |

---

## 🧹 Otomatik Günlük Log Yönetimi (`logcontr`)

SecondX, disk dolmasını engellemek amacıyla dahili bir otomatik temizleme mekanizmasına sahiptir:
- Günlük olayları ve sistem durumunu `YYYY-MM-DD.json` dosyalarına yazar.
- Her döngüde `logcontr()` fonksiyonu çalışarak **14 günden eski** geçmiş log dosyalarını diskten otomatik olarak siler.

---

## 👨‍💻 Yazar

- **İlhan Koçaslan** — [GitHub: @Proaiml](https://github.com/Proaiml)
- **BNB Smart Chain (BEP20) Cüzdan:** `0x89943b0a0f43fc6cd3ce9a8c19718485dcaf0bb7`
