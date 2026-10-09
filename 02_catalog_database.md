# Paknomik — Catalog Database Structure (v2)

> **Core design principle:** Buyers think in two languages simultaneously.
> A café owner thinks *"I need cups"* (technical) AND *"I need cups for hot drinks"* (commercial).
> A pharma buyer thinks *"I need bottles"* (technical) AND *"they must be pharmaceutical-grade"* (commercial).
> The Paknomik catalog must speak both languages at once.

---

## The Hybrid Matrix Model

Every product on Paknomik sits at the intersection of two axes:

```
                    TECHNICAL CATEGORY
                  (what the product IS)
                         │
         Box  Bag  Bottle  Cup  Label  Tube  Wrap  Drum  Foam
          │    │     │      │     │      │     │     │     │
Pharma ───┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──
Cosmetic ─┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──  COMMERCIAL
F&B ──────┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──  SEGMENT
Shopping ─┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──  (what the
Fast Food ┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──   product is
Industry ─┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──   FOR)
E-comm ───┼────┼─────┼──────┼─────┼──────┼─────┼─────┼─────┼──
```

**A pizza box** = Technical: `Box & Carton` + Commercial: `Food Service`
**A pharma bottle** = Technical: `Bottle & Jar` + Commercial: `Pharmaceutical`
**A shopping bag** = Technical: `Bag & Pouch` + Commercial: `Shopping & Retail`
**A coffee cup** = Technical: `Cup & Container` + Commercial: `Food Service`

**Buyers can search either way:**
- "Show me everything for fast food restaurants" → commercial filter
- "Show me all paper bags" → technical filter
- "Show me paper bags for fast food" → both filters combined

---

## Part A — Technical Categories (16, flat, one level)

These are the primary product categories. No subcategories.

| # | Technical Category | Plain description |
|---|---|---|
| 1 | **Bottles & Jars** | Rigid containers with necks — plastic, glass, aluminum |
| 2 | **Rigid Boxes & Cartons** | Flat-pack and setup boxes, folding cartons, display boxes |
| 3 | **Bags & Pouches** | Flexible stand-up, flat, and multi-layer bags |
| 4 | **Cups & Tumblers** | Paper, plastic, and compostable cups and lids |
| 5 | **Shopping & Carrier Bags** | Retail bags — paper, plastic, non-woven, tote |
| 6 | **Wrapping & Film** | Gift wrap, tissue paper, shrink film, cellophane, stretch film |
| 7 | **Labels & Stickers** | Self-adhesive, shrink sleeve, in-mold, RFID |
| 8 | **Caps & Closures** | Screw caps, pumps, flip-tops, sprays, droppers |
| 9 | **Tubes** | Laminate, aluminum, plastic squeeze tubes |
| 10 | **Trays & Containers** | Open trays, clamshells, portion cups, takeaway containers |
| 11 | **Drums & Industrial** | Steel/HDPE drums, IBCs, jerry cans, pails |
| 12 | **Foam & Protective** | EPS, EPE, polyurethane inserts and sheets |
| 13 | **Paper & Corrugated** | Corrugated boxes, kraft paper, honeycomb board |
| 14 | **Cosmetic Components** | Compacts, palettes, airless pumps, mascara tubes |
| 15 | **Pharmaceutical Primary** | Blisters, vials, ampoules, sachets, capsule strips |
| 16 | **Specialty & Other** | Anything that doesn't fit above — prevents dead ends |

---

## Part B — Commercial Segments (7)

These are cross-cutting tags. A product can carry **one or two** commercial tags maximum.

| # | Commercial Segment | Who buys it | Example products |
|---|---|---|---|
| A | **Pharmaceutical** | Drug manufacturers, pharmacies, compounding labs | Blister packs, medicine bottles, vials, ampoules |
| B | **Cosmetics & Beauty** | Beauty brands, private label, salons | Airless pumps, compacts, serum bottles, tubes |
| C | **Food & Beverage** | Food producers, bottlers, canneries | PET bottles, pouches, food trays, caps |
| D | **Food Service** | Restaurants, cafés, fast food chains, caterers | Cups, pizza boxes, sandwich bags, takeaway trays |
| E | **Shopping & Retail** | Supermarkets, fashion stores, gift shops | Carrier bags, gift wrap, tissue paper, retail boxes |
| F | **Industrial & Chemical** | Factories, lubricant companies, cleaning product makers | Drums, IBCs, jerry cans, industrial pouches |
| G | **E-commerce & Logistics** | Online sellers, 3PLs, fulfilment centres | Mailer boxes, protective foam, labels, tape |

---

## Part C — New Categories in Detail

The three categories added in this version:

---

### 5. Shopping & Carrier Bags

**Commercial segment:** Shopping & Retail

**Level 1 (search filters)**
| Attribute | Options |
|---|---|
| Material | Kraft paper · Coated paper · HDPE plastic · Non-woven PP · Cotton · Jute |
| Handle type | Twisted · Flat ribbon · Die-cut · No handle · Rope |
| Size | XS (< 20cm) · S · M · L · XL · Custom |
| Print | Unprinted · 1–2 colors · Full CMYK · Custom branding |

**Level 2 (product specs)**
| Attribute | Format |
|---|---|
| Width | Number (mm) |
| Height | Number (mm) |
| Gusset | Number (mm) |
| Weight capacity | Number (kg) |
| GSM (paper bags) | Number |
| Recyclable / Biodegradable | Yes / No |

---

### 6. Wrapping & Film

**Commercial segment:** Shopping & Retail / Food Service / E-commerce

**Level 1 (search filters)**
| Attribute | Options |
|---|---|
| Type | Gift wrap paper · Tissue paper · Shrink film · Stretch film · Cellophane · Foil wrap |
| Material | Paper · OPP · PVC · PE · Aluminum foil |
| Format | Roll · Sheet · Pre-cut |
| Print | Plain · Solid color · Pattern · Custom print |

**Level 2 (product specs)**
| Attribute | Format |
|---|---|
| Roll width | Number (mm) |
| Roll length | Number (m) |
| Thickness | Number (microns or gsm) |
| Shrink ratio (shrink film) | Number (%) |
| Food-contact safe | Yes / No |

---

### 4. Cups & Tumblers *(Food Service focus)*

**Commercial segment:** Food Service

**Level 1 (search filters)**
| Attribute | Options |
|---|---|
| Material | Single-wall paper · Double-wall paper · Plastic PP · PET cold cup · Compostable PLA |
| Type | Hot cup · Cold cup · Tumbler · Espresso cup · Soup container |
| Volume | 4 oz · 6 oz · 8 oz · 12 oz · 16 oz · 20 oz · 32 oz |
| Lid compatible | Yes · No · Included |

**Level 2 (product specs)**
| Attribute | Format |
|---|---|
| Exact volume | Number (ml) |
| Top diameter | Number (mm) |
| Bottom diameter | Number (mm) |
| Height | Number (mm) |
| Insulation | Single-wall · Double-wall · Ripple · Sleeve |
| Certification | FSC · FDA food-safe · Compostable certified |

---

### 10. Trays & Containers *(Food Service focus)*

This category covers the full fast food / restaurant / café takeaway universe:

**Level 1 (search filters)**
| Attribute | Options |
|---|---|
| Type | Pizza box · Burger box · Sandwich bag · Hot dog tray · Takeaway container · Clamshell · Portion cup · Sauce cup |
| Material | Corrugated paper · Kraft board · Aluminum foil · PP · PET · Sugarcane bagasse · Compostable |
| Grease-resistant | Yes · No |
| Temperature range | Cold only · Hot up to 100°C · Oven-safe · Microwave-safe |

**Level 2 (product specs)**
| Attribute | Format |
|---|---|
| Dimensions (L × W × H) | Numbers (mm) |
| Capacity | Number (ml or g) |
| Stackable | Yes / No |
| Vented (pizza boxes) | Yes / No |
| Certification | Food-safe · Halal · Compostable · HACCP |
| Print area | Number (cm²) |

---

## Full Cross-Reference: Category × Segment Matrix

A quick reference so suppliers know where to list their products:

| Technical Category | Pharma | Cosmetic | F&B | Food Service | Shopping | Industrial | E-comm |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Bottles & Jars | ✓ | ✓ | ✓ | | | ✓ | |
| Rigid Boxes | ✓ | ✓ | ✓ | ✓ | ✓ | | ✓ |
| Bags & Pouches | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Cups & Tumblers** | | | | ✓ | | | |
| **Shopping & Carrier Bags** | | ✓ | | | ✓ | | ✓ |
| **Wrapping & Film** | | ✓ | ✓ | ✓ | ✓ | | ✓ |
| Labels & Stickers | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Caps & Closures | ✓ | ✓ | ✓ | | | ✓ | |
| Tubes | ✓ | ✓ | ✓ | | | | |
| **Trays & Containers** | | | ✓ | ✓ | | | |
| Drums & Industrial | | | ✓ | | | ✓ | |
| Foam & Protective | ✓ | ✓ | ✓ | | | ✓ | ✓ |
| Paper & Corrugated | | | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cosmetic Components | | ✓ | | | | | |
| Pharmaceutical Primary | ✓ | | | | | | |
| Specialty & Other | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Bold = new in v2**

---

## How Search Works With This Structure

### Buyer journey A — Technical search
> A supermarket buyer needs carrier bags

1. Clicks **Shopping & Carrier Bags** (technical category)
2. Filters: Material = Kraft paper, Handle = Flat ribbon
3. Sees all suppliers making kraft paper bags regardless of their country or segment

### Buyer journey B — Commercial search
> A fast food chain owner needs "everything for my restaurant"

1. Clicks **Food Service** (commercial segment)
2. Sees all products tagged Food Service: cups, pizza boxes, sandwich bags, takeaway trays, labels, bags
3. Filters by country or certifications

### Buyer journey C — Both combined
> A café in Saudi Arabia needs eco-friendly cups

1. Clicks **Food Service** → **Cups & Tumblers**
2. Filters: Material = Compostable PLA, Certification = Compostable certified, Country = Saudi Arabia
3. 3 clicks to the exact result

---

## Supplier Onboarding — What They Fill In

```
Step 1: Select TECHNICAL CATEGORY (required)
         → drives which Level 1 + Level 2 attributes appear

Step 2: Select COMMERCIAL SEGMENT(S) (required, max 2)
         → determines which industry pages the product appears on

Step 3: Fill Level 1 attributes (required to publish)
         → 3–4 dropdowns, takes < 2 minutes

Step 4: Fill Level 2 attributes (optional, improves score)
         → 4–6 fields, takes ~5 minutes

Step 5: Add price indication and MOQ (required)

Step 6: Upload photo (optional, but strongly encouraged)
```

Total minimum time to publish one product: **under 5 minutes.**
That is the friction threshold that must never be exceeded.

---

## Design Rules That Cannot Change

| Rule | Reason |
|---|---|
| Maximum 16 technical categories | More = decision paralysis for supplier |
| Maximum 7 commercial segments | More = confusion for buyer |
| Maximum 2 commercial tags per product | A product cannot be "everything for everyone" |
| No subcategories | Every extra level = abandoned form |
| Level 1 attributes are dropdowns only | No free text = clean, filterable data |
| Level 2 attributes can be numbers or short text | Precision without bureaucracy |
| Specialty & Other always exists | No supplier should feel their product doesn't fit |
