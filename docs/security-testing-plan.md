# Sigurnosni plan testiranja — PratimZakon

**Stack:** FastAPI + PostgreSQL + Stripe + statični frontend (vanilla JS)
**Datum:** 2026-03-25
**Prioritet:** Visoki rizici za SaaS s više korisnika i plaćenim planovima

---

## Pregled potvrđenih nalaza iz koda

| # | Nalaz | Datoteka | Rizik |
|---|-------|----------|-------|
| 1 | Rate limiting inicijaliziran ali **nije primijenjen** ni na jedan endpoint | `main.py:36`, `routers/auth.py:253` | Kritičan |
| 2 | JWT token pohranjen u `localStorage` (ne httpOnly cookie) | `dashboard.html` | Visok |
| 3 | Login vraća različit error za nepostojećeg i neverificiranog korisnika | `routers/auth.py:258-259` | Srednji |
| 4 | CORS `allow_methods=["*"]`, `allow_headers=["*"]` | `main.py:57-58` | Srednji |
| 5 | Nema security headera (CSP, HSTS, X-Frame-Options) | `main.py` | Srednji |
| 6 | `plan_type` vs `plan` polja mogu divergirati | `routers/stats.py:43`, `routers/auth.py:277` | Srednji |
| 7 | `/auth/unsubscribe` mijenja `subscription_status` bez autentikacije | `routers/auth.py:472-481` | Nizak* |

> *Token je `secrets.token_urlsafe(32)` = 256 bita entropije — brute force nije praktičan, ali token se šalje emailom

---

## KORAK 1 — Brute force na `/auth/login`

**Cilj:** Potvrditi da nema rate limitinga. Teoretski napadač može pogađati lozinke.

**Zašto je to ranjivost:**
`main.py:36` inicijalizira `slowapi` limiter, ali `routers/auth.py` login endpoint nema `@limiter.limit(...)` dekorator.

### Test 1.1 — Potvrditi odsutnost rate limitinga

```bash
# Pošalji 20 login requestova i provjeri jesu li svi prošli bez 429
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<API_URL>/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"wrong'$i'"}'
done
```

**Očekivano (ranjivo):** svi odgovori su `401`, niti jedan nije `429`
**Očekivano (sigurno):** nakon ~5 pokušaja dobivamo `429 Too Many Requests`

### Test 1.2 — Email enumeracija

```bash
# Postojeći korisnik koji NIJE verificiran — vraća 403 "Potvrdite email adresu"
curl -X POST https://<API_URL>/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"postoji@ali.neverificiran.hr","password":"krivi"}'
# Response: 403

# Nepostojeći korisnik — vraća 401 "Pogrešan email ili lozinka"
curl -X POST https://<API_URL>/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"nepostoji@example.com","password":"krivi"}'
# Response: 401
```

**Nalaz:** Različiti HTTP status (403 vs 401) otkriva je li email registriran
**Lokacija u kodu:** `routers/auth.py:257-259`

---

## KORAK 2 — Plan bypass (Stats endpoint)

**Cilj:** Može li `free` korisnik pristupiti `/stats/`?

**Zašto je relevantno:**
`stats.py:43` provjerava `plan_type` polje, a `routers/auth.py:277` provjerava `plan` polje za MU. Ova dva polja se ažuriraju zajedno u `stripe_router.py:65-66`, ali postoji mogućnost ruba gdje su nesinkronizirani.

### Test 2.1 — Direktan poziv kao free korisnik

```bash
TOKEN=$(curl -s -X POST https://<API_URL>/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"free@test.hr","password":"lozinka"}' | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" https://<API_URL>/stats/
# Očekivano: 403 Forbidden
```

### Test 2.2 — Plan bypass manipulacijom `keyword_limit` u POST body

```bash
# Pokušaj dodati >3 ključne riječi kao free korisnik manipulacijom requesta
# Backend provjerava: len(current_user.keywords) >= current_user.keyword_limit
# keyword_limit je DB polje, ne dolazi iz requesta — ovo je zaštićeno
# Ali svejedno potvrdi:
for kw in "test1" "test2" "test3" "test4"; do
  curl -s -X POST https://<API_URL>/keywords/ \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"keyword\":\"$kw\"}"
done
# 4. keyword treba vratiti 403
```

### Test 2.3 — include_mu bypass

```bash
# Free korisnik pokušava uključiti MU (Narodne novine — Međunarodni ugovori)
# routers/auth.py:277 provjerava plan == "free"
curl -X PATCH https://<API_URL>/auth/settings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"include_mu": true}'
# Očekivano: 403 "MU dostupan uz Pro ili Expert paket"
```

---

## KORAK 3 — IDOR (Insecure Direct Object Reference)

**Cilj:** Može li korisnik A izbrisati ključnu riječ korisnika B?

**Analiza koda (routers/keywords.py:113-115):**
```python
kw = db.query(Keyword).filter(
    Keyword.id == keyword_id,
    Keyword.user_id == current_user.id,  # ← ovo je zaštita
).first()
```

Zaštita **postoji** — query filtrira i po `keyword_id` I po `user_id`. Ako keyword ne pripada korisniku, vraća `404`.

### Test 3.1 — Potvrditi IDOR zaštitu

```bash
# Korisnik A dohvati svoje keyword ID-eve
TOKEN_A=<token korisnika A>
KEYWORDS_A=$(curl -s -H "Authorization: Bearer $TOKEN_A" https://<API_URL>/keywords/)
echo $KEYWORDS_A  # npr. ID=5

# Korisnik B pokušava izbrisati keyword koji pripada korisniku A
TOKEN_B=<token korisnika B>
curl -s -X DELETE https://<API_URL>/keywords/5 \
  -H "Authorization: Bearer $TOKEN_B"
# Očekivano (sigurno): 404 "Ključna riječ nije pronađena"
# Ranjivo bi bilo: 204 No Content (uspješno brisanje)
```

**Predikcija:** zaštićeno, ali svejedno verificirati u produkciji.

---

## KORAK 4 — Stripe webhook bez potpisa

**Cilj:** Što se dogodi ako netko pošalje lažni webhook?

**Analiza koda (routers/stripe_router.py:51-53):**
```python
event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
# Baca SignatureVerificationError ako potpis nije valjan
```

Provjera **postoji** i ispravno je implementirana. Svejedno testiraj:

### Test 4.1 — Webhook bez stripe-signature headera

```bash
curl -s -X POST https://<API_URL>/stripe/webhook \
  -H "Content-Type: application/json" \
  -d '{"type":"checkout.session.completed","data":{"object":{"metadata":{"user_id":"1","plan":"plus"}}}}'
# Očekivano: 400 "Neispravan webhook potpis"
```

### Test 4.2 — Webhook s lažnim potpisom

```bash
curl -s -X POST https://<API_URL>/stripe/webhook \
  -H "Content-Type: application/json" \
  -H "stripe-signature: t=1234567890,v1=abc123fake" \
  -d '{"type":"checkout.session.completed","data":{"object":{"metadata":{"user_id":"1","plan":"plus"}}}}'
# Očekivano: 400 "Neispravan webhook potpis"
```

**Napomena o metadata user_id:** `user_id` u metadata je postavljen **server-side** iz JWT tokena (`stripe_router.py:40`), nije klijentski kontroliran. Napadač ne može manipulirati ovim poljem bez pristupa Stripe dashboardu.

---

## KORAK 5 — Security headeri i CORS

**Cilj:** Auditi HTTP response headera i CORS konfiguracije.

### Test 5.1 — Provjera security headera

```bash
curl -sI https://<API_URL>/health
# Tražiti odsutnost ovih headera (ranjivost):
# - Content-Security-Policy
# - X-Frame-Options
# - Strict-Transport-Security
# - X-Content-Type-Options
```

**Predikcija:** svi navedeni headeri su odsutni jer FastAPI ih ne dodaje automatski.

### Test 5.2 — CORS s proizvoljne domene

```bash
# Provjeri prihvaća li backend requeste s neovlaštene domene
curl -s -I -X OPTIONS https://<API_URL>/auth/me \
  -H "Origin: https://evil.attacker.com" \
  -H "Access-Control-Request-Method: GET"
# Ako odgovor sadrži:
# Access-Control-Allow-Origin: https://evil.attacker.com
# → ranjivo (ali za autenticirane endpointe to nije trivijalno eksploitabilno)
```

**Napomena:** `main.py:56` eksplicitno definira `ALLOWED_ORIGINS` listu, pa bi proizvoljne domene trebale biti blokirane. `allow_methods=["*"]` i `allow_headers=["*"]` su preširoki ali ne kritični uz ispravno whitelist origin.

### Test 5.3 — Provjera FastAPI OpenAPI dokumentacije

```bash
# FastAPI po defaultu eksponira /docs i /redoc
curl -s https://<API_URL>/docs
curl -s https://<API_URL>/redoc
curl -s https://<API_URL>/openapi.json
# Trebalo bi biti onemogućeno u produkciji
```

---

## KORAK 6 — JWT analiza

**Cilj:** Procijeniti sigurnost JWT implementacije.

**Podaci iz koda:**
- Algoritam: HS256 (`auth.py`)
- Expiry: 10.080 minuta = **7 dana**
- Storage: `localStorage` (nije httpOnly cookie)
- Nema token refresh mehanizma

### Test 6.1 — Token expiry i replay

```bash
# Dohvati token, provjeri payload
TOKEN=<tvoj JWT token>
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
# Provjeri: exp claim = now + 7 dana?
# Provjeri: sadrži li token samo user id (sub) bez dodatnih claim-ova?
```

### Test 6.2 — Token invalidacija pri odjavi

```bash
# Prijavi se i dohvati token
TOKEN=$(curl -s -X POST https://<API_URL>/auth/login \
  -d '{"email":"test@test.hr","password":"lozinka"}' | jq -r .access_token)

# Simuliraj odjavu (frontend samo briše localStorage)
# Provjeri: je li token i dalje valjan na backendu?
curl -H "Authorization: Bearer $TOKEN" https://<API_URL>/auth/me
# Ako vraća 200 → backend ne invalidira tokene pri "odjavi"
# Ovo je očekivano za stateless JWT, ali je rizik ako token procuri
```

---

## KORAK 7 — Input validacija

### Test 7.1 — SQL injection u search parametru

```bash
TOKEN=<tvoj token>

# search endpoint koristi SQLAlchemy .ilike() — parameterized query
# Vjerojatno nije ranjivo, ali potvrdi
curl -s "https://<API_URL>/search/?q=%27%20OR%20%271%27%3D%271" \
  -H "Authorization: Bearer $TOKEN"
# q = ' OR '1'='1
# Očekivano: normalan odgovor (prazni rezultati ili uredni podaci), ne 500
```

### Test 7.2 — XSS u keyword polju

```bash
# Dodaj keyword s XSS payloadom
curl -X POST https://<API_URL>/keywords/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"keyword":"<script>alert(1)</script>"}'

# Zatim otvori dashboard.html i provjeri:
# - Je li <script> tag prikazan kao tekst (escaped) ili je izvršen?
# Frontend koristi vanilla JS — provjeriti kako se keyword renderira u DOM
```

---

## KORAK 8 — Izloženi tajni podaci

### Test 8.1 — Env varijable u GitHub repozitoriju

```bash
# Provjeri je li .env committan u repozitorij
git log --all --full-history -- .env
git show HEAD:.env 2>/dev/null || echo "Nije u HEAD"
# Provjeri .gitignore
cat .gitignore | grep .env
```

**Nalaz:** `.env` datoteka postoji lokalno s production credentialima. Provjeri je li ikad bila commitana.

### Test 8.2 — Tajni podaci u response headerima

```bash
curl -sI https://<API_URL>/health
# Tražiti: Server header (otkriva verziju uvicorna/Pythona)
# Tražiti: X-Powered-By ili slično
```

### Test 8.3 — Admin endpointi bez dodatne zaštite

```bash
# Admin provjera je jednostavna email usporedba (routers/auth.py:22)
# Provjeri jesu li admin rute dostupne neautoriziranim korisnicima
curl -s https://<API_URL>/admin/stats
curl -s https://<API_URL>/admin/users
# Bez tokena → treba vratiti 401/403
```

---

## Redoslijed implementacije popravaka (prioritet)

### P0 — Hitno (odmah)

1. **Dodati rate limiting na `/auth/login`**
   ```python
   # routers/auth.py
   from ..main import limiter
   from fastapi import Request

   @router.post("/login", response_model=Token)
   @limiter.limit("5/minute")
   def login(request: Request, data: UserLogin, db: Session = Depends(get_db)):
       ...
   ```

2. **Dodati security headere** (npr. `slowapi-security` ili custom middleware)
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Strict-Transport-Security: max-age=31536000`

### P1 — Visoko (ovaj sprint)

3. **Uskladiti email error poruke na loginu** — obje situacije (nepostojeci korisnik + neverificiran) vraćati isti `401` s istom porukom

4. **Suziti CORS konfiguraciju**
   ```python
   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
   allow_headers=["Authorization", "Content-Type"]
   ```

5. **Onemogućiti `/docs` i `/openapi.json` u produkciji**
   ```python
   app = FastAPI(docs_url=None, redoc_url=None) if os.getenv("ENV") == "production" else FastAPI()
   ```

### P2 — Srednje (sljedeći sprint)

6. **Migrirati JWT token iz localStorage u httpOnly cookie** — eliminira XSS vektor

7. **Dodati token blacklist pri brisanju računa** — trenutno obrisani korisnik može koristiti stari token do isteka (7 dana)

8. **Rate limiting na `/auth/register`** i `/auth/resend-verification`

---

## Alati za testiranje

```bash
# Instalacija
pip install httpie  # user-friendly HTTP klijent
# ili koristiti curl s gornjim primjerima

# Za automatiziranu provjeru headera
pip install observatory-cli
# ili online: https://observatory.mozilla.org/

# Za JWT dekodiranje
pip install pyjwt
python3 -c "import jwt; print(jwt.decode('<token>', options={'verify_signature': False}))"
```

---

## Sažetak rizika

| Rizik | Status | Prioritet popravka |
|-------|--------|--------------------|
| Brute force na login (bez rate limitinga) | **Ranjivo** | P0 — odmah |
| Email enumeracija kroz login | **Ranjivo** | P1 |
| Nedostaju security headeri | **Ranjivo** | P0/P1 |
| CORS preširok (allow_methods/headers *) | Djelomično ranjivo | P1 |
| IDOR na keywords | **Zaštićeno** ✓ | — |
| Stripe webhook bez potpisa | **Zaštićeno** ✓ | — |
| Stripe metadata manipulation | **Zaštićeno** ✓ | — |
| SQL injection (SQLAlchemy ORM) | **Vjerojatno zaštićeno** | Verificirati |
| JWT u localStorage | Prihvatljiv rizik (nema XSS vektora) | P2 |
| Plan bypass na stats | **Zaštićeno** ✓ | Verificirati edge case |
