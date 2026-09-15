# ecommerce-event-pipeline

Bir e-ticaret sitesinin kullanıcı davranış event'lerini (ürün görüntüleme, sepete ekleme, checkout, satın alma) sentetik olarak üreten ve bunları uçtan uca işleyen bir veri pipeline'ı. Amaç, gerçekçi ama kontrol edilebilir bir veri kaynağı üzerinde event işleme, doğrulama ve analitik adımlarını adım adım kurmak.

## Yol haritası

- [x] **Adım 1 — Repo ve referans veri:** kullanıcı ve ürün tabloları
- [ ] **Adım 2 — Event üretimi:** referans veriyi okuyup funnel event'leri üretme
- [ ] _Sonraki adımlar — TBD_

## Kurulum

Gereksinim: Python 3.9+

```bash
make venv        # .venv oluşturur, bağımlılıkları kurar
make reference   # data/users.jsonl ve data/products.jsonl üretir
```

Diğer komutlar:

| Komut                  | Ne yapar                                   |
| ---------------------- | ------------------------------------------ |
| `make reference`       | Referans veriyi üretir; dosya varsa atlar  |
| `make reference-force` | Var olan dosyaların üzerine yeniden üretir |
| `make clean-data`      | `data/*.jsonl` dosyalarını siler           |

Script'ler doğrudan da çalışır:

```bash
.venv/bin/python -m reference.generate_users [--config config.yaml] [--force]
.venv/bin/python -m reference.generate_products [--config config.yaml] [--force]
```

## Konfigürasyon

Tüm parametreler [`config.yaml`](config.yaml) içinde: `seed`, kullanıcı/ürün sayıları, ülke–şehir ve cihaz dağılımları, kategori ağırlıkları ve fiyat aralıkları, funnel dönüşüm oranları.

## Referans veri şemaları

### `data/users.jsonl` — 10.000 satır

| Alan          | Tip    | Örnek                    | Not                                 |
| ------------- | ------ | ------------------------ | ----------------------------------- |
| `user_id`     | string | `"U000001"`              | Sıralı, benzersiz                   |
| `country`     | string | `"TR"`                   | ISO 3166-1 alpha-2, ağırlıklı       |
| `city`        | string | `"Istanbul"`             | Her zaman `country`'ye ait bir şehir |
| `device_type` | string | `"mobile"`               | `mobile` / `desktop` / `tablet`     |
| `created_at`  | string | `"2024-03-12T14:22:05Z"` | ISO 8601 UTC                        |

### `data/products.jsonl` — 1.000 satır

| Alan         | Tip    | Örnek           | Not                                   |
| ------------ | ------ | --------------- | ------------------------------------- |
| `product_id` | string | `"P00001"`      | Sıralı, benzersiz                     |
| `category`   | string | `"electronics"` | Ağırlıklı                             |
| `price`      | float  | `149.99`        | Kategori aralığında log-uniform, 2 ondalık |

## Tasarım kararları

- **Referans veri bir kez üretilir.** Gerçek sistemde kullanıcılar ve ürünler event'lerden önce vardır. Event üreticisi bu dosyaları yalnızca okur; script'ler var olan dosyanın üzerine `--force` olmadan yazmaz.
- **Küçük ölçek (10k kullanıcı / 1k ürün).** Pipeline mantığını öğrenmek için yeterli; daha büyük dosyalar yalnızca iterasyonu yavaşlatır.
- **Deterministik üretim.** Aynı `seed` → bit bit aynı dosyalar. Kullanıcı ve ürün üreticileri ayrı türetilmiş seed'ler kullanır, biri değişince diğeri etkilenmez. Bu yüzden `data/` git'e girmez.
- **Atomik yazım.** Dosya önce `.tmp` olarak yazılır, sonra yerine taşınır; yarıda kalan bir çalıştırma bozuk dosya bırakmaz.
