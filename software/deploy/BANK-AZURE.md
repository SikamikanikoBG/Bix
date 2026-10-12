# Bix в банка с Azure и Microsoft 365

Как се пуска Bix на сървър в инфраструктурата на банката, с услугите на Azure, които банката вече ползва. Целта: **един сървър**, всяка зала е лаптоп с браузър (без инсталация) или устройство Bix.

```
 зала: лаптоп с Chrome/Edge                  сървър на банката (Docker)                Azure (ЕС)
 https://bix.bank.local/room   ── HTTPS ──►  Caddy (TLS) → Bix → PostgreSQL     ──►  Speech (реч → текст)
 портал: https://bix.bank.local              записи: том на Docker / Azure Blob ──►  Azure OpenAI (минути, въпроси)
                                             имейл: SMTP relay / Microsoft 365  ──►  Entra ID (вход, SSO)
```

Данните остават в банката и в Azure абонамента на банката. Bix не праща нищо на други места.

## 1. Какво трябва да подготви ИТ

| Какво | Подробности |
|---|---|
| Сървър | Linux (Ubuntu 22.04/24.04 или RHEL 9) с Docker и Docker Compose. 4 vCPU, 8 GB RAM, 100 GB диск стигат за пилот: тежката работа (реч, езиков модел) е в Azure. Разпознаването на говорещите е локално (около 1 мин за час среща). |
| Име и сертификат | DNS име, напр. `bix.bank.local`, и TLS сертификат от вътрешното PKI на банката (`bix.crt` с веригата и `bix.key`). **HTTPS е задължителен**: браузърите дават микрофона само на защитен адрес. |
| Мрежа навън | Към Azure: `login.microsoftonline.com`, ресурсите на Speech и Azure OpenAI (`*.cognitiveservices.azure.com`, `*.openai.azure.com`), при Microsoft 365 и `graph.microsoft.com`. През проксито на банката или чрез private endpoints. |
| Мрежа навътре | Лаптопите в залите да стигат до сървъра на 443. |
| Сертификат на проксито | Ако проксито инспектира TLS: вътрешният CA (PEM) в `deploy/docker/ca-certificates/*.crt` преди build-а. |
| PyPI | Ако сървърът няма интернет към PyPI: огледало (Artifactory/Nexus), подава се с `PIP_INDEX_URL` при build-а. |

## 2. Ресурси в Azure

1. **Azure AI Speech** в регион на ЕС (West Europe, Germany West Central, Sweden Central). Задайте **custom domain** на ресурса (нужен е за вход с Entra ID и за private endpoint). Bix ползва fast transcription API (български, bg-BG).
2. **Azure OpenAI** (Azure AI Foundry): deployment на модел със structured outputs, напр. `gpt-4.1` или `gpt-4o`, тип *Standard* в регион на ЕС или *Data Zone EU*. Около 30–60 хил. токена на час среща.
3. **Microsoft Entra ID app registration** за сървъра (или managed identity, ако сървърът е в Azure), с роли:
   - **Cognitive Services User** върху ресурса Speech;
   - **Cognitive Services OpenAI User** върху ресурса Azure OpenAI;
   - **Storage Blob Data Contributor** върху контейнера, ако записите се пазят в Azure Blob (по желание).
   - Сертификат вместо тайна е за предпочитане: PEM файл с частния ключ и сертификата.
   - С ключове на ресурсите вместо Entra ID също работи (по-просто за пилот).
4. **Вход в портала със служебния акаунт (SSO)**: второ app registration (Web) с redirect URI `https://bix.bank.local/auth/oidc/callback`.
5. **Microsoft 365** (по желание: календарите на залите и изпращане от пощенска кутия): application permissions `Calendars.Read` и `Mail.Send`, ограничени с *Application Access Policy* или *RBAC for Applications* само до кутията на Bix (напр. споделена `bix@bank.bg`) и кутиите на залите.

## 3. Имейл: SMTP relay или Microsoft 365

Минутите тръгват сами след всяка среща към организатора, участниците от календара, разпознатите по глас хора и получателите на залата.

**SMTP relay на банката** (най-често):
```
BIX_SMTP_HOST=smtp-relay.bank.local
BIX_SMTP_PORT=25
BIX_SMTP_STARTTLS=true            # false само ако relay-ят не предлага STARTTLS
BIX_SMTP_SENDER=bix@bank.bg       # подателят трябва да е разрешен за IP-то на сървъра
BIX_SMTP_REPLY_TO=it@bank.bg
# BIX_SMTP_USERNAME= / BIX_SMTP_PASSWORD=   само ако relay-ят иска вход
```
Писмата са с `Date`, `Message-ID`, `Auto-Submitted: auto-generated` и `X-Auto-Response-Suppress: All`: шлюзовете не ги режат и няма автоматични отговори „извън офиса“. Вътрешният CA на relay-я се добавя както в т. 1.

**Microsoft 365** (дава и календарите на залите):
```
BIX_GRAPH_ENABLED=true
BIX_GRAPH_SENDER_MAILBOX=bix@bank.bg
# приложението е същото като за Azure (BIX_AZURE_*), или отделно: BIX_GRAPH_TENANT_ID/CLIENT_ID/...
```

Пробен имейл след пускане: `docker compose exec bix bix server test-email --to вие@bank.bg`.

## 4. Инсталиране

```bash
git clone https://github.com/SikamikanikoBG/Bix && cd Bix/software/deploy/docker
cp .env.example .env              # попълнете: BIX_DOMAIN, POSTGRES_PASSWORD, Azure, имейл
cp /път/до/bix.crt /път/до/bix.key certs/
cp /път/до/bank-root-ca.crt ca-certificates/      # ако има TLS проксито / вътрешно PKI

# build (през проксито и огледалото на PyPI, ако има)
docker compose -f docker-compose.yml -f docker-compose.internal.yml build \
  --build-arg HTTPS_PROXY=$HTTPS_PROXY --build-arg PIP_INDEX_URL=https://artifactory.bank.local/api/pypi/pypi/simple

docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d
docker compose exec bix bix server admin --email admin@bank.bg     # първият администратор (паролата се показва веднъж)
docker compose exec bix bix server check                           # реч, езиков модел, имейл, хранилище, Microsoft 365
```

`docker-compose.internal.yml` пуска Caddy с вашия сертификат вместо Let's Encrypt. Данните са в томовете `bix-data` и `db-data`.

Минимален `.env` за пилот с ключове (без Entra ID):
```
BIX_DOMAIN=bix.bank.local
POSTGRES_PASSWORD=<дълга случайна>
BIX_AUTH_SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(48))">
HTTPS_PROXY=http://proxy.bank.local:8080
NO_PROXY=localhost,127.0.0.1,db,bix,.bank.local
BIX_STT_PROVIDER=azure
BIX_STT_AZURE_ENDPOINT=https://bank-speech.cognitiveservices.azure.com
AZURE_SPEECH_KEY=<ключ>
BIX_LLM_PROVIDER=azure_openai
BIX_LLM_AZURE_ENDPOINT=https://bank-openai.openai.azure.com
BIX_LLM_AZURE_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_API_KEY=<ключ>
BIX_SMTP_HOST=smtp-relay.bank.local
BIX_SMTP_PORT=25
BIX_SMTP_SENDER=bix@bank.bg
```
С Entra ID вместо ключове: `BIX_AZURE_TENANT_ID`, `BIX_AZURE_CLIENT_ID`, `BIX_AZURE_CERTIFICATE_PATH=/data/bix-app.pem` (или `BIX_AZURE_CLIENT_SECRET`), плюс `BIX_STT_AZURE_AUTH=entra` и `BIX_LLM_AZURE_AUTH=entra`. На Azure VM: `BIX_AZURE_MANAGED_IDENTITY=true`, без тайни.

## 5. Първа среща

1. Портал `https://bix.bank.local` → **Зали → Нова зала** (име, получатели на минутите).
2. **Зали → Календар → Нова резервация**, или в редакцията на залата: таен iCal адрес / кутията на залата в Microsoft 365.
3. **Устройства → Регистрирай устройство** → изберете залата → код.
4. На лаптопа в залата: Chrome или Edge → `https://bix.bank.local/room` → въведете кода → разрешете микрофона. Лаптопът вече е „устройството на залата“; това се прави веднъж.
5. На срещата: **Започни „<тема>“** → по време „Попитай Bix“, ⭐ → **Край на срещата**. Минутите идват по имейл след няколко минути и са в портала.

Препоръки за лаптопа в залата: в средата на масата, на захранване, прозорецът отворен (Bix пречи на заспиване по време на запис). В Chrome/Edge „Инсталирай като приложение“ го отваря на цял екран.

## 6. Сигурност накратко

- Микрофонът в браузъра е включен **само по време на среща**; извън нея Bix не слуша.
- Всеки лаптоп/устройство има собствен токен; отнема се от портала (Устройства).
- Роли: администратор, член, одитор. Всяко действие (преглед, изпращане, изтриване) е в одита.
- Срок на съхранение: звукът и текстът се трият автоматично (Настройки). GDPR: изтриване на човек и гласовия му профил.
- Гласов профил само със съгласие на човека.
- Няма външни услуги извън Azure абонамента на банката и Microsoft 365.

## 7. Ако нещо не работи

| Симптом | Проверка |
|---|---|
| Лаптопът не дава микрофона | Адресът трябва да е `https://`; сертификатът да е доверен на лаптопа; разрешение за микрофона в адресната лента. |
| „Реч → текст“ е червено в Настройки | `bix server check`; ключ/роля; проксито пуска ли `*.cognitiveservices.azure.com`. |
| Azure OpenAI 404 | Името на deployment-а (`BIX_LLM_AZURE_DEPLOYMENT`), не на модела. |
| Azure OpenAI 401/403 | Ключът, или ролята „Cognitive Services OpenAI User“ за приложението. |
| Имейлът не тръгва | `bix server test-email --to ...`: съобщението казва дали relay-ят отказва подателя, сертификата или връзката. |
| Минутите са без имена | Нормално в началото: в портала → среща → „Говорещи“, с реплика и ▶, със „запомни гласа“ при съгласие. |
