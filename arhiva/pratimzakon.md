# 📘 PRATIMZAKON – TEHNIČKI I PRODUKTNI DRAFT (MVP → PRODUCTION)

---

# 1. 🎯 OPIS PROJEKTA

**PratimZakon** je SaaS aplikacija koja:

* prati objave u Narodnim novinama
* detektira relevantne dokumente prema korisničkim ključnim riječima
* šalje email obavijesti korisnicima

Cilj:
👉 smanjiti rizik propuštanja zakonskih promjena
👉 povećati fokus korisnika filtriranjem informacija

---

# 2. 🧱 ARHITEKTURA SUSTAVA

## Frontend

* GitHub Pages (HTML + minimal JS)

## Backend

* Python (Flask ili FastAPI)
* Hosting: Render / Railway / Fly.io

## Baza

* PostgreSQL

## Scheduler

* GitHub Actions (07:00 scraper)
* dodatni job (01:00 subscription check)

## Email

* SendGrid / Mailgun / SMTP

## Plaćanje

* Stripe (Checkout + Webhooks)

---

# 3. ⚙️ FUNKCIONALNOSTI

## 3.1 Scraper

Radi svakodnevno:

* provjerava novi broj Narodnih novina
* ako postoji:

  * dohvaća dokumente
  * sprema u bazu
  * briše stare zapise

---

## 3.2 Keyword matching

* korisnik definira ključne riječi
* sustav traži substring match

### MVP logika:

```python
if keyword.lower() in title.lower():
```

---

## 3.3 Email notifikacije

Šalje se kada:

* postoji novi broj
* postoji match s keywordom

---

# 4. 💳 MONETIZACIJA

| Paket | Riječi | Cijena |
| ----- | ------ | ------ |
| Free  | 3      | 0.00 € |
| Basic | 10     | 4.99 € |
| Plus  | 20     | 7.99 € |

---

## Pravila:

* korisnik može mijenjati riječi
* limit definiran paketom
* nema triala (MVP verzija)

---

# 5. 👤 USER MANAGEMENT

## Tablica: users

* id
* email
* password_hash
* subscription_status (free / active / expired)
* subscription_end
* keyword_limit
* unsubscribe_token
* created_at

---

## Tablica: keywords

* id
* user_id
* keyword

---

## Tablica: documents

* id
* title
* url
* type
* published_date
* issue_number

---

# 6. 🔄 UPRAVLJANJE RIJEČIMA

## Pravila:

* korisnik može:

  * dodati
  * obrisati
* nema edit funkcije

## Validacija:

```python
if len(user.keywords) >= user.keyword_limit:
    raise Exception("Limit reached")
```

---

# 7. 📧 EMAIL SPECIFIKACIJA

Email mora sadržavati:

## Obavezno:

* naslov dokumenta
* link
* detektiranu riječ

## Status:

* pretplata (free / aktivna do datuma)

## Unsubscribe:

* link s tokenom

---

## Unsubscribe endpoint:

```
/unsubscribe?token=XYZ
```

Akcija:

* user.subscription_status = inactive

---

# 8. 🔄 PRETPLATE (LIFECYCLE)

## Statusi:

* free
* active
* expired

---

## Istek:

```python
if today > subscription_end:
    user.subscription_status = "expired"
    user.keyword_limit = 3
```

---

## Pravilo:

❌ NE briši korisnike
✅ downgrade na free

---

# 9. 💳 STRIPE INTEGRACIJA

## Flow:

1. user → Stripe Checkout
2. Stripe → webhook
3. backend aktivira pretplatu

---

# 10. 🔐 KRITIČNA SIGURNOST (P0)

## 🚨 STRIPE WEBHOOK VALIDACIJA

OBAVEZNO prije launch-a.

---

## Implementacija:

```python
import stripe

event = stripe.Webhook.construct_event(
    payload,
    sig_header,
    endpoint_secret
)
```

---

## ERROR handling:

```python
except stripe.error.SignatureVerificationError:
    abort(400)
```

---

## Pravila:

* nikad ne vjeruj requestu bez validacije
* nikad ne aktiviraj pretplatu bez webhooka

---

# 11. 🔐 SIGURNOST (SAŽETAK)

## Obavezno:

* bcrypt hash lozinki
* HTTPS
* ORM (bez raw SQL)
* rate limiting
* input sanitizacija
* .env za ključeve
* email verifikacija

---

# 12. 🧠 ANTI-ABUSE

* limit keyworda
* limit signup pokušaja
* email verification

---

# 13. ⚙️ SCHEDULERI

## 07:00

* scraper

## 01:00

* subscription check

## + admin alert

---

# 14. 🧑‍💼 ADMIN FUNKCIONALNOSTI

## Dashboard:

* broj korisnika
* free vs paid
* aktivni vs expired

---

## Notifikacije:

5 dana prije isteka:

```python
if subscription_end - today == 5:
    notify_admin()
```

---

# 15. 📊 LOGIRANJE

Tablica: logs

* event_type
* user_id
* timestamp

---

# 16. 🚀 MVP ROADMAP

## FAZA 1

* scraper
* baza

## FAZA 2

* user + keywords

## FAZA 3

* email sustav

## FAZA 4

* Stripe

---

# 17. ⚠️ RIZICI

## Tehnički:

* promjena strukture NN
* scraper failure

## Poslovni:

* akvizicija korisnika

## Sigurnosni:

* webhook abuse
* email spam

---

# 18. 📈 CILJ

* 500 korisnika u 12 mjeseci
* ~1000–2000 posjetitelja mjesečno

---

# 19. 🧠 KLJUČNE ODLUKE

✔ jednostavan MVP
✔ backend odvojen od GitHub Pages
✔ Stripe Checkout
✔ downgrade model (ne brisanje)
✔ sigurnost kao prioritet

---

# 20. 🔚 ZAKLJUČAK

Ovaj projekt je:

* tehnički izvediv
* poslovno validan
* skalabilan

Najveći prioriteti:

1. scraper pouzdanost
2. email točnost
3. webhook sigurnost

---

# NEXT STEP

👉 Backend skeleton + scraper implementacija

