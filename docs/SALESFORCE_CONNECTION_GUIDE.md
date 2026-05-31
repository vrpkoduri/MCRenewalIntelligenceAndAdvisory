# Salesforce Connection Guide — Lakeflow Connect → bronze

How to connect Morgan Cash's Salesforce org to Databricks so Lakeflow Connect can land
the funded book in `mca_mri.bronze.*` via managed CDC. Sprint 0 ingestion depends on
this (CLAUDE.md §4; SPRINT_0 in-scope item 2).

> **Approval gate (GENERAL_INSTRUCTIONS Rule 5).** Every step that *creates a cloud
> resource* — the secret scope, the Unity Catalog connection, the ingestion pipeline — is
> done **only on your explicit go-ahead**. This document is the walkthrough; nothing here
> runs until you say so. The Salesforce-side steps (Connected App) are done by you / your
> Salesforce admin, since they require org admin rights I don't have.

---

## 0. What you'll end up with

```
Salesforce org
  └─ Connected App (OAuth client id + secret)        ← you create (SF admin)
        │  credentials
        ▼
Databricks secret scope  mri-salesforce              ← created on your approval
        │  referenced by scope/key (never plaintext)
        ▼
Unity Catalog connection  mri_salesforce             ← created on your approval
        │
        ▼
Lakeflow Connect ingestion pipeline → mca_mri.bronze.*  ← created on your approval
```

Three identities are involved, keep them distinct:
- **Salesforce Connected App** — the OAuth client (lives in Salesforce).
- **Databricks secret scope** — where the OAuth credentials are stored (lives in Databricks).
- **UC connection** — the governed object Lakeflow reads the secrets through (lives in Unity Catalog).

---

## 1. Choose the auth method

Lakeflow Connect's Salesforce connector supports **OAuth 2.0**. Two grant types are common:

| Grant type | When to use | What you provide |
|---|---|---|
| **OAuth (web / authorization-code)** | Interactive, ties ingestion to a named integration user | client id, client secret, and an authorize step in the Databricks UI |
| **JWT bearer (server-to-server)** | Fully headless, no interactive consent, uses a certificate | client id, username, private key, no client secret |

**Recommendation for S0:** start with the **authorization-code (web) flow** behind a
**dedicated integration user** (see §2.1). It's the path the Databricks "Add connection"
UI guides you through, and it avoids managing a certificate. We can migrate to JWT later
if we want a non-interactive refresh — that's a swap of the connection, not the pipeline.

> **Decision for you (D-004):** web/auth-code vs JWT. Default = web/auth-code. Logged in
> [`DECISIONS.md`](DECISIONS.md).

---

## 2. Salesforce side (done by your SF admin)

### 2.1 Create a dedicated integration user (best practice)

Don't bind ingestion to a person's login. Create a service identity, e.g.
`mri-integration@morgancash.com`, with:
- A **permission set** granting **read-only** API access to exactly the objects we
  ingest (principle of least privilege):
  - `Opportunity`
  - `Account` (parent merchant — confirm in G1 / D-002)
  - `Offer__c` and the Selected Offer (confirm API name in G1)
  - `OpportunityFieldHistory`
- **"API Enabled"** system permission.
- **No** write/delete permissions. Ingestion is read-only.

Read-only matters: a credential that can only read can't damage the system of record even
if it leaks.

### 2.2 Create the Connected App (OAuth)

In Salesforce **Setup → App Manager → New Connected App** (or **External Client Apps** on
newer orgs):

1. **Enable OAuth Settings.**
2. **Callback URL** — Databricks gives you the exact value on the "Add connection" screen
   (typically `https://<your-workspace-host>/login/oauth/salesforce.html` or the value
   shown in the wizard). Paste exactly what Databricks displays.
3. **OAuth scopes** — add at minimum:
   - `Manage user data via APIs (api)`
   - `Perform requests at any time (refresh_token, offline_access)`
4. Save. Salesforce generates a **Consumer Key** (= OAuth client id) and **Consumer
   Secret** (= OAuth client secret). Allow the few minutes Salesforce asks for the app to
   propagate.
5. (Recommended) In the Connected App's **policies**, set **Permitted Users =
   "Admin approved users are pre-authorized"** and assign the integration user's
   permission set, so only the service identity can use this app.

> **This org uses External Client Apps, not classic Connected Apps.** The app
> "Databricks Ingestion" was created as an External Client App (App Manager only offered
> "New External Client App"). Two consequences:
> - **IP Relaxation:** set the app's OAuth Policies → **IP Relaxation = "Relax IP
>   restrictions"** (or whitelist Databricks egress IPs). Default "Enforce IP
>   restrictions" can silently break server-side token refresh from Databricks/Azure.
> - **Pre-authorization (admin-approved users):** External Client Apps do **not** appear
>   in the classic Permission Set → "Assigned Connected Apps" picklist, so the
>   admin-approved path can't be completed there. For now the app is left at **"All users
>   may self-authorize."** **FOLLOW-UP (pre-go-live, FU-001):** lock pre-authorization to
>   the integration user via the External-Client-App-native mechanism before production.

### 2.3 Capture these values (hand to me securely — NOT in chat/repo)

| Value | Salesforce label | Used as |
|---|---|---|
| Consumer Key | Connected App | OAuth `client_id` |
| Consumer Secret | Connected App | OAuth `client_secret` |
| My Domain / instance URL | Setup → My Domain | Salesforce login host |
| Integration username | the service user | login / token subject |

> **Never paste the secret into this chat, a commit, a notebook cell, or shell history.**
> Put it straight into the secret scope (§3) via a method that doesn't echo it.

---

## 3. Where the credentials live — the UC connection itself (no separate secret scope)

> **Revised 2026-05-29 (supersedes the earlier secret-scope plan; D-005 closed as N/A).**
> The Salesforce OAuth flow is **interactive** ("Sign in with Salesforce" in the browser),
> so the connection is created in the **Databricks UI**, not the CLI. In that flow you
> paste the Consumer Key/Secret **directly into the Databricks connection form in your
> browser**, and the **Unity Catalog connection securely stores the OAuth credentials +
> refresh token** as a governed, lineage-tracked securable.
>
> Therefore **no separate `mri-salesforce` secret scope is needed** — the UC connection
> *is* the credential store. One fewer moving part, and the secret never passes through
> chat, code, or shell history (it goes browser → UC directly). This follows the
> best-practice / least-moving-parts guardrail (GENERAL_INSTRUCTIONS Rule 6).
>
> A secret scope would only be reintroduced if we later switch to a **headless JWT**
> connection (D-004 alternative), which references a key by secret reference.

**Credentials to have in hand (hold them; the secret goes only into the browser form):**

| Value | Salesforce label | Entered into |
|---|---|---|
| Consumer Key | External Client App → Consumer Key & Secret | Databricks connection form (`Client ID`) |
| Consumer Secret | External Client App → Consumer Key & Secret | Databricks connection form (`Client secret`) |
| Instance URL | Setup → My Domain (`mcabrokerage.my.salesforce.com`) | Databricks connection form (`Salesforce URL`) |

---

## 4. Unity Catalog connection (created on your approval)

The cleanest path is the **Databricks UI**, because the OAuth authorize step is
interactive:

**Catalog → External Data → Connections → Create connection**
1. Connection type: **Salesforce**.
2. Auth: **OAuth** → enter client id / client secret (or point at the secret scope if the
   UI offers it), and the Salesforce instance host.
3. Click **Sign in with Salesforce** → log in as the **integration user** → approve. This
   exchanges the one-time consent for a refresh token that UC stores.
4. Name the connection **`mri_salesforce`** and create it.

CLI equivalent (for reference; the UI is recommended for the OAuth handshake):
```bash
databricks connections list                 # confirm it landed
databricks connections get mri_salesforce
```

The connection is a UC securable — it's governed and lineage-tracked like everything else
under `mca_mri`.

---

## 5. Lakeflow Connect ingestion pipeline (created on your approval)

Once the connection exists **and G1 confirms the object/field API names (D-002)**, the
ingestion pipeline is defined as code in
[`resources/ingestion_pipeline.yml`](../resources/ingestion_pipeline.yml) (currently a
placeholder) and deployed via the DAB.

The pipeline will:
- Use the **`mri_salesforce`** UC connection.
- Ingest the four objects (§2.1) into `mca_mri.bronze.*`, one bronze table each, as
  **managed CDC** — Lakeflow handles incremental change capture; we do **not** hand-roll
  Bulk API (CLAUDE.md §4).
- Leave bronze **raw and immutable**; all cleaning/typing happens bronze→silver
  (RUNBOOK conventions).

Deploy is itself an approval-gated action:
```bash
databricks bundle validate -t dev     # safe, read-only — fine to run anytime
# databricks bundle deploy -t dev      # gated on your approval (Rule 5)
```

---

## 6. Verify the connection works (read-only checks)

After the connection exists, before building the full pipeline, sanity-check it:
- In the UI, the connection's **"Test connection"** should succeed.
- Browse the source objects through the connection to confirm the integration user can
  see `Opportunity` etc.
- Confirm the row counts roughly match the expected funded-book size (this becomes the
  formal **reconciliation test** once silver lands — SPRINT_0 DoD).

---

## 7. Security checklist

- [ ] Integration user is **read-only**, least-privilege, not a person's account.
- [ ] Connected App secret never entered into chat, commits, notebooks, or shell history.
- [ ] Credentials live **only** in the `mri-salesforce` secret scope (or Key Vault).
- [ ] Repo/bundle reference secrets by `{{secrets/...}}`, never by value.
- [ ] Temp files holding the secret were shredded after loading.
- [ ] Connection + pipeline created only after explicit approval.
- [ ] A credential-rotation owner/cadence is agreed (who rotates the Connected App secret,
      and when).

---

## 8. What I need from you to proceed

1. **D-004** — auth method (default: web/auth-code OAuth).
2. **D-005** — secret backend (default: Databricks-backed `mri-salesforce`).
3. The captured values from §2.3, delivered securely (not in chat).
4. **G1 / D-002** — confirmed Salesforce object + field API names.
5. Explicit **approval** to create: the secret scope, the `mri_salesforce` UC connection,
   and the Lakeflow ingestion pipeline.

I'll do steps 3–5 of this guide (Databricks side) with you once you give the word; the
Salesforce-side steps (§2) need your admin.
