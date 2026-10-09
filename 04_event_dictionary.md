# Paknomik — Event Dictionary v0.2
> Restructuré en trois périmètres : **Acheteur (Buyer)** · **Vendeur (Seller)** · **Système (System)**
> Chaque événement suit le même schéma canonique :

```json
{
  "event_type": "string",
  "actor": {
    "user_id": 123,
    "role": "buyer | seller | visitor | system"
  },
  "context": {},
  "timestamp": "ISO-8601"
}
```

---

## 🟦 BUYER SIDE — Événements acheteur

> Actor role : `buyer` ou `visitor`
> Ces événements alimentent : **Recommandations**, **Popularité fournisseur**, **Funnel d'intention**

---

### 1️⃣ Authentication & Session

#### `buyer.login`
```json
{
  "context": {
    "method": "email | google | facebook",
    "device": "mobile | web"
  }
}
```

#### `buyer.logout`
```json
{
  "context": {
    "session_duration_seconds": 1240
  }
}
```

#### `buyer.register`
```json
{
  "context": {
    "method": "email | google",
    "company_name": "string",
    "industry_segment": "food-service | pharma | retail | ..."
  }
}
```

---

### 2️⃣ Navigation & Découverte

#### `buyer.page_view`
```json
{
  "context": {
    "page_type": "home | supplier | product | search | article | category",
    "page_id": 45
  }
}
```

#### `buyer.page_scroll`
```json
{
  "context": {
    "page_type": "supplier | product",
    "page_id": 18,
    "scroll_depth_percent": 80,
    "duration_seconds": 36
  }
}
```

#### `buyer.search`
> 🔑 Signal d'intention le plus fort
```json
{
  "context": {
    "query": "emballage biscuit",
    "filters_applied": ["prix", "quantité", "matière"],
    "category_id": 2,
    "subcategory_id": 211,
    "segment_tag": "food-beverage",
    "results_count": 42
  }
}
```

#### `buyer.filter_apply`
```json
{
  "context": {
    "filter_name": "material | size | country | certification | moq",
    "filter_value": "kraft | M | Algeria | FSC | 1000",
    "results_count": 17
  }
}
```

#### `buyer.compare`
```json
{
  "context": {
    "items_type": "supplier | product",
    "items_ids": [12, 18, 27]
  }
}
```

#### `buyer.category_browse`
```json
{
  "context": {
    "category_id": 2,
    "subcategory_id": 211,
    "source": "nav | homepage | search"
  }
}
```

---

### 3️⃣ Interaction fournisseur

#### `buyer.supplier_view`
```json
{
  "context": {
    "supplier_id": 18,
    "source": "search | direct | recommendation | compare"
  }
}
```

#### `buyer.product_view`
```json
{
  "context": {
    "supplier_id": 18,
    "product_id": 301,
    "time_on_page_seconds": 45
  }
}
```

#### `buyer.supplier_shortlist`
> Sauvegarde fournisseur dans une liste personnelle
```json
{
  "context": {
    "supplier_id": 18,
    "list_name": "Mes favoris | custom"
  }
}
```

#### `buyer.follow_supplier`
```json
{
  "context": {
    "supplier_id": 18
  }
}
```

#### `buyer.external_link_click`
> ⚠️ Sortie de Paknomik — signal de conversion perdue
```json
{
  "context": {
    "supplier_id": 18,
    "link_type": "website | facebook | instagram | whatsapp"
  }
}
```

---

### 4️⃣ Messagerie

#### `buyer.message_send`
```json
{
  "context": {
    "conversation_id": 9001,
    "message_id": 501,
    "receiver_id": 88,
    "is_first_message": true
  }
}
```

#### `buyer.message_read`
```json
{
  "context": {
    "conversation_id": 9001,
    "message_id": 502,
    "delay_seconds": 184
  }
}
```

---

### 5️⃣ Intention commerciale

#### `buyer.quotation_request`
> 🔑 Événement à la plus haute valeur business
```json
{
  "context": {
    "supplier_id": 18,
    "product_ids": [301, 302],
    "quantity": 5000,
    "unit": "units | kg | cartons",
    "delivery_country": "Algeria",
    "deadline": "2026-10-01"
  }
}
```

#### `buyer.quotation_received`
> Acheteur consulte le devis répondu par le vendeur
```json
{
  "context": {
    "quotation_id": 77,
    "supplier_id": 18,
    "delay_since_request_hours": 6
  }
}
```

#### `buyer.quotation_accepted`
```json
{
  "context": {
    "quotation_id": 77,
    "supplier_id": 18,
    "order_value_estimated": 250000,
    "currency": "DZD"
  }
}
```

#### `buyer.quotation_rejected`
```json
{
  "context": {
    "quotation_id": 77,
    "supplier_id": 18,
    "reason": "price | delay | quality | other"
  }
}
```

#### `buyer.order_created`
```json
{
  "context": {
    "supplier_id": 18,
    "order_value_estimated": 250000,
    "currency": "DZD"
  }
}
```

---

### 6️⃣ Contenu & Connaissance

#### `buyer.article_view`
```json
{
  "context": {
    "article_id": 77,
    "category": "packaging_guides | market_news | regulations"
  }
}
```

#### `buyer.guide_download`
```json
{
  "context": {
    "guide_id": 12,
    "supplier_id": 18
  }
}
```

---

## 🟧 SELLER SIDE — Événements vendeur

> Actor role : `seller`
> Ces événements alimentent : **Supplier Score**, **Responsivity Score**, **Profile Completeness**, **Search Ranking**

---

### 1️⃣ Authentication & Session

#### `seller.login`
```json
{
  "context": {
    "method": "email | google",
    "device": "mobile | web"
  }
}
```

#### `seller.logout`
```json
{
  "context": {
    "session_duration_seconds": 840
  }
}
```

---

### 2️⃣ Gestion du profil

#### `seller.profile_created`
```json
{
  "context": {
    "company_name": "string",
    "category_ids": [1, 2],
    "country": "Algeria"
  }
}
```

#### `seller.profile_updated`
> Déclenche recalcul du Profile Score
```json
{
  "context": {
    "fields_updated": ["logo", "description", "certifications", "address"],
    "profile_completeness_pct": 72
  }
}
```

#### `seller.document_uploaded`
> Certificats, agréments, tests qualité
```json
{
  "context": {
    "document_type": "iso_9001 | fsc | halal | gmp | custom",
    "supplier_id": 18
  }
}
```

---

### 3️⃣ Gestion du catalogue

#### `seller.product_added`
```json
{
  "context": {
    "product_id": 301,
    "category_id": 2,
    "subcategory_id": 211,
    "segment_tags": ["food-beverage", "food-service"],
    "has_photo": true,
    "level1_complete": true,
    "level2_complete": false
  }
}
```

#### `seller.product_updated`
```json
{
  "context": {
    "product_id": 301,
    "fields_updated": ["price_range", "moq", "photo"]
  }
}
```

#### `seller.product_deleted`
```json
{
  "context": {
    "product_id": 301,
    "reason": "discontinued | error | other"
  }
}
```

#### `seller.catalog_published`
> Premier produit publié = milestone clé
```json
{
  "context": {
    "product_count": 5,
    "categories_covered": [1, 2, 9]
  }
}
```

---

### 4️⃣ Messagerie & Réactivité

#### `seller.message_receive`
```json
{
  "context": {
    "conversation_id": 9001,
    "message_id": 501,
    "sender_id": 123,
    "is_first_contact": true
  }
}
```

#### `seller.message_read`
> ⏱️ `delay_seconds` = input du Responsivity Score
```json
{
  "context": {
    "conversation_id": 9001,
    "message_id": 501,
    "delay_seconds": 184
  }
}
```

#### `seller.message_reply`
> 🔑 Événement le plus lourd dans le Responsivity Score
```json
{
  "context": {
    "conversation_id": 9001,
    "reply_to_message_id": 501,
    "delay_seconds": 420
  }
}
```

#### `seller.message_ignored`
> Généré automatiquement si pas de réponse après 48h
```json
{
  "context": {
    "conversation_id": 9001,
    "message_id": 501,
    "hours_elapsed": 48
  }
}
```

---

### 5️⃣ Gestion des devis

#### `seller.quotation_received`
> Notification que l'acheteur a soumis une demande
```json
{
  "context": {
    "quotation_id": 77,
    "buyer_id": 123,
    "product_ids": [301, 302],
    "quantity": 5000
  }
}
```

#### `seller.quotation_replied`
> ⏱️ Délai = input critique du Responsivity Score
```json
{
  "context": {
    "quotation_id": 77,
    "delay_hours": 5.5,
    "price_proposed": 180000,
    "currency": "DZD"
  }
}
```

#### `seller.quotation_ignored`
> Généré automatiquement si pas de réponse après 72h
> ⚠️ Pénalise le Responsivity Score
```json
{
  "context": {
    "quotation_id": 77,
    "hours_elapsed": 72
  }
}
```

---

### 6️⃣ Analytics vendeur

#### `seller.dashboard_view`
> Le fournisseur ouvre son tableau de bord principal
```json
{
  "context": {
    "supplier_id": 12,
    "year": 2026,
    "month": 8
  }
}
```

#### `seller.market_analysis_view`
> Le fournisseur consulte l'analyse de marché
```json
{
  "context": {
    "supplier_id": 12,
    "year": 2026,
    "month": 8
  }
}
```

#### `seller.analytics_view`
> Le vendeur consulte ses propres statistiques
```json
{
  "context": {
    "page": "dashboard | visitors | messages | score",
    "period": "7d | 30d | 90d"
  }
}
```

#### `seller.profile_view_received`
> Le vendeur voit qu'un acheteur a visité son profil
```json
{
  "context": {
    "visitor_count": 12,
    "period": "last_7_days"
  }
}
```

---

## ⚙️ SYSTEM — Événements système

> Actor role : `system`
> Ces événements sont générés automatiquement par la plateforme, non par un utilisateur humain.
> Ils alimentent : **Scores**, **Classements**, **Alertes**

| Event | Déclencheur | Alimentation |
|---|---|---|
| `system.appear_in_search` | Requête acheteur | Popularité fournisseur, position |
| `system.score_updated` | Calcul périodique | Score global affiché sur profil |
| `system.inactivity_warning` | 14 jours sans connexion | Alerte email au vendeur |
| `system.inactivity_penalty` | 30 jours sans connexion | −10 pts/semaine score activité |
| `system.badge_awarded` | Score franchit un seuil | Tier Bronze/Silver/Gold/Platinum |
| `system.badge_revoked` | Score descend sous un seuil | Rétrogradation de tier |
| `system.quotation_expired` | 72h sans réponse vendeur | Pénalité −40 pts responsivité |
| `system.message_sla_breach` | 48h sans réponse message | Pénalité Responsivity Score |

#### `system.appear_in_search`
```json
{
  "actor": { "user_id": null, "role": "system" },
  "context": {
    "supplier_id": 18,
    "search_query": "emballage parfum",
    "position": 3,
    "triggered_by_buyer_id": 123
  }
}
```

#### `system.score_updated`
```json
{
  "actor": { "user_id": null, "role": "system" },
  "context": {
    "supplier_id": 18,
    "score_total": 742,
    "score_profile": 210,
    "score_activity": 290,
    "score_responsivity": 242,
    "tier": "Gold",
    "previous_tier": "Silver"
  }
}
```

#### `system.quotation_expired`
```json
{
  "actor": { "user_id": null, "role": "system" },
  "context": {
    "quotation_id": 77,
    "supplier_id": 18,
    "buyer_id": 123,
    "penalty_points": -40
  }
}
```

---

## 📊 Récapitulatif — Qui génère quoi

```
ACHETEUR (Buyer)          VENDEUR (Seller)          SYSTÈME
─────────────────         ──────────────────         ────────────────
buyer.login               seller.login               system.appear_in_search
buyer.register            seller.profile_created     system.score_updated
buyer.search              seller.profile_updated     system.inactivity_warning
buyer.filter_apply        seller.document_uploaded   system.inactivity_penalty
buyer.category_browse     seller.product_added       system.badge_awarded
buyer.page_view           seller.product_updated     system.badge_revoked
buyer.page_scroll         seller.product_deleted     system.quotation_expired
buyer.compare             seller.catalog_published   system.message_sla_breach
buyer.supplier_view       seller.message_receive
buyer.product_view        seller.message_read
buyer.supplier_shortlist  seller.message_reply
buyer.follow_supplier     seller.message_ignored
buyer.external_link_click seller.quotation_received
buyer.message_send        seller.quotation_replied
buyer.message_read        seller.quotation_ignored
buyer.quotation_request   seller.dashboard_view
buyer.quotation_received  seller.market_analysis_view
buyer.quotation_accepted  seller.analytics_view
buyer.quotation_rejected  seller.profile_view_received
buyer.order_created
buyer.article_view
buyer.guide_download
```

**Total : 21 événements acheteur · 19 événements vendeur · 8 événements système = 48 événements**

---

## 🔗 Flux clés à tracer en priorité

```
Flux 1 — Conversion acheteur
buyer.search → buyer.supplier_view → buyer.product_view → buyer.quotation_request
                                                              ↓
Flux 2 — Réactivité vendeur                     seller.quotation_received
seller.quotation_received → seller.quotation_replied → buyer.quotation_received → buyer.quotation_accepted
                         ↘ (72h sans réponse) → system.quotation_expired → system.score_updated

Flux 3 — Fuite de trafic
buyer.supplier_view → buyer.external_link_click  ← 🚨 Paknomik perd la commission
```
