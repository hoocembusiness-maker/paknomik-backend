/**
 * Catalogue taxonomy helpers for the React app.
 * Screens stay on the existing design system; these functions only shape data.
 */

export type AttributeKind = "number" | "enum" | "multi" | "text" | "bool";

export type OfferMode = "STOCK" | "SUR_MESURE";

export type AttributeSpec = {
  key: string;
  label: string;
  kind: AttributeKind;
  unit: string;
  required: boolean;
  filterable: boolean;
  options: string[];
  pivot_key?: string;
  label_en?: string;
  label_ar?: string;
};

export type AttributeValue = string | number | boolean | string[] | { min?: number; max?: number };

export type VariantDraft = {
  id?: number;
  sku: string;
  axes: Record<string, AttributeValue>;
  priceFrom: string;
  moq: string;
  available: boolean;
};

export type CatalogStrings = {
  available: string;
  custom: string;
  forSale: string;
  order: string;
  quote: string;
  sponsored: string;
  alternatives: string;
  quoteNotice: string;
  otherModels: string;
};

const COPY: Record<"fr" | "en" | "ar", CatalogStrings> = {
  fr: {
    available: "Disponible",
    custom: "Sur mesure",
    forSale: "À vendre",
    order: "Commander",
    quote: "Demande de devis",
    sponsored: "Sponsorisé",
    alternatives: "Alternatives possibles",
    quoteNotice: "Votre besoin peut aussi être couvert par…",
    otherModels: "voir les {count} autres modèles de ce fournisseur",
  },
  en: {
    available: "Available",
    custom: "Made to order",
    forSale: "For sale",
    order: "Order",
    quote: "Request a quote",
    sponsored: "Sponsored",
    alternatives: "Possible alternatives",
    quoteNotice: "Your need may also be covered by…",
    otherModels: "see the {count} other models from this supplier",
  },
  ar: {
    available: "متوفر",
    custom: "حسب الطلب",
    forSale: "للبيع",
    order: "اطلب",
    quote: "طلب عرض سعر",
    sponsored: "إعلان",
    alternatives: "بدائل ممكنة",
    quoteNotice: "يمكن تغطية احتياجك أيضاً بـ…",
    otherModels: "عرض {count} نماذج أخرى لهذا المورد",
  },
};

export type Locale = keyof typeof COPY;

export function catalogCopy(locale: Locale): CatalogStrings {
  return COPY[locale];
}

export type FormStep = "type" | "details" | "variants";

export function nextStep(step: FormStep): FormStep | null {
  if (step === "type") {
    return "details";
  }
  if (step === "details") {
    return "variants";
  }
  return null;
}

export function suggestTypes<T extends { label: string; slug: string }>(
  types: readonly T[],
  query: string,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return [...types];
  }
  return types.filter((item) => {
    return item.label.toLowerCase().includes(needle) || item.slug.toLowerCase().includes(needle);
  });
}

export function requiredAttributes(schema: readonly AttributeSpec[]): AttributeSpec[] {
  return schema.filter((item) => item.required);
}

export function filterableAttributes(schema: readonly AttributeSpec[]): AttributeSpec[] {
  return schema.filter((item) => item.filterable);
}

export function attributeLabel(spec: AttributeSpec, locale: Locale): string {
  if (locale === "en" && spec.label_en) {
    return spec.label_en;
  }
  if (locale === "ar" && spec.label_ar) {
    return spec.label_ar;
  }
  return spec.label;
}

export function offerBadge(mode: OfferMode, locale: Locale): string {
  const copy = catalogCopy(locale);
  return mode === "SUR_MESURE" ? copy.custom : copy.available;
}

export function offerFilterLabel(mode: OfferMode, locale: Locale): string {
  const copy = catalogCopy(locale);
  return mode === "SUR_MESURE" ? copy.custom : copy.forSale;
}

export type CardActions = {
  primary: "order" | "quote";
  primaryLabel: string;
  secondary: "order" | "quote" | null;
  secondaryLabel: string | null;
};

export function cardActions(mode: OfferMode, mixed: boolean, locale: Locale): CardActions {
  const copy = catalogCopy(locale);
  const primary = mode === "SUR_MESURE" ? "quote" : "order";
  const secondary = mixed ? (primary === "order" ? "quote" : "order") : null;
  return {
    primary,
    primaryLabel: primary === "order" ? copy.order : copy.quote,
    secondary,
    secondaryLabel: secondary === null ? null : secondary === "order" ? copy.order : copy.quote,
  };
}

export function otherModelsLabel(count: number, locale: Locale): string | null {
  if (count < 1) {
    return null;
  }
  return catalogCopy(locale).otherModels.replace("{count}", String(count));
}

export type AlternativeNotice = {
  text: string;
  labels: string[];
};

export function quoteAlternativeNotice(
  labels: readonly string[],
  locale: Locale,
): AlternativeNotice | null {
  if (labels.length === 0) {
    return null;
  }
  return {
    text: catalogCopy(locale).quoteNotice,
    labels: [...labels],
  };
}

export function emptyVariant(): VariantDraft {
  return { sku: "", axes: {}, priceFrom: "", moq: "", available: true };
}
